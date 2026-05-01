from typing import List

_scanner_logs: List[str] = ["Awaiting Market Data..."]
_current_raw_thought: str = ""

def add_log(msg: str):
    global _scanner_logs
    _scanner_logs.append(msg)
    # Keep last 5 lines for a clean terminal-like view
    if len(_scanner_logs) > 5:
        _scanner_logs.pop(0)

def set_thought(text: str):
    global _current_raw_thought
    _current_raw_thought = text

def get_logs() -> dict:
    return {
        "logs": _scanner_logs,
        "thought": _current_raw_thought
    }
    
def clear_logs():
    global _scanner_logs
    global _current_raw_thought
    _scanner_logs = ["Initializing AI Core..."]
    _current_raw_thought = ""
