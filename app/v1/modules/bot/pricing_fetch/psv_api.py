import json
import logging
from typing import Any, Dict, List

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

REST_BASE_PATH = "/PrintSmith/rest/html5/"
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT_MS = 30_000
# Many failures usually mean PSV is rejecting us (session, load), so the
# remaining requests are skipped instead of sent.
STOP_AFTER_FAILURES = 20

# Runs inside the logged-in PSV page so the JSESSIONID cookie is sent.
# PSV's web app adds the starttime/messageid headers to every REST call.
FETCH_SCRIPT = """
async ([requests, concurrency, maxAttempts, timeoutMs, stopAfter]) => {
    const headers = () => ({
        starttime: String(Date.now()),
        messageid: crypto.randomUUID(),
        accept: 'application/json, text/plain, */*',
        'content-type': 'application/json',
    });
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const attempt = async ({url, method, body}) => {
        try {
            const response = await fetch(url, {
                method, body, headers: headers(),
                signal: AbortSignal.timeout(timeoutMs),
            });
            if (!response.ok) {
                return {ok: false, reason: `HTTP ${response.status}`};
            }
            return {ok: true, data: await response.json()};
        } catch (error) {
            const timedOut = error.name === 'TimeoutError';
            return {ok: false, reason: timedOut ? 'timeout' : error.message};
        }
    };
    const send = async request => {
        let result;
        for (let n = 1; n <= maxAttempts; n++) {
            result = await attempt(request);
            if (result.ok) {
                return result;
            }
            if (n < maxAttempts) {
                await wait(1000 * n);
            }
        }
        return result;
    };
    const results = requests.map(() => ({ok: false, reason: 'skipped'}));
    let next = 0;
    let failures = 0;
    const worker = async () => {
        while (next < requests.length && failures < stopAfter) {
            const index = next++;
            results[index] = await send(requests[index]);
            if (!results[index].ok) {
                failures++;
            }
        }
    };
    await Promise.all(Array.from({length: concurrency}, worker));
    return results;
}
"""


def index_jsog_objects(data: Any) -> Dict[str, Dict[str, Any]]:
    # PSV sends each repeated object once with a generatorId and replaces
    # later copies with {"@ref": generatorId}.
    objects: Dict[str, Dict[str, Any]] = {}
    pending = [data]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "generatorId" in item:
                objects[item["generatorId"]] = item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return objects


def resolve_jsog_ref(
    item: Any, objects: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    if "@ref" in item:
        return objects.get(item["@ref"], {})
    return item


class PsvApi:
    def __init__(self, page: Page) -> None:
        self._page = page

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        request = {
            "url": REST_BASE_PATH + path,
            "method": "POST",
            "body": json.dumps(body),
        }
        result = self._send([request], concurrency=1)[0]
        if not result["ok"]:
            raise RuntimeError(f"PSV API {path} failed: {result['reason']}")
        return result["data"]

    def get_many(
        self, paths: List[str], concurrency: int
    ) -> List[Dict[str, Any]]:
        # Each result is {"ok": True, "data": ...} or
        # {"ok": False, "reason": ...}, in the same order as paths.
        requests = [
            {"url": REST_BASE_PATH + path, "method": "GET"} for path in paths
        ]
        return self._send(requests, concurrency)

    def _send(
        self, requests: List[Dict[str, Any]], concurrency: int
    ) -> List[Dict[str, Any]]:
        logger.info("Sending %s PSV API requests", len(requests))
        results = self._page.evaluate(
            FETCH_SCRIPT,
            [
                requests,
                concurrency,
                MAX_ATTEMPTS,
                REQUEST_TIMEOUT_MS,
                STOP_AFTER_FAILURES,
            ],
        )
        failed_count = sum(not result["ok"] for result in results)
        if failed_count:
            logger.warning(
                "%s of %s PSV API requests failed or were skipped",
                failed_count,
                len(requests),
            )
        return results
