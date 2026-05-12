"""
Chat key-sync detector.
Compares API keys between config.ini and flatline.db, copies the newer value
to whichever store is missing or stale, then smoke-tests each live Azure
deployment plus any configured OpenAI / Anthropic / Gemini key.

Usage:
    python start.py --chat-sync
    python vendor/claude/chat_sync.py          (direct)
    python vendor/claude/chat_sync.py --root C:\\Path\\To\\App
"""
from __future__ import annotations

import argparse
import configparser
import datetime
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

# ── locate project root ───────────────────────────────────────────────────────
def _find_root(hint: str | None = None) -> Path:
    if hint:
        return Path(hint).resolve()
    # walk up from this file until we find config.ini
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / "config.ini").exists():
            return p
        p = p.parent
    return Path.cwd()

# ── key mappings: (config_section, config_option) <-> db_key ─────────────────
KEY_MAP: list[tuple[str, str, str]] = [
    ("azure",    "api_key1",     "azure_openai_key1"),
    ("azure",    "api_key2",     "azure_openai_key2"),
    ("azure",    "api_key",      "azure_openai_key"),
    ("azure",    "endpoint",     "azure_openai_endpoint"),
    ("azure",    "deployment_name", "azure_deployment_name"),
    ("azure",    "account_name", "azure_account_name"),
    ("azure",    "resource_group", "azure_resource_group"),
    ("azure",    "subscription_id", "azure_subscription_id"),
    ("azure",    "tenant_id",    "azure_tenant_id"),
    ("azure",    "client_id",    "azure_client_id"),
    ("azure",    "client_secret", "azure_client_secret"),
    ("azure",    "cognitive_services_endpoint", "azure_cognitive_services_endpoint"),
    ("azure",    "stt_endpoint", "azure_stt_endpoint"),
    ("azure",    "tts_endpoint", "azure_tts_endpoint"),
    ("api_keys", "openai_api_key",    "openai_api_key"),
    ("api_keys", "anthropic_api_key", "anthropic_api_key"),
    ("api_keys", "gemini_api_key",    "gemini_api_key"),
]

# ── read / write helpers ──────────────────────────────────────────────────────

def _read_ini(ini: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if ini.exists():
        cfg.read(str(ini), encoding="utf-8")
    return cfg

def _write_ini(cfg: configparser.ConfigParser, ini: Path) -> None:
    with open(str(ini), "w", encoding="utf-8") as fh:
        cfg.write(fh)

def _db_read(db: Path) -> dict[str, tuple[str, str | None]]:
    """Return {key: (value, modified_iso)} for settings table."""
    if not db.exists():
        return {}
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT key, value, modified FROM settings").fetchall()
        return {r[0]: (str(r[1] or ""), r[2]) for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()

def _db_write(db: Path, key: str, value: str) -> None:
    if not db.exists():
        return
    now = datetime.datetime.now().isoformat()
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO settings (key, value, modified) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, modified=excluded.modified",
            (key, value, now),
        )
        conn.commit()
    finally:
        conn.close()

# ── sync logic ────────────────────────────────────────────────────────────────

def sync_keys(root: Path, lines: list[str]) -> dict[str, str]:
    """Sync config.ini <-> flatline.db.  Returns merged value dict."""
    ini  = root / "config.ini"
    db   = root / "flatline.db"
    cfg  = _read_ini(ini)
    db_data = _db_read(db)
    merged: dict[str, str] = {}
    ini_dirty = False

    for section, option, db_key in KEY_MAP:
        ini_val = ""
        if cfg.has_section(section) and cfg.has_option(section, option):
            ini_val = cfg.get(section, option, fallback="").strip()

        db_entry = db_data.get(db_key)
        db_val   = (db_entry[0] if db_entry else "").strip()
        db_ts    = (db_entry[1] if db_entry else None)

        if ini_val and not db_val:
            _db_write(db, db_key, ini_val)
            lines.append(f"  SYNC  ini→db  {db_key}")
            merged[db_key] = ini_val
        elif db_val and not ini_val:
            if not cfg.has_section(section):
                cfg.add_section(section)
            cfg.set(section, option, db_val)
            ini_dirty = True
            lines.append(f"  SYNC  db→ini  [{section}]{option}")
            merged[db_key] = db_val
        elif ini_val and db_val and ini_val != db_val:
            # Both present but different — prefer DB if it has a modified timestamp
            if db_ts:
                if not cfg.has_section(section):
                    cfg.add_section(section)
                cfg.set(section, option, db_val)
                ini_dirty = True
                lines.append(f"  DIFF  db wins {db_key}  (db has timestamp, ini does not)")
                merged[db_key] = db_val
            else:
                _db_write(db, db_key, ini_val)
                lines.append(f"  DIFF  ini wins {db_key}  (db has no timestamp)")
                merged[db_key] = ini_val
        elif ini_val:
            merged[db_key] = ini_val
        elif db_val:
            merged[db_key] = db_val

    if ini_dirty:
        _write_ini(cfg, ini)
        lines.append("  Wrote updated config.ini")

    return merged

# ── model smoke-test ──────────────────────────────────────────────────────────
PING = "Say hi in exactly 3 words."

def _ping_azure(endpoint: str, key: str, deployment: str) -> tuple[bool, str]:
    ep  = endpoint.rstrip("/")
    url = f"{ep}/openai/deployments/{urllib.parse.quote(deployment, safe='')}/chat/completions?api-version=2025-01-01-preview"
    use_completion_tokens = any(deployment.startswith(p) for p in ("gpt-5", "o1", "o3", "o4", "grok", "kimi", "deep", "Deep"))
    body: dict = {"messages": [{"role": "user", "content": PING}]}
    if use_completion_tokens:
        body["max_completion_tokens"] = 64
    else:
        body["max_tokens"] = 64
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "api-key": key}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return True, reply or "(empty reply)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:120]}"
    except Exception as exc:
        return False, str(exc)[:120]

