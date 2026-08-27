# Estimate History — Bulk CSV Export (Case 4a)

## Context

The bot currently supports exactly one operation — creating a PrintSmith estimate — and that logic, browser/login lifecycle, locking, and result callback are all fused together in `estimate_service.py` / `queue_service.py`. We're extending the bot to support additional PrintSmith operations, starting with **"Estimate History"**. That case itself splits into two sub-cases:

- **4a — bulk export**: open the Estimate History grid and download the full CSV (this round).
- **4b — single-record lookup**: search the same grid by estimate #/account/etc. and open one matching record's detail page (deferred to a future round; not touched now).

This round implements 4a end-to-end and lays the dispatch/locking groundwork so 4b (and any future case) slots in later without re-touching the plumbing built here. The existing create-estimate flow must keep working exactly as it does today — every change to existing files is a mechanical, behavior-preserving relocation, not a rewrite.

Scaffolding already in place (created by the user): `app/v1/modules/bot/etimate_history/elements/estimate_history_button.html` (the quick-access menu card, `name="menuitem_14"`, text "Estimate History", plus the grid's toolbar including `name="downloadAsCSVButton"`) and `.../elements/estimatesearch.html` (the grid's column/filter header row — reserved for 4b). Confirmed with the user: the page's loading state uses the same generic spinner (`.spinner-overlay`/`.ng-progress`) already handled by `BasePage.wait_for_spinner_to_disappear()` — no new loader logic needed. Confirmed scope: 4a downloads the CSV as-is (no filters applied first); filters are 4b's concern.

## New files

