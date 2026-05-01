import asyncio
import time
from contextlib import asynccontextmanager


_ai_lock = asyncio.Lock()
_auto_paused_until = 0.0
_auto_pause_reason = ""


def pause_auto_ai(seconds: int = 90, reason: str = "manual AI request") -> None:
    global _auto_paused_until, _auto_pause_reason
    _auto_paused_until = max(_auto_paused_until, time.monotonic() + seconds)
    _auto_pause_reason = reason


def auto_ai_pause_status() -> tuple[bool, str]:
    if time.monotonic() < _auto_paused_until:
        return True, _auto_pause_reason
    return False, ""


def is_ai_busy() -> bool:
    return _ai_lock.locked()


@asynccontextmanager
async def auto_ai_session():
    paused, reason = auto_ai_pause_status()
    if paused or _ai_lock.locked():
        yield False, reason or "another AI request is already running"
        return

    await _ai_lock.acquire()
    try:
        yield True, ""
    finally:
        _ai_lock.release()


@asynccontextmanager
async def manual_ai_session(reason: str = "manual AI request", pause_seconds: int = 120):
    pause_auto_ai(pause_seconds, reason)
    await _ai_lock.acquire()
    try:
        yield
    finally:
        _ai_lock.release()
        pause_auto_ai(45, f"{reason} cooldown")