def _ping_openai(key: str) -> tuple[bool, str]:
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": PING}], "max_tokens": 64}
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return True, reply
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:120]}"
    except Exception as exc:
        return False, str(exc)[:120]

def _ping_anthropic(key: str) -> tuple[bool, str]:
    body = {"model": "claude-haiku-4-5-20251001", "max_tokens": 64, "messages": [{"role": "user", "content": PING}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        reply = ((data.get("content") or [{}])[0]).get("text", "").strip()
        return True, reply
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:120]}"
    except Exception as exc:
        return False, str(exc)[:120]

def _ping_gemini(key: str) -> tuple[bool, str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    body = {"contents": [{"parts": [{"text": PING}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        reply = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        return True, reply
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:120]}"
    except Exception as exc:
        return False, str(exc)[:120]

def _fetch_azure_deployments(endpoint: str, key: str) -> list[str]:
    ep = endpoint.rstrip("/")
    for version in ("2025-01-01-preview", "2024-10-21"):
        url = f"{ep}/openai/deployments?api-version={version}"
        req = urllib.request.Request(url, headers={"api-key": key}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            return [d["id"] for d in data.get("data", []) if d.get("id")]
        except Exception:
            continue
    return []

# ── main ──────────────────────────────────────────────────────────────────────

def run(root: Path) -> int:
    lines: list[str] = []
    lines.append(f"chat_sync  {datetime.datetime.now().isoformat()}")
    lines.append(f"root: {root}")
    lines.append("")

    # 1. Sync keys
    lines.append("=== Key sync ===")
    merged = sync_keys(root, lines)
    lines.append("")

    azure_ep   = merged.get("azure_openai_endpoint", "").rstrip("/")
    azure_key  = merged.get("azure_openai_key1") or merged.get("azure_openai_key", "")
    openai_key = merged.get("openai_api_key", "")
    anth_key   = merged.get("anthropic_api_key", "")
    gemini_key = merged.get("gemini_api_key", "")

    findings = 0

    # 2. Azure deployments
    if azure_ep and azure_key:
        lines.append("=== Azure deployments ===")
        deployments = _fetch_azure_deployments(azure_ep, azure_key)
        if not deployments:
            lines.append("  WARN  Could not fetch live deployment list — using saved name")
            saved = merged.get("azure_deployment_name", "")
            deployments = [saved] if saved else []

        for dep in deployments:
            ok, reply = _ping_azure(azure_ep, azure_key, dep)
            status = "OK  " if ok else "FAIL"
            lines.append(f"  {status}  {dep:35s}  {reply[:80]}")
            if not ok:
                findings += 1
        lines.append("")
    else:
        lines.append("=== Azure ===  SKIP (no endpoint or key)\n")

    # 3. OpenAI
    if openai_key:
        lines.append("=== OpenAI (gpt-4o-mini) ===")
        ok, reply = _ping_openai(openai_key)
        lines.append(f"  {'OK  ' if ok else 'FAIL'}  {reply[:80]}")
        if not ok:
            findings += 1
        lines.append("")
    else:
        lines.append("=== OpenAI ===  SKIP (no key)\n")

    # 4. Anthropic
    if anth_key:
        lines.append("=== Anthropic (claude-haiku-4-5) ===")
        ok, reply = _ping_anthropic(anth_key)
        lines.append(f"  {'OK  ' if ok else 'FAIL'}  {reply[:80]}")
        if not ok:
            findings += 1
        lines.append("")
    else:
        lines.append("=== Anthropic ===  SKIP (no key)\n")

    # 5. Gemini
    if gemini_key:
        lines.append("=== Gemini (gemini-2.0-flash) ===")
        ok, reply = _ping_gemini(gemini_key)
        lines.append(f"  {'OK  ' if ok else 'FAIL'}  {reply[:80]}")
        if not ok:
            findings += 1
        lines.append("")
    else:
        lines.append("=== Gemini ===  SKIP (no key)\n")

    lines.append(f"findings: {findings}")

    report_text = "\n".join(lines)

    # Write report
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / "chat_sync.txt"
    report_path.write_text(report_text, encoding="utf-8")

    print(report_text)
    print(f"\nReport: {report_path}")
    return 1 if findings else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chat key-sync and model health check")
    parser.add_argument("--root", default=None, help="Project root (default: auto-detect)")
    args = parser.parse_args()
    root = _find_root(args.root)
    sys.exit(run(root))
