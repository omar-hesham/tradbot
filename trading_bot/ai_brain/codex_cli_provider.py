import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from ai_brain.base_provider import BaseAIProvider
from core.security import get_credential


DEFAULT_WORKSPACE = str(Path(__file__).resolve().parents[2])
DEFAULT_NODE = r"C:\Program Files\nodejs\node.exe"
DEFAULT_CODEX_JS = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "npm"
    / "node_modules"
    / "@openai"
    / "codex"
    / "bin"
    / "codex.js"
)
SUPPORTED_CODEX_MODELS = (
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2",
)
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
TRADE_SCHEMA_PATH = Path(__file__).with_name("codex_trade_schema.json")


class CodexCliProvider(BaseAIProvider):
    def __init__(
        self,
        model: Optional[str] = None,
        workspace: Optional[str] = None,
    ):
        self.model = model or get_credential("CODEX_MODEL") or ""
        self.workspace = workspace or get_credential("CODEX_WORKSPACE") or DEFAULT_WORKSPACE

    @property
    def needs_api_key(self) -> bool:
        return False

    @property
    def provider_name(self) -> str:
        return "codex"

    def _resolve_node(self) -> str:
        configured = get_credential("CODEX_NODE_PATH") or os.getenv("CODEX_NODE_PATH")
        if configured and Path(configured).exists():
            return configured
        if Path(DEFAULT_NODE).exists():
            return DEFAULT_NODE
        return shutil.which("node") or "node"

    def _resolve_codex_js(self) -> str:
        configured = get_credential("CODEX_CLI_JS_PATH") or os.getenv("CODEX_CLI_JS_PATH")
        if configured and Path(configured).exists():
            return configured
        if DEFAULT_CODEX_JS.exists():
            return str(DEFAULT_CODEX_JS)
        raise FileNotFoundError(
            "Codex CLI JavaScript entrypoint not found. Set CODEX_CLI_JS_PATH to codex.js."
        )

    def _base_command(self) -> list[str]:
        return [self._resolve_node(), self._resolve_codex_js()]

    def _codex_model(self) -> str:
        model = (self.model or "").strip()
        # BotConfig stores one global ai_model, so switching from OpenCode,
        # Anthropic, etc. can leave Codex holding an incompatible model id.
        if not model:
            return DEFAULT_CODEX_MODEL
        return model if model in SUPPORTED_CODEX_MODELS else DEFAULT_CODEX_MODEL

    def _clean_error_text(self, text: str) -> str:
        lowered_text = text.lower()
        if "access is denied" in lowered_text and "plugin cache" in lowered_text:
            return "Codex CLI plugin cache is blocked by Windows permissions (Access is denied)."
        if "status 403 forbidden" in lowered_text and "plugins/featured" in lowered_text:
            return "Codex CLI plugin sync was blocked with 403 Forbidden."

        noisy_markers = (
            "failed to warm featured plugin ids cache",
            "remote plugin sync request",
            "chatgpt.com/backend-api/plugins/featured",
            "backend-api/codex/analytics-events/events",
            "codex_core_plugins::manifest",
            "ignoring interface.defaultprompt",
            "codex_core::plugins::manager",
            "codex_analytics::client",
            "codex_core::shell_snapshot",
            "shell snapshot not supported",
        )
        html_markers = ("<!doctype", "<html", "<head", "<meta", "<style", "</")
        cleaned_lines = []
        for line in text.splitlines():
            lowered = line.lower()
            stripped = lowered.strip()
            if any(marker in lowered for marker in noisy_markers):
                continue
            if any(marker in stripped for marker in html_markers):
                continue
            cleaned_lines.append(line)
        cleaned = re.sub(r"<[^>]+>", " ", "\n".join(cleaned_lines)).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or "Codex CLI did not return a usable JSON response"

    def _message_is_cli_failure(self, message: str) -> bool:
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, dict):
            return False
        reason = str(parsed.get("reason", "")).lower()
        return (
            "codex cli unavailable" in reason
            or "<html" in reason
            or "<meta" in reason
            or "<style" in reason
            or "backend-api/plugins/featured" in reason
            or "backend-api/codex/analytics-events/events" in reason
            or "codex_core_plugins::manifest" in reason
        )

    async def _run(self, *args: str, input_text: Optional[str] = None, timeout: int = 180) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *self._base_command(),
            *args,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_text.encode() if input_text is not None else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError("Codex CLI timed out")

        return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    def _extract_agent_message(self, stdout: str) -> Optional[str]:
        fallback: Optional[str] = None
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text")
                if text:
                    text = text.strip()
                    fallback = text
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if (isinstance(parsed, dict) and "action" in parsed) or isinstance(parsed, list):
                        return text
        return fallback

    async def ask(self, system_prompt: str, user_prompt: str) -> str:
        # Use the prompts as provided, but add a hint for compact JSON if needed
        # We wrap in a generic instruction that doesn't force a specific schema
        instruction = (
            f"{system_prompt}\n\n"
            "Return only the valid JSON payload requested. No prose.\n\n"
            f"{user_prompt}"
        )

        args = [
            "exec",
            "--skip-git-repo-check",
            "--json",
            "--disable",
            "plugins",
            "-C",
            self.workspace,
        ]
        
        # Only use trade schema if it's a trade decision request
        # If it looks like a scanner (contains 'suggested_allocation_usd'), don't force the trade schema
        if "action" in system_prompt and "quantity_usd" in system_prompt and "suggested_allocation_usd" not in system_prompt:
            args.extend(["--output-schema", str(TRADE_SCHEMA_PATH)])

        codex_model = self._codex_model()
        if codex_model:
            args.extend(["-m", codex_model])
        args.append(instruction)

        returncode, stdout, stderr = await self._run(*args, timeout=400)
        unsupported_model = "not supported when using Codex with a ChatGPT account"
        combined_error = f"{stdout}\n{stderr}"
        if codex_model and unsupported_model in combined_error:
            retry_args = [
                "exec",
                "--skip-git-repo-check",
                "--json",
                "--disable",
                "plugins",
                "--output-schema",
                str(TRADE_SCHEMA_PATH),
                "-C",
                self.workspace,
                "-m",
                DEFAULT_CODEX_MODEL,
                instruction,
            ]
            returncode, stdout, stderr = await self._run(*retry_args, timeout=240)

        message = self._extract_agent_message(stdout)
        if message and not self._message_is_cli_failure(message):
            try:
                json.loads(message)
                return message
            except json.JSONDecodeError:
                pass

        error_text = self._clean_error_text(
            f"{stderr.strip()}\n{stdout.strip()}".strip() or f"Codex CLI exited with code {returncode}"
        )
        return json.dumps(
            {
                "action": "HOLD",
                "symbol": "BTCUSDT",
                "quantity_usd": 0,
                "confidence": 0.0,
                "reason": f"Codex CLI unavailable: {error_text[:240]}",
            }
        )

    async def get_login_status(self) -> dict:
        try:
            login_code, login_stdout, login_stderr = await self._run("login", "status", timeout=30)
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

        login_text = (login_stdout or login_stderr).strip()
        if login_code != 0 or "Logged in" not in login_text:
            return {"connected": False, "error": login_text or "Codex CLI is not logged in"}
        return {"connected": True, "login_status": login_text, "workspace": self.workspace}

    async def test_connection(self) -> dict:
        login_result = await self.get_login_status()
        if not login_result.get("connected"):
            return login_result

        try:
            probe = await self.ask(
                "You are a connectivity probe.",
                'Return HOLD for BTCUSDT with confidence 0.5 and reason "probe".',
            )
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

        try:
            parsed_probe = json.loads(probe)
        except json.JSONDecodeError:
            parsed_probe = {}
        if (
            self._message_is_cli_failure(probe)
            or not isinstance(parsed_probe, dict)
            or parsed_probe.get("action") not in {"BUY", "SELL", "HOLD"}
        ):
            raw_error = (
                str(parsed_probe.get("reason"))
                if isinstance(parsed_probe, dict) and parsed_probe.get("reason")
                else "Codex CLI did not return a usable JSON response"
            )
            raw_error = raw_error.removeprefix("Codex CLI unavailable:").strip()
            cleaned_error = self._clean_error_text(raw_error)
            return {
                "connected": False,
                "login_status": login_result.get("login_status"),
                "workspace": self.workspace,
                "error": f"Codex CLI unavailable: {cleaned_error}",
            }

        return {
            "connected": True,
            "login_status": login_result.get("login_status"),
            "workspace": self.workspace,
            "response": probe,
        }

    async def list_models(self) -> list[dict]:
        models = [
            self._codex_model(),
            *SUPPORTED_CODEX_MODELS,
        ]
        seen = set()
        return [
            {"name": model}
            for model in models
            if model and not (model in seen or seen.add(model))
        ]


def is_configured() -> bool:
    try:
        provider = CodexCliProvider()
        provider._base_command()
        return True
    except Exception:
        return False
