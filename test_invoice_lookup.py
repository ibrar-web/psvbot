"""
Manual tester for the invoice_history_lookup task — runs the exact same
run_invoice_history_lookup_flow() the queue worker calls, without the queue,
Mongo, or result callback.

Usage:
    python test_invoice_lookup.py <invoice_id>
    python test_invoice_lookup.py            # falls back to INVOICE_ID below / TEST_INVOICE_ID env

Set credentials in .env or as environment variables:
    PRINTSMITH_URL
    PRINTSMITH_USERNAME
    PRINTSMITH_PASSWORD
    PRINTSMITH_COMPANY      (optional — derived from URL hostname if omitted)
    PRINTSMITH_HEADLESS     (default: false here so you can watch the browser)
    PRINTSMITH_DEBUG        (default: true — prints each step/field as it's scraped)
    PRINTSMITH_TIMEOUT_SECONDS (default: 120)

The full result is printed and also saved to invoice_lookup_<invoice_id>.json
(ignored by git via *.json).
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

# Must be set before app config is imported — config reads env at import time.
os.environ.setdefault("PRINTSMITH_HEADLESS", "false")
os.environ.setdefault("PRINTSMITH_DEBUG", "true")

# ---------------------------------------------------------------------------
# Test payload — same shape the queue service passes to the handler.
# tenant_credentials takes priority over the .env credentials; leave values
# empty to use .env.
# ---------------------------------------------------------------------------
INVOICE_ID = os.getenv("TEST_INVOICE_ID", "")

TENANT_CREDENTIALS: Dict[str, str] = {
    "printsmith_url": "",
    "printsmith_username": "",
    "printsmith_password": "",
    "printsmith_company": "",
}


# ---------------------------------------------------------------------------
# Credential helper — mirrors queue_service._normalize_runtime_credentials
# ---------------------------------------------------------------------------

def _normalize_runtime_credentials(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    payload = data or {}
    printsmith_url = str(
        payload.get("printsmith_url") or payload.get("url") or os.getenv("PRINTSMITH_URL", "")
    ).strip()
    username = str(
        payload.get("printsmith_username")
        or payload.get("username")
        or os.getenv("PRINTSMITH_USERNAME", "")
    ).strip()
    password = str(
        payload.get("printsmith_password")
        or payload.get("password")
        or os.getenv("PRINTSMITH_PASSWORD", "")
    ).strip()
    company = str(
        payload.get("printsmith_company")
        or payload.get("company")
        or os.getenv("PRINTSMITH_COMPANY", "")
    ).strip()

    if not company and printsmith_url:
        hostname = urlparse(printsmith_url).hostname or ""
        company = hostname.split(".", 1)[0].strip()

    return {
        "printsmith_url": printsmith_url,
        "username": username,
        "password": password,
        "company": company,
    }


# ---------------------------------------------------------------------------
# Result summary
# ---------------------------------------------------------------------------

def _print_job(job: Dict[str, Any], indent: str = "    ") -> None:
    print(f"{indent}job_name     : {job.get('job_name')}")
    print(f"{indent}job_method   : {job.get('job_method')}")
    print(f"{indent}description  : {job.get('description')}")
    print(f"{indent}stock_search : {job.get('stock_search')!r}")
    print(f"{indent}size / sides : {job.get('size')!r} / {job.get('sides')!r}")
    print(f"{indent}quantity     : {job.get('quantity')}   price: {job.get('price')}")
    if job.get("vendor_name"):
        print(f"{indent}vendor_name  : {job.get('vendor_name')}   agent_total: {job.get('agent_total')}")
    charges = job.get("job_charges") or []
    print(f"{indent}job_charges  : {len(charges)}")
    for charge in charges:
        print(
            f"{indent}  - {charge.get('charge_name')} | qty={charge.get('quantity')} "
            f"| price={charge.get('charge_price')}"
        )
    for other in job.get("other_charges") or []:
        print(
            f"{indent}other_charge : {other.get('charge_name')} | qty={other.get('quantity')} "
            f"| price={other.get('charge_price')} | {len(other.get('job_charges') or [])} charge line(s)"
        )
    for index, part in enumerate(job.get("parts") or [], start=1):
        print(f"{indent}part {index}:")
        _print_job(part, indent=indent + "    ")


def _print_summary(result: Dict[str, Any]) -> None:
    print("\n--- Result ---")
    print(f"  status           : {result.get('status')}")
    print(f"  message          : {result.get('message')}")
    print(f"  step             : {result.get('step')}")
    print(f"  logout_succeeded : {result.get('logout_succeeded')} (error={result.get('logout_error')})")
    if result.get("status") != "success":
        return

    job_items = result.get("job_items") or []
    print(f"\n  job_items ({len(job_items)}):")
    for index, job in enumerate(job_items, start=1):
        print(f"\n  [{index}]")
        _print_job(job)

    direct_charges = result.get("direct_charges") or []
    print(f"\n  direct_charges ({len(direct_charges)}):")
    for charge in direct_charges:
        print(f"    - {charge.get('charge_name')} | price={charge.get('charge_price')}")

    empty_stock = [
        job.get("job_name") or job.get("description")
        for job in job_items
        if not job.get("stock_search") and (job.get("job_method") or "").lower() not in ("multi-part",)
        and not (job.get("job_method") or "").lower().startswith("sublet")
    ]
    if empty_stock:
        print(f"\n  [WARN] stock came back empty for: {empty_stock}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def test_invoice_lookup(invoice_id: str) -> bool:
    from app.v1.modules.bot.etimate_history.invoice_history_lookup import (
        run_invoice_history_lookup_flow,
    )

    runtime_credentials = _normalize_runtime_credentials(TENANT_CREDENTIALS)
    task_payload = {"invoice_id": invoice_id}

    print("\n[TEST] Invoice History Lookup")
    print(f"  URL        : {runtime_credentials['printsmith_url']}")
    print(f"  Username   : {runtime_credentials['username']}")
    print(f"  Company    : {runtime_credentials['company']}")
    print(f"  invoice_id : {invoice_id}")
    print(f"  Headless   : {os.getenv('PRINTSMITH_HEADLESS')}")
    print()

    started_at = time.monotonic()
    result = run_invoice_history_lookup_flow(
        tenant_credentials=runtime_credentials,
        task_payload=task_payload,
    )
    elapsed = time.monotonic() - started_at

    _print_summary(result)

    output_path = Path(f"invoice_lookup_{invoice_id}.json")
    output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\n  [INFO] Took {elapsed:.1f}s — full result saved to {output_path.resolve()}")

    return result.get("status") == "success"


if __name__ == "__main__":
    invoice_id = (sys.argv[1] if len(sys.argv) > 1 else INVOICE_ID).strip()
    if not invoice_id:
        print("Usage: python test_invoice_lookup.py <invoice_id>  (or set TEST_INVOICE_ID)")
        sys.exit(2)
    passed = test_invoice_lookup(invoice_id)
    sys.exit(0 if passed else 1)
