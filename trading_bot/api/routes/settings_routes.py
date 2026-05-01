from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from pathlib import Path
import os

from core.database import get_session
from core.models import BotConfig
from core.security import (
    set_credential,
    delete_credential,
    has_credential,
    get_credential,
)
from exchange.binance_client import binance_client, is_configured
from ai_brain.provider_factory import PROVIDERS, get_available_providers, get_config_value
from ai_brain.ollama_provider import OllamaProvider
from ai_brain.openai_provider import OpenAIProvider
from ai_brain.openai_provider import is_configured as openai_is_configured
from ai_brain.opencode_provider import OpenCodeProvider, DEFAULT_BASE_URL as OPENCODE_DEFAULT_BASE_URL
from ai_brain.anthropic_provider import AnthropicProvider
from ai_brain.openrouter_provider import OpenRouterProvider
from ai_brain.openclaw_provider import OpenclawProvider, DEFAULT_BASE_URL
from ai_brain.codex_cli_provider import CodexCliProvider
from ai_brain.ai_runtime import manual_ai_session
from api.schemas import (
    BinanceKeysRequest,
    AIConfigRequest,
    StrategyRequest,
    SettingsStatusResponse,
    CMCKeyRequest,
)


router = APIRouter(prefix="/api/settings", tags=["settings"])


async def upsert_config(session, key: str, value: str):
    """Insert or update a BotConfig row by primary key."""
    obj = BotConfig(key=key, value=value)
    await session.merge(obj)


def _credential_name(provider: str, suffix: str) -> str:
    return f"{provider.upper()}_{suffix}"


async def is_ai_provider_enabled(provider: str) -> bool:
    value = await get_config_value(f"ai_provider_enabled_{provider}")
    return value != "false"


async def provider_summary(provider: dict) -> dict:
    name = provider["name"]
    current_provider = await get_config_value("ai_provider")
    model = await get_config_value("ai_model") if name == current_provider else None
    model = model or get_credential(_credential_name(name, "MODEL")) or ""
    if name == "codex":
        model = CodexCliProvider(model=model or None)._codex_model()
    base_url = None
    if name == "openclaw":
        base_url = get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL
    elif name == "ollama":
        base_url = get_credential("OLLAMA_BASE_URL") or "http://localhost:11434"
    elif name == "opencode":
        base_url = get_credential("OPENCODE_BASE_URL") or OPENCODE_DEFAULT_BASE_URL
    return {
        **provider,
        "enabled": await is_ai_provider_enabled(name),
        "configured": provider_configured(name),
        "model": model,
        "base_url": base_url,
    }


def provider_configured(provider: str) -> bool:
    if provider in {"openclaw", "ollama", "codex"}:
        return True
    if provider == "openai":
        return openai_is_configured()
    return has_credential(_credential_name(provider, "API_KEY")) or os.environ.get(_credential_name(provider, "API_KEY")) is not None


