"""
chart_builder.py -- Render an ECharts chart from XML to PNG/JPEG/PDF.

Pipeline: chart.xml -> HTML -> PDF (via Chromium headless) -> image (optional)

Usage:
    python tools/chart_builder.py chart.xml
    python tools/chart_builder.py chart.xml --out chart.png
    python tools/chart_builder.py chart.xml --out chart.pdf
    python tools/chart_builder.py chart.xml --out chart.jpg --width 1200 --height 600

chart.xml format:
    <chart type="bar" theme="dark" width="900" height="500">
        <title>Monthly Sales</title>
        <xAxis type="category" data="Jan,Feb,Mar,Apr,May"/>
        <series name="Sales" type="bar" data="10,20,30,25,35"/>
        <series name="Revenue" type="line" data="100,150,200,175,220"/>
    </chart>
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ECHARTS_CDN = "https://unpkg.com/echarts@5/dist/echarts.min.js"

# Local ECharts path (used if CDN unreachable — bundle it alongside this tool)
ECHARTS_LOCAL = Path(__file__).parent.parent / "assets" / "echarts.min.js"


# ---------------------------------------------------------------------------
# XML -> ECharts option
# ---------------------------------------------------------------------------

def parse_data(s: str) -> list:
    """'10,20,30' -> [10.0, 20.0, 30.0]  or  'A,B,C' -> ['A','B','C']"""
    parts = [p.strip() for p in s.split(",")]
    try:
        return [float(p) if "." in p else int(p) for p in parts]
    except ValueError:
        return parts


def xml_to_option(root: ET.Element) -> tuple[dict, int, int, str]:
    """Returns (echarts_option, width, height, theme)."""
    width  = int(root.get("width",  900))
    height = int(root.get("height", 500))
    theme  = root.get("theme", "dark")

    opt: dict = {}

    title_el = root.find("title")
    if title_el is not None and title_el.text:
        opt["title"] = {"text": title_el.text.strip()}

    subtitle_el = root.find("subtitle")
    if subtitle_el is not None and subtitle_el.text:
        opt.setdefault("title", {})["subtext"] = subtitle_el.text.strip()

    opt["tooltip"] = {"trigger": "axis"}
    opt["legend"]  = {}

    # xAxis
    x_el = root.find("xAxis")
    if x_el is not None:
        opt["xAxis"] = {"type": x_el.get("type", "category")}
        if x_el.get("data"):
            opt["xAxis"]["data"] = parse_data(x_el.get("data"))
        if x_el.get("name"):
            opt["xAxis"]["name"] = x_el.get("name")
    else:
        opt["xAxis"] = {"type": "category"}

    # yAxis
    y_el = root.find("yAxis")
    if y_el is not None:
        opt["yAxis"] = {"type": y_el.get("type", "value")}
        if y_el.get("name"):
            opt["yAxis"]["name"] = y_el.get("name")
    else:
        opt["yAxis"] = {"type": "value"}

    # Series
    series = []
    for s_el in root.findall("series"):
        s: dict = {
            "type": s_el.get("type", root.get("type", "bar")),
        }
        if s_el.get("name"):
            s["name"] = s_el.get("name")
        if s_el.get("data"):
            raw = s_el.get("data")
            # Support JSON array in data attr: data="[[1,2],[3,4]]"
            if raw.startswith("["):
                s["data"] = json.loads(raw)
            else:
                s["data"] = parse_data(raw)
        if s_el.get("smooth"):
            s["smooth"] = s_el.get("smooth").lower() == "true"
        if s_el.get("areaStyle"):
            s["areaStyle"] = {}
        if s_el.get("color"):
            s.setdefault("itemStyle", {})["color"] = s_el.get("color")
        if s_el.get("radius"):
            s["radius"] = s_el.get("radius")   # for pie
        series.append(s)

    # If no <series> children, check for inline data on root
    if not series and root.get("data"):
        series.append({
            "type": root.get("type", "bar"),
            "data": parse_data(root.get("data")),
        })

    if series:
        opt["series"] = series

    # dataZoom
    if root.get("zoom") == "true":
        opt["dataZoom"] = [
            {"type": "slider", "start": 0, "end": 100, "bottom": 0},
            {"type": "inside"},
        ]

    # Grid padding
    opt["grid"] = {"containLabel": True, "top": 60, "bottom": 50,
                   "left": 40, "right": 40}

    return opt, width, height, theme


# ---------------------------------------------------------------------------
# Build HTML page
# ---------------------------------------------------------------------------

def build_html(option: dict, width: int, height: int, theme: str) -> str:
    # Use local file if available, otherwise CDN
    if ECHARTS_LOCAL.exists():
        echarts_src = ECHARTS_LOCAL.as_posix()
        script_tag = f'<script src="file:///{echarts_src}"></script>'
    else:
        script_tag = f'<script src="{ECHARTS_CDN}"></script>'

    option_json = json.dumps(option, indent=2, ensure_ascii=False)
    bg = "#1a1a1a" if theme == "dark" else "#ffffff"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{width}px; height:{height}px; background:{bg}; overflow:hidden; }}
  #chart {{ width:{width}px; height:{height}px; }}
</style>
</head>
<body>
<div id="chart"></div>
{script_tag}
<script>
const chart = echarts.init(document.getElementById('chart'), '{theme}')
chart.setOption({option_json})
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Render via Chromium headless
# ---------------------------------------------------------------------------

def find_chrome() -> str | None:
    candidates = [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Users\%s\AppData\Local\Google\Chrome\Application\chrome.exe" % os.environ.get("USERNAME",""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    for c in candidates:
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.exists(path):
            return path
    return None


def render_pdf(html_path: str, pdf_path: str, width: int, height: int) -> None:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "Chrome/Chromium not found. Install it or add it to PATH.\n"
            "Download: https://www.google.com/chrome/"
        )
    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        f"--window-size={width},{height}",
        f"--print-to-pdf={pdf_path}",
        "--print-to-pdf-no-header",
        f"file:///{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Chrome failed:\n{result.stderr}")


def render_screenshot(html_path: str, img_path: str, width: int, height: int) -> None:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found.")
    cmd = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        f"--window-size={width},{height}",
        f"--screenshot={img_path}",
        f"file:///{html_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Chrome failed:\n{result.stderr}")


def pdf_to_image(pdf_path: str, img_path: str) -> None:
    """Convert first page of PDF to image using pdftoppm or ImageMagick."""
    ext = Path(img_path).suffix.lower()

    # Try pdftoppm (poppler)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        fmt = "jpeg" if ext in (".jpg", ".jpeg") else "png"
        base = str(Path(img_path).with_suffix(""))
        subprocess.run([pdftoppm, "-r", "150", "-singlefile",
                        f"-{fmt}", pdf_path, base], check=True)
        # pdftoppm appends nothing with -singlefile when output name given
        if not Path(img_path).exists():
            # some versions append -1
            candidate = base + "-1" + ext
            if Path(candidate).exists():
                Path(candidate).rename(img_path)
        return

    # Try ImageMagick convert
    convert = shutil.which("convert") or shutil.which("magick")
    if convert:
        subprocess.run([convert, "-density", "150",
                        f"{pdf_path}[0]", img_path], check=True)
        return

    raise RuntimeError(
        "PDF to image conversion requires pdftoppm (poppler) or ImageMagick.\n"
        "Install: choco install poppler  OR  choco install imagemagick\n"
        "Or use --out chart.pdf to skip image conversion."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("xml",           help="Input chart.xml file")
    p.add_argument("--out",         help="Output file (.png .jpg .pdf). Default: chart.png")
    p.add_argument("--width",  type=int, help="Override width from XML")
    p.add_argument("--height", type=int, help="Override height from XML")
    p.add_argument("--theme",       help="Override theme (dark/light)")
    p.add_argument("--keep-html",   action="store_true", help="Keep intermediate HTML")
    p.add_argument("--keep-pdf",    action="store_true", help="Keep intermediate PDF")
    args = p.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.exists():
        print(f"[ERROR] File not found: {xml_path}", file=sys.stderr)
        sys.exit(1)

    root = ET.parse(xml_path).getroot()
    option, width, height, theme = xml_to_option(root)

    if args.width:  width  = args.width
    if args.height: height = args.height
    if args.theme:  theme  = args.theme

    out_path = Path(args.out) if args.out else xml_path.with_suffix(".png")
    ext = out_path.suffix.lower()

    tmpdir = Path(tempfile.mkdtemp())
    html_path = tmpdir / "chart.html"
    pdf_path  = tmpdir / "chart.pdf"

    try:
        # Write HTML
        html_path.write_text(build_html(option, width, height, theme),
                             encoding="utf-8")
        print(f"[chart] HTML written: {html_path}")

        if ext == ".pdf":
            render_pdf(str(html_path), str(out_path), width, height)
            print(f"[chart] PDF saved: {out_path}")

        elif ext in (".png", ".jpg", ".jpeg"):
            # Try direct screenshot first
            try:
                render_screenshot(str(html_path), str(out_path), width, height)
                print(f"[chart] Image saved: {out_path}")
            except Exception as e:
                print(f"[chart] Screenshot failed ({e}), trying PDF→image route...")
                render_pdf(str(html_path), str(pdf_path), width, height)
                pdf_to_image(str(pdf_path), str(out_path))
                print(f"[chart] Image saved: {out_path}")
        else:
            print(f"[ERROR] Unknown output format: {ext}", file=sys.stderr)
            sys.exit(1)

        if args.keep_html:
            dest = out_path.with_suffix(".html")
            html_path.rename(dest)
            print(f"[chart] HTML kept: {dest}")

        if args.keep_pdf and pdf_path.exists():
            dest = out_path.with_suffix(".pdf")
            pdf_path.rename(dest)
            print(f"[chart] PDF kept: {dest}")

    finally:
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
