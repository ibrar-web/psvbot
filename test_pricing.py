"""Run a pricing fetch task the same way the queue worker does.

The task payload comes from testdata.json, and the result callback is sent
to a local capture server that prints exactly what the backend would get.

Usage:
    python test_pricing.py --flow stock
    python test_pricing.py --flow charges --id 1 --headless
    python test_pricing.py --flow both
"""

import argparse
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

TESTDATA_PATH = Path("app/v1/modules/bot/testdata.json")
FLOW_TASK_TYPES = {
    "stock": "stock_material_pricing",
    "charges": "job_charges_pricing",
}


class CaptureHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(body)
        result_path = Path(f"{payload.get('task_type')}_result.txt")
        result_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(
            f"\nBackend received total_rows={payload.get('total_rows')}, "
            f"failed_rows={payload.get('failed_rows')}, "
            f"path={self.path}, "
            f"saved to {result_path}"
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args) -> None:
        pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test pricing tasks")
    parser.add_argument(
        "--flow", choices=[*FLOW_TASK_TYPES, "both"], default="stock"
    )
    parser.add_argument("--id", default="1", help="testdata.json payload id")
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def _start_capture_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/pricing-result"


def _build_envelope(task_type: str, payload_id: str, result_url: str):
    with TESTDATA_PATH.open(encoding="utf-8") as testdata_file:
        data = json.load(testdata_file)[task_type][payload_id]
    data["BACK_URL_RECORD_RESULT"] = result_url
    return {"task_type": task_type, "data": data}


def _run_task(queue, task_type: str, payload_id: str, result_url: str):
    envelope = _build_envelope(task_type, payload_id, result_url)
    task_type, machine_name, payload = queue._extract_task_envelope(envelope)
    credentials = queue._normalize_runtime_credentials(
        queue._extract_psv_credentials(payload, {})
    )
    queue._validate_runtime_credentials(credentials)

    result = queue.TASK_HANDLERS[task_type](credentials, payload)
    success = result.get("status") == "success"
    asyncio.run(
        queue._call_record_result(
            task_payload=payload,
            queue_id=payload.get("queue_id", ""),
            task_type=task_type,
            success=success,
            machine_name=machine_name,
            result=result,
            error_message=None if success else result.get("message"),
        )
    )


def main() -> None:
    load_dotenv()
    args = _parse_args()
    # Must be set before bot config is imported, which reads it once.
    os.environ["PRINTSMITH_HEADLESS"] = str(args.headless).lower()

    from app.v1.modules.bot.services import queue_service as queue

    result_url = _start_capture_server()
    flows = FLOW_TASK_TYPES if args.flow == "both" else [args.flow]
    for flow in flows:
        _run_task(queue, FLOW_TASK_TYPES[flow], args.id, result_url)


if __name__ == "__main__":
    main()
