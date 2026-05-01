"""Tests for ltxv_api.config api-key resolution."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ltxv_api import config  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch, tmp_path_factory):
    """Fully isolate the resolver from the host: clear env vars *and* point the
    studio paths at non-existent files so resolution is determined solely by
    what the test sets up.
    """
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    fake_studio = tmp_path_factory.mktemp("studio") / "does-not-exist.env"
    monkeypatch.setattr(config, "_STUDIO_ENV_FILES", (fake_studio,))


def test_env_var_wins(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_VAR, "from-env")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    user_cfg = tmp_path / "comfyui-ltxv-api" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text('api_key = "from-user-file"')
    assert config.resolve_api_key() == "from-env"


def test_user_config_file_used_when_env_missing(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    user_cfg = tmp_path / "comfyui-ltxv-api" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text('# comment\napi_key = "from-user-file"\n')
    assert config.resolve_api_key() == "from-user-file"


def test_studio_env_file_falls_through_to_user(clean_env, monkeypatch, tmp_path):
    """A studio file present but lacking LTXV_API_KEY should not block the
    user-config fallback."""
    studio_path = tmp_path / "studio.env"
    studio_path.write_text("OTHER_VAR=foo\n")
    monkeypatch.setattr(config, "_STUDIO_ENV_FILES", (studio_path,))

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    user_cfg = tmp_path / "comfyui-ltxv-api" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text('api_key = "from-user"')
    assert config.resolve_api_key() == "from-user"


def test_missing_raises_with_helpful_message(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(config.ApiKeyNotFoundError) as exc_info:
        config.resolve_api_key()
    assert config.ENV_VAR in str(exc_info.value)
    assert "config.toml" in str(exc_info.value)


def test_env_file_parser(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        "# header\n"
        "OTHER=foo\n"
        f"{config.ENV_VAR}='quoted-value'\n"
    )
    assert config._parse_env_file(p) == "quoted-value"


def test_toml_parser_handles_double_quotes(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('# header\napi_key = "abc-123"  # trailing comment\n')
    assert config._parse_toml_api_key(p) == "abc-123"


def test_toml_parser_handles_single_quotes(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("api_key = 'abc-123'\n")
    assert config._parse_toml_api_key(p) == "abc-123"