def build_provider(provider: str, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
    if provider == "openclaw":
        return OpenclawProvider(base_url=base_url, api_key=api_key, model=model or None)
    if provider == "ollama":
        return OllamaProvider(base_url=base_url, api_key=api_key, model=model or None)
    if provider == "opencode":
        return OpenCodeProvider(base_url=base_url, api_key=api_key, model=model or None)
    if provider == "codex":
        return CodexCliProvider(model=model or None)
    provider_class = PROVIDERS.get(provider)
    if not provider_class:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    return provider_class(api_key=api_key, model=model or None)


@router.get("/status", response_model=SettingsStatusResponse)
async def get_settings_status():
    ai_provider = await get_config_value("ai_provider")
    ai_model = await get_config_value("ai_model")
    if ai_provider and ai_provider not in PROVIDERS:
        ai_provider = None
        ai_model = None
    return SettingsStatusResponse(
        binance_configured=is_configured(),
        ai_provider=ai_provider,
        ai_model=ai_model,
    )


@router.post("/binance")
async def save_binance_keys(request: BinanceKeysRequest):
    set_credential("BINANCE_API_KEY", request.api_key)
    set_credential("BINANCE_API_SECRET", request.api_secret)
    async for session in get_session():
        await upsert_config(session, "binance_configured", "true")
        await session.commit()
    return {"message": "Binance keys saved"}


@router.delete("/binance")
async def delete_binance_keys():
    delete_credential("BINANCE_API_KEY")
    delete_credential("BINANCE_API_SECRET")
    async for session in get_session():
        await upsert_config(session, "binance_configured", "false")
        await session.commit()
    return {"message": "Binance keys removed"}


@router.get("/ai")
async def get_ai_config():
    provider = await get_config_value("ai_provider")
    model = await get_config_value("ai_model")
    if provider and provider not in PROVIDERS:
        provider = None
        model = None
    providers = get_available_providers()
    provider_cards = [await provider_summary(p) for p in providers]
    openclaw_base = get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL
    ollama_base = get_credential("OLLAMA_BASE_URL") or "http://localhost:11434"
    opencode_base = get_credential("OPENCODE_BASE_URL") or OPENCODE_DEFAULT_BASE_URL
    openai_key_configured = openai_is_configured()
    codex_status = await CodexCliProvider(model=model or None).get_login_status()
    return {
        "current_provider": provider,
        "current_model": model,
        "available_providers": providers,
        "provider_cards": provider_cards,
        "openclaw_base_url": openclaw_base,
        "ollama_base_url": ollama_base,
        "opencode_base_url": opencode_base,
        "openai_key_configured": openai_key_configured,
        "openai_auth_note": "OpenAI API access uses an API key. Codex or ChatGPT login cannot be reused here.",
        "codex_available": codex_status.get("connected", False),
        "codex_login_status": codex_status.get("login_status") if codex_status.get("connected") else codex_status.get("error"),
        "codex_auth_note": "Codex CLI uses your local ChatGPT sign-in, runs on this machine, and is advisory-only for CryptoBot.",
        "codex_workspace": codex_status.get("workspace"),
        "opencode_auth_note": "OpenCode API uses an API key. The default base URL targets OpenCode Zen's chat-completions compatible endpoint family.",
        "cmc_key_configured": bool(get_credential("CMC_API_KEY")),
    }


@router.get("/ai/providers")
async def get_ai_providers():
    providers = [await provider_summary(p) for p in get_available_providers()]
    return {"providers": providers}


@router.post("/ai/providers/{provider}/enabled")
async def set_ai_provider_enabled(provider: str, payload: dict):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    enabled = bool(payload.get("enabled"))
    async for session in get_session():
        await upsert_config(session, f"ai_provider_enabled_{provider}", "true" if enabled else "false")
        await session.commit()
    return {"provider": provider, "enabled": enabled}


@router.post("/ai/providers/{provider}/test")
async def test_ai_provider(provider: str, payload: dict | None = None):
    payload = payload or {}
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    current_provider = await get_config_value("ai_provider")
    model = payload.get("model")
    if not model and provider == current_provider:
        model = await get_config_value("ai_model")
    api_key = payload.get("api_key") or None
    base_url = payload.get("base_url") or None
    async with manual_ai_session(f"manual provider test: {provider}"):
        ai = build_provider(provider, model=model, base_url=base_url, api_key=api_key)
        if hasattr(ai, "test_connection"):
            result = await ai.test_connection()
        else:
            result = {"connected": True}
    return {
        "provider": provider,
        "enabled": await is_ai_provider_enabled(provider),
        **result,
    }


@router.get("/ai/providers/{provider}/models")
async def get_ai_provider_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    current_provider = await get_config_value("ai_provider")
    model = await get_config_value("ai_model") if provider == current_provider else None
    async with manual_ai_session(f"manual model fetch: {provider}", pause_seconds=75):
        ai = build_provider(provider, model=model)
        if hasattr(ai, "list_models"):
            models = await ai.list_models()
            names = [m.get("name") if isinstance(m, dict) else str(m) for m in models]
            errors = [m.get("error") for m in models if isinstance(m, dict) and m.get("error")]
            return {"provider": provider, "models": [{"name": n} for n in names if n and not str(n).startswith("Error:")], "errors": errors}

        if hasattr(ai, "test_connection"):
            result = await ai.test_connection()
            names = result.get("available_models", [])
            return {
                "provider": provider,
                "models": [{"name": name} for name in names],
                "connected": result.get("connected", False),
                "error": result.get("error"),
            }

    return {"provider": provider, "models": []}


@router.post("/ai")
async def save_ai_config(request: AIConfigRequest):
    if request.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {request.provider}")
    async for session in get_session():
        await upsert_config(session, "ai_provider", request.provider)
        await upsert_config(session, "ai_model", request.model or "")
        await upsert_config(session, f"ai_provider_enabled_{request.provider}", "true")
        if request.provider == "codex":
            await upsert_config(session, "paper_trading", "true")
        await session.commit()

    if request.model:
        set_credential(_credential_name(request.provider, "MODEL"), request.model)

    if request.provider == "openclaw":
        if request.base_url:
            set_credential("OPENCLAW_BASE_URL", request.base_url)
        if request.api_key:
            set_credential("OPENCLAW_API_KEY", request.api_key)
        if request.model:
            set_credential("OPENCLAW_MODEL", request.model)
    elif request.provider == "ollama":
        if request.base_url:
            set_credential("OLLAMA_BASE_URL", request.base_url)
        elif request.api_key:
            if request.api_key.startswith("http"):
                set_credential("OLLAMA_BASE_URL", request.api_key)
            else:
                set_credential("OLLAMA_API_KEY", request.api_key)
        set_credential("OLLAMA_MODEL", request.model or "qwen3.5")
    elif request.provider == "opencode":
        if request.base_url:
            set_credential("OPENCODE_BASE_URL", request.base_url)
        if request.api_key:
            set_credential("OPENCODE_API_KEY", request.api_key)
        if request.model:
            set_credential("OPENCODE_MODEL", request.model)
    elif request.provider == "codex":
        if request.model:
            set_credential("CODEX_MODEL", request.model)
        set_credential("CODEX_WORKSPACE", str(Path(__file__).resolve().parents[3]))
    elif request.api_key:
        set_credential(f"{request.provider.upper()}_API_KEY", request.api_key)
        if request.provider == "openai" and request.model:
            set_credential("OPENAI_MODEL", request.model)
    elif request.provider == "openai" and request.model:
        set_credential("OPENAI_MODEL", request.model)

    return {"message": "AI config saved"}


@router.post("/cmc")
async def save_cmc_key(request: CMCKeyRequest):
    set_credential("CMC_API_KEY", request.api_key)
    return {"message": "CoinMarketCap key saved"}


@router.get("/strategy")
async def get_strategy_settings():
    from trading.strategy import get_strategy
    strategy = await get_strategy()
    async for session in get_session():
        from sqlalchemy import select as sa_select
        from core.models import BotConfig
        result = await session.execute(sa_select(BotConfig).where(BotConfig.key == "daily_loss_limit_usd"))
        loss_row = result.scalar_one_or_none()
        daily_loss_limit_usd = float(loss_row.value) if loss_row and loss_row.value else 0.0
        return {
            "allowed_symbols": strategy.allowed_symbols,
            "max_trade_usd": strategy.max_trade_usd,
            "max_open_trades": strategy.max_open_trades,
            "confidence_threshold": strategy.confidence_threshold,
            "stop_loss_pct": strategy.stop_loss_pct,
            "take_profit_pct": strategy.take_profit_pct,
            "paper_trading": strategy.paper_trading,
            "trading_interval_seconds": strategy.trading_interval_seconds,
            "profiles": {k: v.__dict__ for k, v in strategy.profiles.items()} if strategy.profiles else {},
            "daily_loss_limit_usd": daily_loss_limit_usd,
        }


@router.post("/strategy")
async def save_strategy_settings(request: StrategyRequest):
    import json
    async for session in get_session():
        provider = await get_config_value("ai_provider")
        paper_trading = True if provider == "codex" else request.paper_trading
        await upsert_config(session, "allowed_symbols", ",".join(request.allowed_symbols))
        await upsert_config(session, "max_trade_usd", str(request.max_trade_usd))
        await upsert_config(session, "max_open_trades", str(request.max_open_trades))
        await upsert_config(session, "confidence_threshold", str(request.confidence_threshold))
        await upsert_config(session, "stop_loss_pct", str(request.stop_loss_pct))
        await upsert_config(session, "take_profit_pct", str(request.take_profit_pct))
        await upsert_config(session, "paper_trading", "true" if paper_trading else "false")
        await upsert_config(session, "trading_interval_seconds", str(request.trading_interval_seconds))
        if request.profiles:
            await upsert_config(session, "strategy_profiles", json.dumps(request.profiles))
        if request.daily_loss_limit_usd is not None:
            await upsert_config(session, "daily_loss_limit_usd", str(request.daily_loss_limit_usd))
        await session.commit()
    if provider == "codex":
        return {"message": "Strategy saved. Paper trading remains enabled while Codex CLI is in advisory-only mode."}
    return {"message": "Strategy saved"}


@router.post("/test-binance")
async def test_binance_connection():
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")
    try:
        result = await binance_client.test_connection()
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/test-ai")
async def test_ai_connection():
    provider_name = await get_config_value("ai_provider")
    model = await get_config_value("ai_model")
    if not provider_name:
        raise HTTPException(status_code=403, detail="AI provider not configured")
    if provider_name not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"AI provider '{provider_name}' is no longer available")

    test_prompt = "Say 'OK' if you can read this."
    try:
        async with manual_ai_session("manual AI connection test"):
            if provider_name == "openclaw":
                base_url = get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL
                api_key = get_credential("OPENCLAW_API_KEY")
                provider = OpenclawProvider(base_url=base_url, api_key=api_key, model=model or "default")
                result = await provider.test_connection()
                if not result.get("connected"):
                    raise HTTPException(status_code=400, detail=f"Openclaw not connected: {result.get('error')}")
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {"response": response, "available_models": result.get("available_models", []), "base_url": base_url}
            elif provider_name == "ollama":
                api_key = get_credential("OLLAMA_API_KEY")
                base_url = get_credential("OLLAMA_BASE_URL") or "http://localhost:11434"
                provider = OllamaProvider(base_url=base_url, model=model or None, api_key=api_key)
                result = await provider.test_connection()
                if not result.get("connected"):
                    raise HTTPException(status_code=400, detail=f"Ollama not connected: {result.get('error')}")
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {"response": response, "available_models": result.get("available_models", []), "cloud_mode": result.get("cloud_mode", False)}
            elif provider_name == "codex":
                provider = CodexCliProvider(model=model or None)
                result = await provider.test_connection()
                if not result.get("connected"):
                    raise HTTPException(status_code=400, detail=f"Codex CLI not connected: {result.get('error')}")
                return {
                    "response": result.get("response", ""),
                    "login_status": result.get("login_status"),
                    "workspace": result.get("workspace"),
                    "advisory_only": True,
                }
            elif provider_name == "opencode":
                base_url = get_credential("OPENCODE_BASE_URL") or OPENCODE_DEFAULT_BASE_URL
                api_key = get_credential("OPENCODE_API_KEY")
                provider = OpenCodeProvider(base_url=base_url, api_key=api_key, model=model or None)
                result = await provider.test_connection()
                if not result.get("connected"):
                    raise HTTPException(status_code=400, detail=f"OpenCode not connected: {result.get('error')}")
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {
                    "response": response,
                    "available_models": result.get("available_models", []),
                    "base_url": result.get("base_url", base_url),
                }
            elif provider_name == "openai":
                provider = OpenAIProvider(model=model or None)
                result = await provider.test_connection()
                if not result.get("connected"):
                    raise HTTPException(status_code=400, detail=f"OpenAI not connected: {result.get('error')}")
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {"response": response, "available_models": result.get("available_models", [])}
            elif provider_name == "anthropic":
                provider = AnthropicProvider(model=model or None)
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {"response": response}
            elif provider_name == "openrouter":
                provider = OpenRouterProvider(model=model or None)
                response = await provider.ask("You are a helpful assistant.", test_prompt)
                return {"response": response}
            else:
                raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_name}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/openclaw/models")
