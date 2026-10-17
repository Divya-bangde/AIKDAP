"""Regression checks for the offline test boundary, not application behavior."""

import socket

import pytest

from app.core.config import settings
from app.core.llm.provider_health import ProviderHealthRegistry, get_provider_health_registry


def test_unit_registry_never_resets_the_application_redis():
    assert type(get_provider_health_registry()) is ProviderHealthRegistry


def test_accidental_remote_network_call_fails_before_connecting():
    with socket.socket() as connection:
        with pytest.raises(AssertionError, match="Unexpected network call"):
            connection.connect(("203.0.113.1", 443))


def test_no_real_model_credentials_are_loaded_from_environment():
    assert settings.gemini_api_key is None
    assert settings.groq_api_key is None
    assert settings.openrouter_api_key is None
    assert settings.jina_api_key is None
    assert settings.tavily_api_key is None
