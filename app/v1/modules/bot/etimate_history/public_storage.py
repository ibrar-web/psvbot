import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from app.v1.modules.bot.config import BOT_PUBLIC_DIR


def store_file_publicly(
    local_path: Path,
    *,
    subfolder: str,
    tenant_id: str,
    queue_id: str,
) -> Dict[str, Optional[str]]:
    """Copy a local file into BOT_PUBLIC_DIR, served statically at /public
    with no auth. For now this is the whole "make it available" step; a
    later step will instead push the file to another server via API on top
    of this.
    """
    file_name = local_path.name
    public_dir = BOT_PUBLIC_DIR / subfolder / tenant_id / queue_id
    public_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}_{file_name}"
    stored_path = public_dir / stored_name
    shutil.copy2(local_path, stored_path)

    relative_path = stored_path.relative_to(BOT_PUBLIC_DIR).as_posix()
    return {
        "file_name": file_name,
        "file_local_path": str(stored_path),
        "file_url": f"/public/{relative_path}",
    }


def store_json_publicly(
    data: Any,
    *,
    subfolder: str,
    tenant_id: str,
    queue_id: str,
    file_name: str,
) -> Dict[str, Optional[str]]:
    """Write a JSON-serializable value directly into BOT_PUBLIC_DIR, served
    statically at /public with no auth. Same folder layout/URL shape as
    store_file_publicly, but for data generated in-process rather than a
    file already sitting on disk.
    """
    public_dir = BOT_PUBLIC_DIR / subfolder / tenant_id / queue_id
    public_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}_{file_name}"
    stored_path = public_dir / stored_name
    stored_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    relative_path = stored_path.relative_to(BOT_PUBLIC_DIR).as_posix()
    return {
        "file_name": file_name,
        "file_local_path": str(stored_path),
        "file_url": f"/public/{relative_path}",
    }
