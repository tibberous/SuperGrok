"""
Quick Azure OpenAI chat test.
Run from the project root:
    python vendor/claude/azure_chat_test.py
Or with a custom prompt:
    python vendor/claude/azure_chat_test.py "What is 2+2?"
"""
import configparser
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── load config.ini ──────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent  # project root
_INI  = _ROOT / "config.ini"

def _load_config() -> dict:
    cfg = configparser.ConfigParser()
    cfg.read(str(_INI), encoding="utf-8")
    az = dict(cfg["azure"]) if cfg.has_section("azure") else {}
    return az

cfg = _load_config()

ENDPOINT       = cfg.get("endpoint", "").rstrip("/")
API_KEY        = cfg.get("api_key1") or cfg.get("api_key", "")
DEPLOYMENT     = cfg.get("deployment_name", "")
API_VERSION    = "2024-10-21"

# ── validate ─────────────────────────────────────────────────────────────────
missing = [k for k, v in [("endpoint", ENDPOINT), ("api_key1", API_KEY), ("deployment_name", DEPLOYMENT)] if not v]
if missing:
    print(f"[ERROR] Missing config values: {missing}")
    print(f"  config.ini path: {_INI}")
    sys.exit(1)

URL = f"{ENDPOINT}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version={API_VERSION}"

print(f"Endpoint  : {ENDPOINT}")
print(f"Deployment: {DEPLOYMENT}")
print(f"API key   : {API_KEY[:6]}…{API_KEY[-4:]}")
print(f"URL       : {URL}")
print()

# ── chat loop ─────────────────────────────────────────────────────────────────
history: list[dict] = []

def send(user_text: str) -> str:
    history.append({"role": "user", "content": user_text})
    body = json.dumps({"messages": history, "max_tokens": 1024}).encode()
    req = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json", "api-key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        return f"[HTTP {e.code}] {body_text}"
    except Exception as exc:
        return f"[ERROR] {exc}"

    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    history.append({"role": "assistant", "content": reply})
    return reply

# one-shot mode: python azure_chat_test.py "your prompt"
if len(sys.argv) > 1:
    prompt = " ".join(sys.argv[1:])
    print(f"You: {prompt}")
    print(f"Azure: {send(prompt)}")
    sys.exit(0)

# interactive loop
print("Type your message (or 'q' to quit).\n")
while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if user_input.lower() in {"q", "quit", "exit"}:
        break
    if not user_input:
        continue
    reply = send(user_input)
    print(f"Azure: {reply}\n")
