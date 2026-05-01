import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query


router = APIRouter(prefix="/api/logs", tags=["logs"])
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = APP_DIR / "logs"
CLIENT_ERROR_LOG = LOG_DIR / "client_errors.jsonl"
SERVER_ERROR_LOG = LOG_DIR / "server_errors.jsonl"
APP_LOG = LOG_DIR / "app.log"
LEGACY_ERROR_LOG = LOG_DIR / "error.log"


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"raw": line})
    return records


def read_text_error_tail(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    interesting = [
        line
        for line in lines
        if " - ERROR - " in line or " - WARNING - " in line or "Traceback" in line
    ]
    return interesting[-limit:]


@router.post("/client-error")
async def log_client_error(payload: dict[str, Any] = Body(...)):
    safe_payload = {
        "source": "dashboard",
        "message": str(payload.get("message", ""))[:1000],
        "stack": str(payload.get("stack", ""))[:4000],
        "url": str(payload.get("url", ""))[:500],
        "user_agent": str(payload.get("userAgent", ""))[:500],
        "context": payload.get("context", {}),
    }
    append_jsonl(CLIENT_ERROR_LOG, safe_payload)
    logger.warning("Dashboard client error: %s", safe_payload["message"])
    return {"logged": True}


@router.get("/errors")
async def get_recent_errors(limit: int = Query(50, ge=1, le=200)):
    return {
        "server_errors": read_jsonl_tail(SERVER_ERROR_LOG, limit),
        "client_errors": read_jsonl_tail(CLIENT_ERROR_LOG, limit),
        "app_warnings": read_text_error_tail(APP_LOG, limit),
        "legacy_error_log": read_text_error_tail(LEGACY_ERROR_LOG, limit),
    }
