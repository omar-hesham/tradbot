import pytest


@pytest.mark.asyncio
async def test_provider_factory():
    from ai_brain.provider_factory import get_available_providers
    
    providers = get_available_providers()
    
    assert len(providers) == 7
    provider_names = [p["name"] for p in providers]
    assert "ollama" in provider_names
    assert "openai" in provider_names
    assert "anthropic" in provider_names
    assert "openclaw" in provider_names
    assert "codex" in provider_names
    assert "opencode" in provider_names


def test_ollama_provider_class():
    from ai_brain.ollama_provider import OllamaProvider
    
    provider = OllamaProvider()
    
    assert provider.needs_api_key is False
    assert provider.provider_name == "ollama"


def test_openai_provider_class():
    from ai_brain.openai_provider import OpenAIProvider
    
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o")
    
    assert provider.needs_api_key is True
    assert provider.provider_name == "openai"
    assert provider.model == "gpt-4o"


def test_anthropic_provider_class():
    from ai_brain.anthropic_provider import AnthropicProvider
    
    provider = AnthropicProvider(api_key="test-key", model="claude-3-5-sonnet-20241022")
    
    assert provider.needs_api_key is True
    assert provider.provider_name == "anthropic"

def test_openrouter_provider_class():
    from ai_brain.openrouter_provider import OpenRouterProvider
    
    provider = OpenRouterProvider(api_key="test-key", model="openai/gpt-4o")
    
    assert provider.needs_api_key is True
    assert provider.provider_name == "openrouter"


def test_codex_provider_class():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="gpt-5-codex")

    assert provider.needs_api_key is False
    assert provider.provider_name == "codex"
    assert provider.model == "gpt-5-codex"


def test_codex_ignores_non_codex_model_ids():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="claude-opus-4-7")

    assert provider._codex_model() == "gpt-5.4-mini"


def test_codex_uses_local_supported_default_for_newer_model():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="gpt-5.5")

    assert provider._codex_model() == "gpt-5.4-mini"


def test_codex_cleans_cli_html_noise():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="gpt-5.5")

    cleaned = provider._clean_error_text(
        'remote plugin sync request failed\n'
        'WARN codex_core_plugins::manifest: ignoring interface.defaultPrompt: bad plugin\n'
        '<html>\n<head>\n<meta name="viewport">\n<style>body{}</style>'
    )

    assert cleaned == "Codex CLI did not return a usable JSON response"


def test_codex_summarizes_plugin_cache_permission_error():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="gpt-5.5")

    cleaned = provider._clean_error_text(
        "WARN codex_core::plugins::manager: failed to refresh curated plugin cache: Access is denied"
    )

    assert cleaned == "Codex CLI plugin cache is blocked by Windows permissions (Access is denied)."


def test_codex_detects_cli_failure_payload():
    from ai_brain.codex_cli_provider import CodexCliProvider

    provider = CodexCliProvider(model="gpt-5.5")

    assert provider._message_is_cli_failure(
        '{"action":"HOLD","symbol":"BTCUSDT","quantity_usd":0,"confidence":0,"reason":"Codex CLI unavailable: <meta name=\\"viewport\\">"}'
    )


def test_opencode_provider_class():
    from ai_brain.opencode_provider import OpenCodeProvider

    provider = OpenCodeProvider(api_key="test-key", model="qwen3.5-plus")

    assert provider.needs_api_key is True
    assert provider.provider_name == "opencode"
    assert provider.model == "qwen3.5-plus"


def test_base_provider_is_abstract():
    from ai_brain.base_provider import BaseAIProvider
    from abc import ABC
    
    assert issubclass(BaseAIProvider, ABC)


def test_parse_scanner_object_with_opportunities():
    from ai_brain.prompt_builder import parse_response

    parsed = parse_response(
        """
        {
          "market_mood": "Bullish",
          "opportunities": [
            {"asset": "solusdt", "allocation_usd": 125, "narrative": "Volume expansion", "confidence": "0.78"}
          ]
        }
        """
    )

    assert parsed == [
        {
            "symbol": "SOLUSDT",
            "suggested_allocation_usd": 125.0,
            "reason": "Volume expansion",
            "confidence": 0.78,
            "sentiment": "Bullish",
        }
    ]


def test_parse_scanner_recommendations_key():
    from ai_brain.prompt_builder import parse_response

    parsed = parse_response(
        '```json\n{"recommendations":[{"symbol":"BTCUSDT","suggested_allocation_usd":250,"reason":"Stable bid","confidence":0.8}]}\n```'
    )

    assert parsed[0]["symbol"] == "BTCUSDT"
    assert parsed[0]["suggested_allocation_usd"] == 250.0


def test_parse_trade_decision_stays_dict():
    from ai_brain.prompt_builder import parse_response

    parsed = parse_response(
        '{"action":"HOLD","symbol":"BTCUSDT","quantity_usd":0,"reason":"No edge","confidence":0.4}'
    )

    assert isinstance(parsed, dict)
    assert parsed["action"] == "HOLD"


def test_parse_none_response_returns_none():
    from ai_brain.prompt_builder import parse_response

    assert parse_response(None) is None
