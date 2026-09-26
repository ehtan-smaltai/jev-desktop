"""`jevd setup`: ask for the two API keys, check them, and store them in the user's profile."""

import getpass
import os
from pathlib import Path

import httpx

TEXT_MODELS = ",".join([
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-26b-a4b-it:free",
])
KEYS = (
    ("TYPESAFE_API_KEY", "TypeSafe API key (makes the decisions)", "https://docs.typesafe.ai/introduction"),
    ("TEXT_MODEL_API_KEY", "OpenRouter API key (writes typed text; free models work)", "https://openrouter.ai/keys"),
)


def config_path():
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "jev-desktop" / ".env"


def read_env(path):
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def check_typesafe(key):
    """A minimal two-option question. Returns None if the key works, else a reason."""
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {"note": "jev-desktop setup check"},
        "questions": {"ok": {"type": "choice", "criteria": {"YES": "The key works.", "NO": "It does not."}}},
    }
    try:
        response = httpx.post("https://api.typesafe.ai/v1/systemone", json=body, timeout=20,
                              headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        return "could not reach TypeSafe"
    return None if response.status_code == 200 else f"TypeSafe returned HTTP {response.status_code}"


def check_openrouter(key):
    try:
        response = httpx.get("https://openrouter.ai/api/v1/key", timeout=20, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        return "could not reach OpenRouter"
    return None if response.status_code == 200 else f"OpenRouter returned HTTP {response.status_code}"


CHECKS = {"TYPESAFE_API_KEY": check_typesafe, "TEXT_MODEL_API_KEY": check_openrouter}


def write_env(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"TYPESAFE_API_KEY={values['TYPESAFE_API_KEY']}",
        f"TYPESAFE_MODEL={values.get('TYPESAFE_MODEL') or 'jev-latest'}",
        f"TEXT_MODEL_API_KEY={values['TEXT_MODEL_API_KEY']}",
        f"TEXT_MODEL_BASE_URL={values.get('TEXT_MODEL_BASE_URL') or 'https://openrouter.ai/api/v1'}",
        "# Comma-separated fallbacks: a rate-limited model falls through to the next.",
        f"TEXT_MODEL={values.get('TEXT_MODEL') or TEXT_MODELS}",
        f"TEXT_MODEL_REASONING={values.get('TEXT_MODEL_REASONING') or 'none'}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # No BOM.


def run_setup(path=None, ask=getpass.getpass, say=print):
    path = Path(path) if path else config_path()
    values = read_env(path)
    say("jev-desktop setup. Keys are hidden as you type and stored only in:")
    say(f"  {path}\n")
    for name, label, url in KEYS:
        while True:
            have = values.get(name)
            prompt = f"{label}\n  get one: {url}\n  paste key{' (Enter keeps the current one)' if have else ''}: "
            key = ask(prompt).strip() or have
            if not key:
                say("  A key is required.")
                continue
            problem = CHECKS[name](key)
            if problem:
                say(f"  That key did not work: {problem}. Try again.")
                continue
            values[name] = key
            say("  OK.\n")
            break
    write_env(path, values)
    say("Setup complete. Try:\n  jevd \"Open Notepad and type: hello from jev\"")
    say("Stop it any time with Ctrl+Alt+Q or by moving the mouse to the top-left corner.")
    return path
