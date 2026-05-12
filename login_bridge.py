# ============================================================================
#  login_bridge.py — Stripped-down login-only Qt WebEngine window
#  ---------------------------------------------------------------------------
#  One QWebEngineView, one persistent QWebEngineProfile, no toolbar, no menus,
#  no debug/chat panes. Sized as small as possible for the provider's login UI
#  without scrollbars. Closes itself when auth completes.
#
#  Used by:
#    - `python start.py --login`     (interactive login for the chosen target)
#    - auto-handoff from --chat when the resident bridge reports loginLikely
#
#  Author : Trenton Tompkins  <trentontompkins@gmail.com>
#  Phone  : 724-431-5207
#  GitHub : https://github.com/tibberous/SuperGrok
#
#  Need help on your next project?
#  Call me at 724-431-5207 for a free consultation!
#
#  Codex by Claude Opus 4.7 and ChatGPT 5.5.
# ============================================================================
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Per-provider config: home URL, auth-domain hints, and the minimum no-scroll
# login window size. The window sizes are conservative defaults that fit each
# provider's logged-out auth UI without horizontal or vertical scrollbars.
# Refined per-provider via --probe-auth (writes back into this dict).
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, dict[str, Any]] = {
    "grok": {
        "label": "Grok",
        "homeUrl": "https://grok.com/",
        "loginUrl": "https://accounts.x.ai/sign-in?redirect=grok-com",
        "authHosts": ("accounts.x.ai", "x.ai/sign-in", "x.com/i/oauth2"),
        "successUrl": "https://grok.com/",
        "size": (520, 760),
    },
    "chatgpt": {
        "label": "ChatGPT",
        "homeUrl": "https://chatgpt.com/",
        "loginUrl": "https://chatgpt.com/auth/login",
        "authHosts": ("auth.openai.com", "chatgpt.com/auth", "auth0.openai.com"),
        "successUrl": "https://chatgpt.com/",
        "size": (480, 720),
    },
    "gemini": {
        "label": "Gemini",
        "homeUrl": "https://gemini.google.com/app",
        "loginUrl": "https://accounts.google.com/ServiceLogin?continue=https://gemini.google.com/app",
        "authHosts": ("accounts.google.com", "accounts.youtube.com"),
        "successUrl": "https://gemini.google.com/",
        "size": (520, 680),
    },
    "claude": {
        "label": "Claude",
        "homeUrl": "https://claude.ai/new",
        "loginUrl": "https://claude.ai/login",
        "authHosts": ("claude.ai/login", "auth0.com", "anthropic.com"),
        "successUrl": "https://claude.ai/",
        "size": (480, 720),
    },
}


# JS probe that runs against the loaded page and returns a small JSON dict.
# `loginLikely` mirrors the heuristic in app.py (auth domain in URL, or auth
# keywords in body text near the top). `scrollHeight`/`scrollWidth` measure the
# actual rendered page size so the caller can right-size the window.
AUTH_PROBE_JS = r"""
(function() {
  try {
    var url = String(location.href || '');
    var title = String(document.title || '');
    var bodyText = String((document.body && document.body.innerText) || '').slice(0, 4000);
    var lower = (url + '\n' + title + '\n' + bodyText).toLowerCase();
    var authHostHit = /(accounts\.x\.ai|auth\.openai\.com|auth0\.openai\.com|accounts\.google\.com|claude\.ai\/login|auth0\.com)/.test(lower);
    var authPathHit = /\/(login|signin|sign[-_]?in|auth|oauth)(\b|[\/?#])/.test(url.toLowerCase());
    var authTextHit = /\b(continue with google|sign in to continue|log in to continue|please sign in|please log in|session expired|access denied|authentication required)\b/.test(lower);
    var loginLikely = !!(authHostHit || authPathHit || authTextHit);
    return {
      url: url,
      title: title,
      loginLikely: loginLikely,
      scrollWidth: Math.max(document.documentElement.scrollWidth || 0, document.body && document.body.scrollWidth || 0),
      scrollHeight: Math.max(document.documentElement.scrollHeight || 0, document.body && document.body.scrollHeight || 0),
      innerWidth: Number(window.innerWidth || 0),
      innerHeight: Number(window.innerHeight || 0),
    };
  } catch (err) {
    return { url: String(location.href || ''), title: '', loginLikely: false, error: String(err) };
  }
})()
"""


def defaultProfileRoot(provider: str) -> Path:
    """Resolve the default persistent profile path for *provider*.

    Default: %USERPROFILE%/.supergrok/profiles/<provider>/
    Override: SUPERGROK_PROFILE_DIR env var (treated as the parent root).
    """
    override = (os.environ.get("SUPERGROK_PROFILE_DIR") or "").strip()
    base = Path(override).expanduser().resolve() if override else (Path.home() / ".supergrok" / "profiles")
    return base / provider


