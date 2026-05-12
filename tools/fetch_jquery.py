from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "jquery" / "jquery.min.js"
URL = "https://code.jquery.com/jquery-3.7.1.min.js"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    data = urlopen(URL, timeout=30).read()
    TARGET.write_bytes(data)
    print(f"[INFO:assets] Wrote {TARGET} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
