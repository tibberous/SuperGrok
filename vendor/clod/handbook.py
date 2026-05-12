#!/usr/bin/env python3
"""
handbook.py — Assembles the model handbook from numbered parts in handbook/.
Parts are sorted numerically by filename prefix (0001, 0100, 0200, etc.).

Static parts (md, txt, html, xml, rtf, sql) are read and included as-is.
Executable parts (ps1, bat, cmd, py) are run and their stdout is included.

HTML parts are included verbatim (they form the page shell).
Non-HTML dynamic output is wrapped in <pre class="tool-output"> for browser display.
Markdown/text parts are wrapped in <div class="section">.

Pass --text to get plain-text output instead of HTML (for debugging).
"""

import os, sys, subprocess, datetime, html as html_mod
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SELF_DIR     = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
HANDBOOK_DIR = SELF_DIR.parent / "handbook"

STATIC_EXTS = {".md", ".txt", ".html", ".xml", ".rtf", ".sql", ".pdf"}
RUN_EXTS    = {".ps1", ".bat", ".cmd", ".py", ".exe"}
HTML_EXTS   = {".html", ".htm"}

def sort_key(p):
    name = p.stem.split("_")[0]
    try:
        return int(name)
    except ValueError:
        return 99999

def run_part(path):
    ext = path.suffix.lower()
    python = sys.executable
    try:
        if ext == ".exe":
            r = subprocess.run([str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        elif ext == ".ps1":
            r = subprocess.run(
                ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
            )
        elif ext in {".bat", ".cmd"}:
            r = subprocess.run([str(path)], capture_output=True, text=True, timeout=30,
                               shell=True, encoding="utf-8", errors="replace")
        elif ext == ".py":
            r = subprocess.run([python, str(path)], capture_output=True, text=True,
                               timeout=30, encoding="utf-8", errors="replace")
        else:
            return f"(unsupported exec type: {ext})", False
        out = r.stdout.strip()
        if r.returncode != 0 and r.stderr:
            out += f"\n[stderr: {r.stderr.strip()[:200]}]"
        return out, False
    except subprocess.TimeoutExpired:
        return "(timed out after 30s)", False
    except Exception as e:
        return f"(error: {e})", False

def read_part(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip(), path.suffix.lower() in HTML_EXTS
    except Exception as e:
        return f"(read error: {e})", False

def label(path):
    return path.name.replace("_", " ").rsplit(".", 1)[0].lstrip("0123456789 ").strip() or path.stem

def assemble_html():
    if not HANDBOOK_DIR.exists():
        return f"<p>(handbook/ directory not found at {HANDBOOK_DIR})</p>"

    parts = sorted(
        [p for p in HANDBOOK_DIR.iterdir() if p.is_file() and p.suffix.lower() in STATIC_EXTS | RUN_EXTS],
        key=sort_key
    )

    out = []
    for part in parts:
        ext = part.suffix.lower()
        if ext in RUN_EXTS:
            content, is_html = run_part(part)
        else:
            content, is_html = read_part(part)

        if is_html or ext in HTML_EXTS:
            # Raw HTML — page shell, header, footer, etc.
            out.append(content)
        elif ext in {".md", ".txt"}:
            # Wrap markdown/text in a labelled section div
            escaped = html_mod.escape(content)
            out.append(
                f'\n<div class="section">\n'
                f'  <div class="section-title">{html_mod.escape(label(part))}</div>\n'
                f'  <pre style="white-space:pre-wrap;font-family:inherit;font-size:0.88rem;color:#333">{escaped}</pre>\n'
                f'</div>\n'
            )
        else:
            # Dynamic output (ps1, py, bat) — monospace tool-output block
            escaped = html_mod.escape(content)
            out.append(
                f'\n<div class="section">\n'
                f'  <div class="section-title">{html_mod.escape(label(part))}</div>\n'
                f'  <pre class="tool-output">{escaped}</pre>\n'
                f'</div>\n'
            )

    return "\n".join(out)

def assemble_text():
    if not HANDBOOK_DIR.exists():
        return f"(handbook/ directory not found at {HANDBOOK_DIR})"

    parts = sorted(
        [p for p in HANDBOOK_DIR.iterdir() if p.is_file() and p.suffix.lower() in STATIC_EXTS | RUN_EXTS],
        key=sort_key
    )

    now = datetime.datetime.now().strftime("%A, %B %d %Y %I:%M %p")
    sections = [f"── Handbook generated {now} ──────────────────────────────────────"]

    for part in parts:
        ext = part.suffix.lower()
        if ext in HTML_EXTS:
            continue  # skip HTML shell files in plain-text mode
        header = f"\n{'─'*60}\n[{part.name}]\n"
        if ext in RUN_EXTS:
            content, _ = run_part(part)
        else:
            content, _ = read_part(part)
        sections.append(header + content)

    sections.append(f"\n{'─'*60}\nAzure OpenAI Chat — https://www.trentontompkins.com")
    return "\n".join(sections)

if __name__ == "__main__":
    if "--text" in sys.argv:
        print(assemble_text())
    else:
        print(assemble_html())
