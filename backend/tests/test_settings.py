"""Defaults for settings this sprint added, matching the project's
existing style (Field(default=..., gt=0) for timeouts)."""

from app.core.config import settings


def test_paper_import_download_settings_have_sane_defaults():
    assert settings.paper_import_download_timeout == 30.0
    assert settings.paper_import_max_redirects == 3