def migrateLegacyProfile(provider: str, newRoot: Path) -> bool:
    """Copytree the legacy `data/<provider>_profile/` into *newRoot* on first run.

    Non-destructive: leaves the legacy dir in place as a backup. Returns True
    on a real migration, False if there was nothing to migrate or the new
    root already has data.
    """
    if newRoot.exists() and any(newRoot.iterdir()):
        return False
    legacy = ROOT / "data" / f"{provider}_profile"
    if not legacy.exists():
        return False
    try:
        newRoot.mkdir(parents=True, exist_ok=True)
        # copy storage/ and cache/ subtrees so the WebEngine cookies + cache survive.
        for sub in ("storage", "cache"):
            src = legacy / sub
            if src.exists():
                shutil.copytree(src, newRoot / sub, dirs_exist_ok=True)
        return True
    except Exception:
        return False


def buildLoginProfile(provider: str, parent: Any, profileDirOverride: str = "") -> tuple[Any, Path]:
    """Create a persistent QWebEngineProfile for *provider*.

    Returns (profile, profileRoot). Cookies + cache are persistent on disk so
    the SAME profile can be reused by the full SuperGrok bridge later.
    """
    from PySide6.QtWebEngineCore import QWebEngineProfile  # late import — Qt may not be available in --probe contexts

    profileRoot = Path(profileDirOverride).expanduser().resolve() if profileDirOverride else defaultProfileRoot(provider)
    migrated = migrateLegacyProfile(provider, profileRoot)
    storagePath = profileRoot / "storage"
    cachePath = profileRoot / "cache"
    storagePath.mkdir(parents=True, exist_ok=True)
    cachePath.mkdir(parents=True, exist_ok=True)

    label = PROVIDERS.get(provider, {}).get("label", provider.title())
    profile = QWebEngineProfile(f"SuperGrokLogin{label}Profile", parent)
    profile.setPersistentStoragePath(str(storagePath))
    profile.setCachePath(str(cachePath))
    try:
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    except Exception:  # swallow-ok: older PySide6 enum casing varies
        pass
    try:
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    except Exception:
        pass
    if migrated:
        print(f"[login-bridge] migrated legacy profile data/{provider}_profile/ -> {profileRoot}", file=sys.stderr, flush=True)
    return profile, profileRoot


