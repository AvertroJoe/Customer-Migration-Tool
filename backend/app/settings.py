"""
Reads and writes the local .env file that holds LLM provider configuration
(which provider is active, and that provider's API key).

Security practices this module follows, given this is a local single-user
tool with no other secret store available:
- The .env file is the only place a key is ever persisted - never in the
  in-memory session store, never logged.
- Keys are written to .env with owner-only file permissions (chmod 600).
- A key is never sent back to the frontend in full - callers of
  get_status() only ever see a masked form (see mask_secret below).
- .env is already git-ignored (see .gitignore) so a saved key can't end
  up committed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

PROVIDER_INFO = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "key_env_var": "ANTHROPIC_API_KEY",
        "model_env_var": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-5-20250929",
        "console_url": "https://console.anthropic.com/settings/keys",
    },
    "gemini": {
        "label": "Gemini (Google)",
        "key_env_var": "GEMINI_API_KEY",
        "model_env_var": "GEMINI_MODEL",
        "default_model": "gemini-flash-lite-latest",
        "console_url": "https://aistudio.google.com/apikey",
    },
}


def _read_env_file() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_dotenv_into_environ() -> None:
    """Populate os.environ from .env at startup. Real environment variables
    (e.g. set by the shell or a deployment platform) always take priority."""
    for key, value in _read_env_file().items():
        os.environ.setdefault(key, value)


def write_env_values(updates: dict[str, str]) -> None:
    """Merge `updates` into the .env file in place, preserving any existing
    lines, comments and ordering. Creates the file if it doesn't exist."""
    existing_lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    try:
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600: owner read/write only
    except OSError:
        pass  # best-effort - not fatal on platforms/filesystems that don't support it

    os.environ.update(updates)


def mask_secret(value: str) -> str:
    """Redact a secret for display: only enough of the prefix/suffix to
    recognise which key it is, never enough to reconstruct it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 8}{value[-4:]}"


def get_status() -> dict:
    """Snapshot of provider configuration for the settings UI - keys are
    always masked, never returned in full."""
    current = _read_env_file()
    active_provider = os.environ.get("LLM_PROVIDER", "").strip().lower() or None
    if not active_provider:
        # Nothing explicitly chosen yet - if exactly one provider has a key,
        # that's the one classify_sheet() will actually use (see
        # classifier.get_active_provider_name), so reflect that here too
        # rather than showing "not configured" when it would in fact work.
        keyed = [key for key, info in PROVIDER_INFO.items() if current.get(info["key_env_var"])]
        if len(keyed) == 1:
            active_provider = keyed[0]

    providers = []
    for key, info in PROVIDER_INFO.items():
        raw_key = current.get(info["key_env_var"], "")
        providers.append(
            {
                "provider": key,
                "label": info["label"],
                "key_set": bool(raw_key),
                "key_masked": mask_secret(raw_key) if raw_key else None,
                "model": current.get(info["model_env_var"]) or info["default_model"],
                "default_model": info["default_model"],
                "console_url": info["console_url"],
            }
        )

    return {"active_provider": active_provider, "providers": providers}
