#!/usr/bin/env python3
from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "prism"
VERSION = "1.30.0"
BASE = f"https://cdn.jsdelivr.net/npm/prismjs@{VERSION}"

FILES = {
    "prism.min.js": f"{BASE}/prism.min.js",
    "prism.min.css": f"{BASE}/themes/prism-tomorrow.min.css",
    "prism-line-numbers.min.css": f"{BASE}/plugins/line-numbers/prism-line-numbers.min.css",
    "prism-line-numbers.min.js": f"{BASE}/plugins/line-numbers/prism-line-numbers.min.js",
    "prism-toolbar.min.css": f"{BASE}/plugins/toolbar/prism-toolbar.min.css",
    "prism-toolbar.min.js": f"{BASE}/plugins/toolbar/prism-toolbar.min.js",
    "prism-copy-to-clipboard.min.js": f"{BASE}/plugins/copy-to-clipboard/prism-copy-to-clipboard.min.js",
    "components/prism-python.min.js": f"{BASE}/components/prism-python.min.js",
    "components/prism-javascript.min.js": f"{BASE}/components/prism-javascript.min.js",
    "components/prism-markup.min.js": f"{BASE}/components/prism-markup.min.js",
    "components/prism-css.min.js": f"{BASE}/components/prism-css.min.js",
    "components/prism-json.min.js": f"{BASE}/components/prism-json.min.js",
    "components/prism-bash.min.js": f"{BASE}/components/prism-bash.min.js",
    "components/prism-powershell.min.js": f"{BASE}/components/prism-powershell.min.js",
    "components/prism-sql.min.js": f"{BASE}/components/prism-sql.min.js",
    "components/prism-yaml.min.js": f"{BASE}/components/prism-yaml.min.js",
    "components/prism-diff.min.js": f"{BASE}/components/prism-diff.min.js",
}


def download(name: str, url: str) -> None:
    target = OUT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO:assets] fetch {url} -> {target.relative_to(ROOT)}")
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read()
    target.write_bytes(data)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        download(name, url)
    (OUT / "PRISM_VERSION.txt").write_text(f"PrismJS {VERSION}\n{BASE}\n", encoding="utf-8")  # file-io-ok
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