class LoginOnlyBridgeWindow:
    """Stripped-down single-WebEngineView login window.

    Constructed via `openLoginWindow(provider)` which handles app bootstrap.
    Closes when the post-load JS probe says `loginLikely == False` AND the
    URL host matches the provider's home host (i.e. auth completed and the
    redirect bounced us back to the app).
    """

    def __init__(self, provider: str, profileDir: str = "", onClose: Callable[[dict[str, Any]], None] | None = None) -> None:
        from PySide6.QtCore import Qt, QUrl, QTimer
        from PySide6.QtWidgets import QMainWindow, QStatusBar
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEnginePage

        provider = (provider or "grok").lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}")
        cfg = PROVIDERS[provider]
        self.provider = provider
        self.label = cfg["label"]
        self.successUrl = cfg["successUrl"]
        self.authHosts = tuple(cfg["authHosts"])
        self.onClose = onClose
        self.lastProbe: dict[str, Any] = {}
        self._closing = False

        self.window = QMainWindow()
        self.window.setWindowTitle(f"Sign in to {self.label}")
        width, height = cfg["size"]
        self.window.resize(int(width), int(height))

        self.profile, self.profileRoot = buildLoginProfile(provider, self.window, profileDir)
        self.view = QWebEngineView(self.window)
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        self.window.setCentralWidget(self.view)
        self.window.setStatusBar(QStatusBar(self.window))
        self.window.statusBar().showMessage(f"Profile: {self.profileRoot}")

        # Navigate to the login URL directly so the user lands on the form
        # without an extra home-page bounce.
        self.view.load(QUrl(cfg["loginUrl"]))

        # Run the JS probe on every load-finished. When the probe says
        # logged-in (loginLikely==False AND URL is back on the app host),
        # close the window — auth persisted to the profile.
        self.view.loadFinished.connect(self._onLoadFinished)
        self._probeTimer = QTimer(self.window)
        self._probeTimer.setInterval(750)
        self._probeTimer.timeout.connect(self._runProbe)
        # Don't probe-poll forever; only after a load completes.

    def show(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _onLoadFinished(self, ok: bool) -> None:
        if not ok or self._closing:
            return
        # Probe immediately, then poll briefly for SPA-driven URL changes.
        self._runProbe()
        self._probeTimer.start()

    def _runProbe(self) -> None:
        if self._closing:
            return
        self.page.runJavaScript(AUTH_PROBE_JS, self._onProbeResult)

    def _onProbeResult(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        self.lastProbe = dict(payload)
        url = str(payload.get("url") or "").lower()
        loginLikely = bool(payload.get("loginLikely"))
        # Auth success heuristic: URL is no longer an auth host AND probe
        # says not-login. Some providers (grok, gemini) bounce through their
        # own host before the final SPA loads, so wait until both signals
        # agree.
        atAuthHost = any(host in url for host in self.authHosts)
        if not atAuthHost and not loginLikely:
            self._finish()

    def _finish(self) -> None:
        self._closing = True
        self._probeTimer.stop()
        try:
            self.window.close()
        except Exception:  # swallow-ok: window may already be torn down
            pass
        if callable(self.onClose):
            try:
                self.onClose(self.lastProbe)
            except Exception:
                pass


def openLoginWindow(provider: str, profileDir: str = "", block: bool = True) -> int:
    """Open the stripped login window for *provider* and run the Qt event loop.

    Returns 0 on success (window closed via auth completion or user close),
    non-zero on setup error. Use `block=False` for a launcher that wires the
    window into an already-running QApplication.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as importErr:
        print(f"[login-bridge] PySide6 import failed: {type(importErr).__name__}: {importErr}", file=sys.stderr, flush=True)
        return 2
    app = QApplication.instance() or QApplication(sys.argv)
    bridge = LoginOnlyBridgeWindow(provider=provider, profileDir=profileDir)
    bridge.show()
    if block:
        return int(app.exec())
    return 0


def probeAuth(provider: str, profileDir: str = "", timeoutSec: int = 20) -> dict[str, Any]:
    """Headless-ish probe: load provider home URL, run probe JS, return JSON.

    "Headless-ish" because QtWebEngine still composites a native surface on
    Windows for Chromium to hydrate; we move that surface off-screen instead
    of trying QT_QPA_PLATFORM=offscreen, which is unreliable for SPA pages.
    Returns a dict with: url, title, loginLikely, scrollWidth, scrollHeight,
    innerWidth, innerHeight, recommendedSize, provider, profileRoot.
    """
    try:
        from PySide6.QtCore import Qt, QUrl, QTimer
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEnginePage
    except Exception as importErr:
        return {"ok": False, "error": f"PySide6 import failed: {type(importErr).__name__}: {importErr}"}
    provider = (provider or "grok").lower()
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}"}

    app = QApplication.instance() or QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle(f"SuperGrok probe — {PROVIDERS[provider]['label']}")
    window.resize(900, 900)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.move(32000, 32000)  # off-screen but composited

    profile, profileRoot = buildLoginProfile(provider, window, profileDir)
    view = QWebEngineView(window)
    page = QWebEnginePage(profile, view)
    view.setPage(page)
    window.setCentralWidget(view)
    window.show()

    homeUrl = PROVIDERS[provider]["homeUrl"]
    view.load(QUrl(homeUrl))

    state: dict[str, Any] = {"done": False, "result": None}
    deadline = time.time() + max(2, int(timeoutSec or 20))

    def _onProbe(payload: Any) -> None:
        if isinstance(payload, dict):
            state["result"] = dict(payload)
            state["done"] = True

    def _onLoaded(ok: bool) -> None:
        if not ok:
            state["result"] = {"ok": False, "error": "page failed to load"}
            state["done"] = True
            return
        # Let the SPA hydrate a beat before probing.
        QTimer.singleShot(1200, lambda: page.runJavaScript(AUTH_PROBE_JS, _onProbe))

    view.loadFinished.connect(_onLoaded)

    while not state["done"] and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)

    result = state["result"] or {"ok": False, "error": "probe timed out"}
    if isinstance(result, dict):
        result.setdefault("ok", True)
        result["provider"] = provider
        result["profileRoot"] = str(profileRoot)
        # Recommended window size: scrollHeight/Width + small margin, capped to
        # 1024 wide / 900 tall so we never produce an oversized login chrome.
        sw = int(result.get("scrollWidth") or 0) + 24
        sh = int(result.get("scrollHeight") or 0) + 48
        result["recommendedSize"] = [min(1024, max(380, sw)), min(900, max(480, sh))]
    try:
        window.close()
    except Exception:  # swallow-ok
        pass
    return result


def runCli(action: str, provider: str, profileDir: str = "") -> int:
    """Top-level dispatcher used by start.py.

    action ∈ {"login", "probe-auth"}.
    """
    action = (action or "").strip().lower()
    provider = (provider or "grok").lower()
    if provider not in PROVIDERS:
        print(f"[login-bridge] unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}", file=sys.stderr, flush=True)
        return 2
    if action == "login":
        return openLoginWindow(provider, profileDir=profileDir, block=True)
    if action == "probe-auth":
        result = probeAuth(provider, profileDir=profileDir)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok", False) else 1
    print(f"[login-bridge] unknown action {action!r}; expected 'login' or 'probe-auth'", file=sys.stderr, flush=True)
    return 2


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SuperGrok stripped login-only bridge")
    ap.add_argument("--action", choices=["login", "probe-auth"], required=True)
    ap.add_argument("--target", default="grok")
    ap.add_argument("--profile-dir", default="")
    ns = ap.parse_args()
    sys.exit(runCli(ns.action, ns.target, ns.profile_dir))