**`app/v1/modules/bot/task_types.py`**
`TaskType(str, Enum)` with `CREATE_ESTIMATE = "create_estimate"`, `ESTIMATE_HISTORY_EXPORT = "estimate_history_export"`, and `ESTIMATE_HISTORY_LOOKUP = "estimate_history_lookup"` (reserved now per the user's "keep configuration for both" instruction, but not registered to a handler yet — a task arriving with this type today will fail cleanly as "unregistered task_type" rather than silently doing the wrong thing).

**`app/v1/modules/bot/session_runner.py`**
Pure relocation (zero logic change) of the generic, non-estimate-specific helpers currently in `estimate_service.py`: `_build_quick_access_url`, `_is_logged_in_url`, `_safe_page_url`, `_stop_page_load`, `_wait_for_app_to_settle`, `_load_page`, `_complete_login_if_needed`, `_recover_session_from_home`, `_navigate_with_recovery`, `_login`, `_ensure_browser_and_login`, `_logout_if_possible`, `_ensure_within_timeout`, `_cleanup_browser`. Same function names/signatures/bodies — this is the "reusable login" module both the existing and new flows import from.

**`app/v1/modules/bot/etimate_history/estimate_history_page.py`**
`EstimateHistoryPage(BasePage)`:
- `open_from_quick_access()` — click the `menuitem_14` quick-access card (same JS-click-with-retry pattern as `EstimatePage.click_create_estimate_quick_access`), then `wait_for_spinner_to_disappear()`, then `wait_for_visible()` on the Download-as-CSV button as the "grid finished rendering" signal (no known URL fragment for this screen, so we confirm readiness via that element instead of a URL check).
- `download_csv() -> Path` — click `downloadAsCSVButton` inside `page.expect_download()`, save to a temp dir, return the path. Same mechanism as `EstimatedSummaryTab._download_headless`. Headless-only for this round (matches the default `HEADLESS=true` config); headed-mode support can follow the existing `_download_headed` pattern later if needed.

**`app/v1/modules/bot/etimate_history/estimate_history_export.py`**
`run_estimate_history_export_flow(tenant_credentials, task_payload) -> dict`:
- Validates `username`/`password`/`base_url` (same checks as `run_estimate_flow`), opens `sync_playwright()`, creates the browser/page via `driver.create_browser_page`, logs in via `session_runner` helpers.
- `EstimateHistoryPage(page).open_from_quick_access()` → `download_csv()`.
- Uploads the CSV directly via the already-generic `app.v1.common.storage_service.build_storage_key` / `upload_bytes_to_storage` (no new upload abstraction needed — those functions are already shape-agnostic), under a new `PRINTSMITH_ESTIMATE_HISTORY_STORAGE_ROOT` root (config default `"estimate-history"`, mirroring `QUOTE_SUMMARY_STORAGE_ROOT`), `content_type="text/csv"`, keyed by `{tenant_id-or-"adhoc"}/{queue_id}`.
- Logs out (`session_runner._logout_if_possible`) and tears down the browser (`session_runner._cleanup_browser`), deletes the local temp CSV.
- Returns `{"status": "success", "history_file_name": ..., "history_file_storage_key": ..., "history_file_url": ...}`, or an `{"status": "error", "message": ...}` dict on `InvalidLoginCredentialsError` / `PlaywrightTimeoutError` / generic `Exception` (same error-dict contract as `run_estimate_flow`, simpler since there are fewer steps that can fail).

## Modified files (mechanical, behavior-preserving)

**`app/v1/modules/bot/config.py`** — add `PRINTSMITH_ESTIMATE_HISTORY_STORAGE_ROOT` env var (default `"estimate-history"`), same pattern as `QUOTE_SUMMARY_STORAGE_ROOT`.

**`app/v1/modules/bot/services/estimate_service.py`** — delete the relocated helper function bodies, add one import from `session_runner`. `run_estimate_flow` and every create-estimate-specific helper (`_upload_summary_file`, `_open_existing_estimate`, etc.) stay exactly as they are.

**`app/v1/modules/bot/services/queue_service.py`**:
1. Add `TASK_HANDLERS = {TaskType.CREATE_ESTIMATE.value: run_estimate_flow, TaskType.ESTIMATE_HISTORY_EXPORT.value: run_estimate_history_export_flow}`.
2. In `_process_task`, after resolving `source_payload`: read `task_type = str(task_payload.get("task_type") or TaskType.CREATE_ESTIMATE.value)`, look it up in `TASK_HANDLERS`. Unknown type → fail the task immediately (terminal, `_mark_task_failed_or_retry(..., retry_allowed=False)` + result callback) with a clear "Unknown task_type" message — same failure path already used for other terminal errors.
3. Branch on `task_type == CREATE_ESTIMATE`: **unchanged existing path** (`_build_quote_record_from_task_payload`, `_extract_psv_credentials(source_payload, quote_record)`, etc.). Else: build `runtime_credentials` from `source_payload` directly (`_extract_psv_credentials(source_payload, {})` → `_normalize_runtime_credentials` / `_build_runtime_credentials` fallback — same existing helper functions), validate, call the handler with `(runtime_credentials, source_payload)`.
4. **Lock key prefixing** — in `_extract_lock_components`/`_lock_key_values` (called via `_task_lock_fields`, which already receives `payload` everywhere it's used), prefix each computed lock key with the task type read from `payload.get("task_type", TaskType.CREATE_ESTIMATE.value)`, e.g. `estimate:<id>` → `<task_type>:estimate:<id>`. This is what makes a `create_estimate` job and an `estimate_history_export`/`lookup` job on the same `estimate_id` run concurrently instead of blocking each other, while two jobs of the *same* type on the same record still serialize. A bulk export with no `chat_id`/`quote_id`/`estimate_id` in its payload naturally gets an empty lock-key list (no lock acquired) — matches the earlier decision that 4a needs no lock unless a specific record id is involved.
5. `_call_record_result` — add `task_type` and the new `history_file_name` / `history_file_storage_key` / `history_file_url` fields (via `result.get(...)`, default `None`) to the callback payload, alongside the existing fields (which stay untouched and simply come through as `None` for non-create-estimate results, same as today).

## Explicitly out of scope this round

- 4b (single-record lookup / `ESTIMATE_HISTORY_LOOKUP`) — enum value reserved, no handler registered, no page-object filter methods built. Uses `estimatesearch.html`'s captured filter fields (`filter_invoiceNumber_input`, account combobox, status dropdown, date-range calendars) when we get to it.
- Applying grid filters before the 4a download — per the user's description, 4a downloads the CSV as currently displayed with no filtering step.
- Renaming the existing `etimate_history/` folder (typo aside) — new files go inside it as-is to avoid disturbing what's already there.

## Verification

1. `python -m py_compile` (or just importing the modules) on all new/edited files to catch syntax errors before touching the live queue.
2. Manual headed dry run first: set `PRINTSMITH_HEADLESS=false`, call `run_estimate_history_export_flow` directly against real/staging PrintSmith credentials in a throwaway script, and visually confirm the quick-access click lands on the Estimate History grid and the CSV downloads — the click/selector logic can only be verified against the live PrintSmith UI.
3. Add a test payload entry (task_type `estimate_history_export`) to `testdata.json` and trigger it via the existing `GET /execute-test-task?id=<key>` endpoint; confirm in Mongo the `tasks` document goes `pending → processing → done`, the CSV lands in the storage bucket under `estimate-history/...`, and (if a callback URL is configured) the result payload carries `history_file_*` fields.
4. Enqueue a `create_estimate` task and an `estimate_history_export` task referencing the same `estimate_id` at the same time; confirm both proceed without a lock conflict (inspect `task_locks` collection — keys should differ by task-type prefix).
