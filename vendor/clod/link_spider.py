"""
link_spider.py -- Crawl a site and report broken links.

Stays on the same domain by default. Follows href, src, action attrs.
Reports 404s, connection errors, and redirect chains.

Usage:
    python tools/link_spider.py https://example.com
    python tools/link_spider.py https://example.com --depth 3
    python tools/link_spider.py https://example.com --external   # follow off-domain too
    python tools/link_spider.py https://example.com --json > report.json
    python tools/link_spider.py file:///C:/mysite/index.html     # local files too
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import html.parser


# ---------------------------------------------------------------------------
# HTML link extractor
# ---------------------------------------------------------------------------

class LinkExtractor(html.parser.HTMLParser):
    LINK_ATTRS = {
        "a": "href", "link": "href", "script": "src",
        "img": "src", "iframe": "src", "form": "action",
        "area": "href", "base": "href",
    }

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_name = self.LINK_ATTRS.get(tag.lower())
        if not attr_name:
            return
        for name, value in attrs:
            if name.lower() == attr_name and value:
                abs_url = urljoin(self.base_url, value)
                abs_url, _ = urldefrag(abs_url)  # strip #anchors
                self.links.append(abs_url)


def extract_links(html_bytes: bytes, base_url: str) -> list[str]:
    try:
        text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        return []
    parser = LinkExtractor(base_url)
    try:
        parser.feed(text)
    except Exception:
        pass
    return parser.links


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PageResult:
    url: str
    status: int            # HTTP status, 0 = connection error
    ok: bool
    content_type: str = ""
    redirect_to: str = ""
    error: str = ""
    found_on: str = ""
    depth: int = 0
    links_found: int = 0


# ---------------------------------------------------------------------------
# Spider
# ---------------------------------------------------------------------------

class Spider:
    def __init__(self,
                 start_url: str,
                 max_depth: int = 2,
                 follow_external: bool = False,
                 delay: float = 0.1,
                 timeout: int = 10,
                 user_agent: str = "LinkSpider/1.0"):
        self.start_url = start_url.rstrip("/")
        self.origin = urlparse(start_url).netloc
        self.max_depth = max_depth
        self.follow_external = follow_external
        self.delay = delay
        self.timeout = timeout
        self.headers = {"User-Agent": user_agent}
        self.visited: set[str] = set()
        self.results: list[PageResult] = []

    def is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.origin

    def should_crawl(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https", "file"):
            return False
        if not self.follow_external and not self.is_same_domain(url):
            return False
        return True

    def fetch(self, url: str) -> tuple[int, str, str, bytes]:
        """Returns (status, content_type, redirect_url, body)."""
        try:
            req = Request(url, headers=self.headers)
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read(1024 * 512)  # cap at 512 KB
                ct = resp.headers.get_content_type() or ""
                final = resp.url
                return resp.status, ct, (final if final != url else ""), body
        except HTTPError as e:
            return e.code, "", "", b""
        except URLError as e:
            return 0, "", "", b""
        except Exception as e:
            return 0, "", "", b""

    def crawl(self) -> list[PageResult]:
        queue: deque[tuple[str, int, str]] = deque()
        queue.append((self.start_url, 0, ""))

        while queue:
            url, depth, found_on = queue.popleft()
            if url in self.visited:
                continue
            self.visited.add(url)

            status, ct, redirect, body = self.fetch(url)
            is_html = "html" in ct or url.endswith((".html", ".htm"))
            ok = 200 <= status < 400

            links_found = 0
            if ok and is_html and depth < self.max_depth:
                links = extract_links(body, url)
                links_found = len(links)
                for link in links:
                    if link not in self.visited and self.should_crawl(link):
                        queue.append((link, depth + 1, url))

            result = PageResult(
                url=url,
                status=status,
                ok=ok,
                content_type=ct,
                redirect_to=redirect,
                error="" if ok else f"HTTP {status}" if status else "Connection error",
                found_on=found_on,
                depth=depth,
                links_found=links_found,
            )
            self.results.append(result)

            if self.delay:
                time.sleep(self.delay)

        return self.results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="Start URL")
    p.add_argument("--depth",    type=int,   default=2,   help="Max crawl depth (default 2)")
    p.add_argument("--external", action="store_true",     help="Follow off-domain links")
    p.add_argument("--delay",    type=float, default=0.1, help="Seconds between requests")
    p.add_argument("--timeout",  type=int,   default=10,  help="Request timeout in seconds")
    p.add_argument("--json",     action="store_true",     help="Output JSON")
    p.add_argument("--broken",   action="store_true",     help="Only show broken links")
    args = p.parse_args()

    spider = Spider(
        args.url,
        max_depth=args.depth,
        follow_external=args.external,
        delay=args.delay,
        timeout=args.timeout,
    )

    print(f"[spider] Starting at {args.url} (depth={args.depth})", file=sys.stderr)
    results = spider.crawl()
    print(f"[spider] Done. {len(results)} URLs checked.", file=sys.stderr)

    if args.json:
        data = [asdict(r) for r in results]
        if args.broken:
            data = [r for r in data if not r["ok"]]
        print(json.dumps(data, indent=2))
        return

    broken = [r for r in results if not r.ok]
    ok     = [r for r in results if r.ok]

    if not args.broken:
        print(f"\n{'URL':<70} {'STATUS':<8} {'DEPTH'}")
        print("-" * 90)
        for r in sorted(results, key=lambda x: (x.depth, x.url)):
            tag = "OK" if r.ok else f"ERR {r.status or 'CONN'}"
            print(f"  {r.url:<68} {tag:<8} {r.depth}")

    print(f"\n{'='*60}")
    print(f"  Total:  {len(results)}")
    print(f"  OK:     {len(ok)}")
    print(f"  Broken: {len(broken)}")

    if broken:
        print(f"\nBROKEN LINKS:")
        for r in broken:
            print(f"  [{r.status or 'CONN'}] {r.url}")
            if r.found_on:
                print(f"         found on: {r.found_on}")


if __name__ == "__main__":
    main()
