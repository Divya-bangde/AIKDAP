"""OpenAlex settings, provider, abstract reconstruction, and gap detection
(Milestone 10 step 2 -- spec section 2)."""

import pytest
from pydantic import SecretStr

from app.core.config.settings import Settings


def test_openalex_settings_default_to_unconfigured(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key is None
    assert settings.openalex_timeout == 20.0


def test_blank_openalex_key_is_treated_as_unset():
    assert Settings.blank_openalex_key_is_unset("") is None
    assert Settings.blank_openalex_key_is_unset("   ") is None
    assert Settings.blank_openalex_key_is_unset("real-key") == "real-key"
    assert Settings.blank_openalex_key_is_unset(None) is None
