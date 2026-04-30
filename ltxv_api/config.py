"""API key resolution.

The LTX API key is never accepted as a node-input widget -- widgets get
serialised into saved workflows and screenshots, which is exactly the leak we
want to avoid. Instead, the key is looked up from one of three locations, in
order:

1. ``LTXV_API_KEY`` environment variable.
2. A ``.env``-style file at one of the well-known studio config paths
   (Windows: ``Z:\\admin\\config\\ltx-video.env``; Linux:
   ``/_pipeline/admin/config/ltx-video.env``). These paths exist only on
   am-pipeline studio installs; they are silently skipped when absent.
3. A user-level config file at ``~/.config/comfyui-ltxv-api/config.toml``
   (or ``%APPDATA%\\comfyui-ltxv-api\\config.toml`` on Windows) with a
   single ``api_key = "..."`` field.

If none of these resolves, :class:`ApiKeyNotFoundError` is raised with a
message pointing at the README's setup section.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

ENV_VAR = "LTXV_API_KEY"

# Studio-wide pipeline-readable config paths. These are intentionally hard-
# coded to the am-pipeline-prod studio layout; they no-op for everyone else.
_STUDIO_ENV_FILES = (
    Path(r"Z:\admin\config\ltx-video.env"),
    Path("/_pipeline/admin/config/ltx-video.env"),
)


class ApiKeyNotFoundError(RuntimeError):
    """Raised when the LTX API key cannot be resolved."""


def _user_config_path() -> Path:
    """Per-user fallback path. Cross-platform, no studio assumptions."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "comfyui-ltxv-api" / "config.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "comfyui-ltxv-api" / "config.toml"


def _parse_env_file(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != ENV_VAR:
            continue
        return value.strip().strip("'\"") or None
    return None


_TOML_API_KEY_RE = re.compile(
    r"""^\s*api_key\s*=\s*['\"]([^'\"]+)['\"]\s*(?:#.*)?$""",
    re.MULTILINE,
)


def _parse_toml_api_key(path: Path) -> Optional[str]:
    """Tiny TOML reader for the single key we care about.

    We intentionally do not depend on ``tomllib`` / ``tomli`` -- the file has at
    most one field and a regex keeps the dependency surface minimal.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _TOML_API_KEY_RE.search(text)
    return match.group(1) if match else None


def resolve_api_key() -> str:
    """Return the resolved LTX API key.

    Raises :class:`ApiKeyNotFoundError` if no source provides one.
    """
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return env_value.strip()

    for studio_path in _STUDIO_ENV_FILES:
        if studio_path.is_file():
            value = _parse_env_file(studio_path)
            if value:
                return value

    user_cfg = _user_config_path()
    if user_cfg.is_file():
        value = _parse_toml_api_key(user_cfg)
        if value:
            return value

    studio_paths = "\n  ".join(str(p) for p in _STUDIO_ENV_FILES)
    raise ApiKeyNotFoundError(
        "No LTX API key found. Set one of:\n"
        f"  * {ENV_VAR} environment variable, or\n"
        f"  * {user_cfg} with `api_key = \"...\"`, or\n"
        "  * (am-pipeline studio only) one of:\n"
        f"  {studio_paths}\n"
        "See the README's 'API key setup' section for details."
    )
