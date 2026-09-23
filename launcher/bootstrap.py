#!/usr/bin/env python3
"""
Self-updating launcher for the CyberHQ GRC Data Migration Tool.

This is the ONLY file that changes what the launcher does - the tiny
per-OS stubs (run-mac.command / run-windows.bat) exist purely to fetch
whichever copy of THIS file lives on `main` and run it, so improvements
here reach every user's next launch without anyone re-downloading the
stub. That also means this file must stay dependency-free (stdlib only)
- it has to run standalone via `uv run` before anything else has been
installed.

What it does, in order, every time it's launched:
  1. If the app is already running (someone double-clicked twice), just
     open the browser on it and exit - never starts a second server.
  2. Check GitHub for a newer commit than the one currently installed.
     If there is one, ask via a native OS dialog before touching
     anything - there's no CI/release gate on this repo, so a bad
     commit should never reach every machine silently.
  3. Download and apply a confirmed update (or the very first install),
     then re-exec into the freshly-downloaded copy of this file - so an
     update to this logic itself takes effect immediately, not next launch.
  4. Make sure the app's own venv exists and its dependencies are
     installed (only reinstalling if requirements.txt actually changed).
  5. Start the server in the foreground (closing this window stops it,
     matching what "closing the app" should feel like) and open the browser.

See PROJECT_BRIEF.md / README.md "Getting started" for the user-facing story.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPO = "AvertroJoe/Customer-Migration-Tool"
BRANCH = "main"
PYTHON_VERSION = "3.12"

APP_DIR = Path.home() / ".grc-migration-tool"
REPO_DIR = APP_DIR / "repo"
STATE_FILE = APP_DIR / "state.json"
VENV_DIR = REPO_DIR / ".venv"

SERVER_URL = "http://127.0.0.1:8000"
HEALTH_URL = f"{SERVER_URL}/api/health"

REEXEC_GUARD_ENV = "_GRC_REEXECED"


def log(msg: str) -> None:
    print(f"[grc-migration-tool] {msg}", flush=True)


# ---------- Persistent launcher state ----------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------- Fast path: is it already running? ----------

def is_already_running() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as resp:
            body = json.loads(resp.read())
            return body.get("app") == "grc-migration-tool"
    except Exception:
        return False


# ---------- Update check + native confirm dialog ----------

def get_latest_commit() -> dict | None:
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return {
            "sha": data["sha"],
            "subject": data["commit"]["message"].splitlines()[0],
        }
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
        log(f"Could not check for updates ({e}) - continuing with what's already installed.")
        return None


def confirm_update(subject: str) -> bool:
    message = f"An update is available:\n\n“{subject}”\n\nApply it now?"
    try:
        if sys.platform == "darwin":
            script = (
                f'display dialog "{message}" with title "CyberHQ GRC Migration Tool" '
                f'buttons {{"Skip", "Update"}} default button "Update"'
            )
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return "button returned:Update" in result.stdout
        if sys.platform == "win32":
            MB_YESNO = 0x04
            IDYES = 6
            response = ctypes.windll.user32.MessageBoxW(
                0, message, "CyberHQ GRC Migration Tool - Update available", MB_YESNO
            )
            return response == IDYES
    except Exception as e:
        log(f"Could not show the update dialog ({e}) - skipping this update for now.")
        return False

    log("Unsupported platform for a native dialog - skipping update prompt.")
    return False


# ---------- Download + apply an update ----------

def download_and_extract(sha: str) -> None:
    url = f"https://github.com/{REPO}/archive/{sha}.tar.gz"
    log(f"Downloading {sha[:8]}...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        archive_bytes = resp.read()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / "source.tar.gz"
        archive_path.write_bytes(archive_bytes)

        with tarfile.open(archive_path) as tar:
            tar.extractall(tmp_path)

        # GitHub's archive tarball wraps everything in one top-level dir
        # (e.g. Customer-Migration-Tool-<sha>/) - copy its contents up.
        extracted_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        if len(extracted_dirs) != 1:
            raise RuntimeError(f"Unexpected archive layout: {extracted_dirs}")
        source_root = extracted_dirs[0]

        REPO_DIR.mkdir(parents=True, exist_ok=True)
        # Only overwrites files the archive actually contains - .env and
        # .venv/ are git-ignored, so they're never in this tarball at all.
        shutil.copytree(source_root, REPO_DIR, dirs_exist_ok=True)

    log("Update applied.")


def reexec_into_updated_copy() -> None:
    if os.environ.get(REEXEC_GUARD_ENV) == "1":
        return  # already re-exec'd once this launch - don't risk a loop.

    new_bootstrap = REPO_DIR / "launcher" / "bootstrap.py"
    if not new_bootstrap.exists() or new_bootstrap.resolve() == Path(__file__).resolve():
        return

    log("Restarting with the updated launcher...")
    os.environ[REEXEC_GUARD_ENV] = "1"
    os.execv(sys.executable, [sys.executable, str(new_bootstrap), *sys.argv[1:]])


def ensure_latest_code() -> None:
    state = load_state()
    installed_sha = state.get("installed_sha")

    latest = get_latest_commit()
    if latest is None:
        if installed_sha is None:
            raise RuntimeError(
                "No internet connection and no local install found - can't get started. "
                "Check your connection and try again."
            )
        return  # can't check for updates right now; run what's already installed.

    if latest["sha"] == installed_sha:
        return  # already current.

    is_first_install = installed_sha is None
    if is_first_install:
        log("Installing for the first time...")
    elif not confirm_update(latest["subject"]):
        log("Update skipped for now.")
        return

    download_and_extract(latest["sha"])
    state["installed_sha"] = latest["sha"]
    save_state(state)
    reexec_into_updated_copy()


# ---------- venv + dependencies ----------

def find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    candidate = APP_DIR / "bin" / ("uv.exe" if sys.platform == "win32" else "uv")
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("Could not find the 'uv' tool - the launcher stub should have installed it.")


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv_and_deps() -> Path:
    uv = find_uv()
    requirements = REPO_DIR / "requirements.txt"
    req_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()

    state = load_state()
    python = venv_python()
    needs_install = not python.exists() or state.get("requirements_hash") != req_hash

    if not python.exists():
        log("Setting up a private Python environment (first run only)...")
        subprocess.run([uv, "venv", "--python", PYTHON_VERSION, str(VENV_DIR)], check=True)

    if needs_install:
        log("Installing dependencies...")
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), "-r", str(requirements)],
            check=True,
        )
        state["requirements_hash"] = req_hash
        save_state(state)

    return python


# ---------- Run the server ----------

def wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_already_running():
            return True
        time.sleep(0.5)
    return False


def start_server_and_open_browser(python: Path) -> None:
    log("Starting the server...")
    proc = subprocess.Popen(
        [
            str(python), "-m", "uvicorn", "app.main:app",
            "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000",
        ],
        cwd=str(REPO_DIR),
    )

    try:
        if wait_for_health():
            webbrowser.open(SERVER_URL)
            log(f"Running at {SERVER_URL} - close this window to stop the app.")
        else:
            log("The server didn't respond in time - check the output above for errors.")
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


def main() -> None:
    if is_already_running():
        log("Already running - opening your browser.")
        webbrowser.open(SERVER_URL)
        return

    ensure_latest_code()
    python = ensure_venv_and_deps()
    start_server_and_open_browser(python)


if __name__ == "__main__":
    main()
