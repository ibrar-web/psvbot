from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException, status
from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import storage

from app.v1.core.settings import BUCKET_NAME, GOOGLE_APPLICATION_CREDENTIALS


def _credentials_path() -> Optional[Path]:
    if GOOGLE_APPLICATION_CREDENTIALS:
        path = Path(GOOGLE_APPLICATION_CREDENTIALS).expanduser()
        if path.exists():
            return path

    keys_dir = Path(__file__).resolve().parents[1] / "keys"
    if keys_dir.exists():
        json_files = sorted(keys_dir.glob("*.json"))
        if json_files:
            return json_files[0]

    alphagraphics_keys_dir = (
        Path(__file__).resolve().parents[4] / "alphgraphics" / "app" / "v1" / "keys"
    )
    if alphagraphics_keys_dir.exists():
        json_files = sorted(alphagraphics_keys_dir.glob("*.json"))
        if json_files:
            return json_files[0]

    return None


def _ensure_storage_ready() -> None:
    if not BUCKET_NAME:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage bucket is not configured. Set BUCKET_NAME.",
        )

    if _credentials_path() is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Google credentials JSON not found. "
                "Set GOOGLE_APPLICATION_CREDENTIALS or place a key in app/v1/keys."
            ),
        )


def _client() -> storage.Client:
    creds_path = _credentials_path()
    if creds_path is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google credentials JSON not found.",
        )

    return storage.Client.from_service_account_json(str(creds_path))


def _bucket():
    return _client().bucket(BUCKET_NAME)


def build_storage_key(prefix: str, file_name: str) -> str:
    clean_name = Path(file_name).name
    clean_prefix = (prefix or "").strip("/ ")
    if clean_prefix:
        return f"{clean_prefix}/{uuid4().hex}_{clean_name}"
    return f"{uuid4().hex}_{clean_name}"


def upload_bytes_to_storage(
    *,
    key: str,
    content: bytes,
    content_type: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    _ensure_storage_ready()
    try:
        blob = _bucket().blob(key)
        if metadata:
            blob.metadata = metadata
        blob.upload_from_string(content, content_type=content_type)
        blob.reload()
        return {
            "bucket": BUCKET_NAME,
            "key": key,
            "etag": (blob.etag or "").strip('"'),
        }
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GCS upload failed: {exc}",
        )


def generate_presigned_download_url(*, key: str, expires_in: int = 3600) -> str:
    _ensure_storage_ready()
    try:
        blob = _bucket().blob(key)
        return blob.generate_signed_url(
            expiration=timedelta(seconds=expires_in),
            method="GET",
            version="v4",
        )
    except GoogleAPICallError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GCS signed URL generation failed: {exc}",
        )


def extract_storage_key(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None

    raw = image_url.strip()
    if raw.startswith("gs://"):
        prefix = f"gs://{BUCKET_NAME}/"
        return raw[len(prefix):] if raw.startswith(prefix) else None

    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        expected_prefix = f"/{BUCKET_NAME}/"
        if parsed.path.startswith(expected_prefix):
            return parsed.path[len(expected_prefix):]
        return None

    return raw