async def get_openclaw_models():
    base_url = get_credential("OPENCLAW_BASE_URL") or DEFAULT_BASE_URL
    api_key = get_credential("OPENCLAW_API_KEY")
    provider = OpenclawProvider(base_url=base_url, api_key=api_key)
    models = await provider.list_models()
    return {"models": models, "base_url": base_url}


@router.get("/ollama/models")
async def get_ollama_models():
    print("[Routes] GET /ollama/models called")
    try:
        base_url = get_credential("OLLAMA_BASE_URL") or "http://localhost:11434"
        print(f"[Routes] base_url: {base_url}")
        api_key = get_credential("OLLAMA_API_KEY")
        print(f"[Routes] api_key present: {bool(api_key)}")
        provider = OllamaProvider(base_url=base_url, api_key=api_key)
        print("[Routes] Calling provider.list_models()...")
        models = await provider.list_models()
        print(f"[Routes] Returning {len(models)} models")
        return {"models": models}
    except Exception as e:
        print(f"[Routes] Error in get_ollama_models: {str(e)}")
        return {"models": [{"name": f"Error: {str(e)}", "error": True}]}


@router.get("/opencode/models")
async def get_opencode_models():
    base_url = get_credential("OPENCODE_BASE_URL") or OPENCODE_DEFAULT_BASE_URL
    api_key = get_credential("OPENCODE_API_KEY")
    provider = OpenCodeProvider(base_url=base_url, api_key=api_key)
    result = await provider.test_connection()
    if not result.get("connected"):
        return {"models": [{"name": f"Error: {result.get('error')}", "error": True}], "base_url": base_url}
    return {"models": [{"name": m} for m in result.get("available_models", [])], "base_url": base_url}


@router.post("/ollama/web-search-test")
async def test_ollama_web_search(query: str = "bitcoin price"):
    api_key = get_credential("OLLAMA_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="Ollama API key required for web search")
    provider = OllamaProvider(api_key=api_key)
    result = await provider.web_search(query)
    return result
