# ============================================================================
#  gh_pipeline.py — AI-driven GitHub publishing pipeline
#  ---------------------------------------------------------------------------
#  Stdlib-only PAT-based wrapper over the GitHub REST API + a release content
#  generator that drives ChatGPT through the SuperGrok bridge CLI to produce
#  every field a GH project has (repo description / topics / README / LICENSE /
#  release name / release notes / source-file header comment).
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

import configparser
import datetime
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# GitHub field hard limits (verified against docs.github.com + community
# discussions, May 2026). Used inside the ChatGPT prompt so the model never
# generates content the API will reject.
# ---------------------------------------------------------------------------
GH_LIMITS: dict[str, int] = {
    "repo_name": 100,
    "repo_description": 350,
    "topic": 50,
    "max_topics_per_repo": 20,
    "release_name": 256,
    "release_tag": 256,
    "release_body": 125_000,
    "issue_title": 256,
    "issue_body": 65_536,
    "pr_body": 65_536,
    "issue_comment_bytes": 262_144,
    "org_name": 39,
    "user_bio": 160,
    "profile_name": 255,
    "team_name": 255,
}

GH_API = "https://api.github.com"
GH_UA = "supergrok-pipeline/1.0 (+https://github.com/tibberous/SuperGrok)"
MIT_LICENSE_TEMPLATE = """MIT License

Copyright (c) {year} {name}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class GhPipelineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Config loader — finds config.ini next to the project, falls back to env vars
# ---------------------------------------------------------------------------

def loadConfig(projectRoot: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    candidates = [
        projectRoot / "config.ini",
        projectRoot / "config.local.ini",
        Path("C:/triodesktop/config.ini"),
    ]
    for path in candidates:
        if path.exists():
            cfg.read(path, encoding="utf-8")
            break
    return cfg


def getPat(projectRoot: Path) -> str:
    cfg = loadConfig(projectRoot)
    pat = ""
    if cfg.has_option("github", "pat"):
        pat = (cfg.get("github", "pat") or "").strip()
    if not pat:
        pat = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not pat:
        raise GhPipelineError(
            "no GitHub PAT found. add it to config.ini under [github] pat = ghp_... "
            "or export GH_TOKEN before running this command."
        )
    return pat


def getGhUsername(projectRoot: Path, pat: str) -> str:
    cfg = loadConfig(projectRoot)
    if cfg.has_option("github", "username"):
        cached = (cfg.get("github", "username") or "").strip()
        if cached:
            return cached
    # Fall back to /user lookup so we can publish without a configured username.
    info = ghApi(pat, "GET", "/user")
    return str(info.get("login") or "")


def getAuthor(projectRoot: Path) -> dict[str, str]:
    cfg = loadConfig(projectRoot)
    out = {"name": "", "email": "", "phone": "", "consulting_blurb": ""}
    if cfg.has_section("author"):
        for key in out.keys():
            if cfg.has_option("author", key):
                out[key] = (cfg.get("author", key) or "").strip()
    return out


# ---------------------------------------------------------------------------
# GitHub REST helpers — stdlib only, no `requests` dependency
# ---------------------------------------------------------------------------

def ghApi(pat: str, method: str, path: str, payload: dict[str, Any] | list[Any] | None = None) -> Any:  # noqa: nonconform
    url = path if path.startswith("http") else f"{GH_API}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {pat}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", GH_UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if not body.strip():
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GhPipelineError(f"GH API {method} {path} failed: HTTP {exc.code} — {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise GhPipelineError(f"GH API {method} {path} network error: {exc}") from exc


# ---------------------------------------------------------------------------
# Repo / Release / Branch / Topic operations
# ---------------------------------------------------------------------------

def listRepos(pat: str, user: str = "", visibility: str = "all") -> list[dict[str, Any]]:
    """List authenticated user's repos (all visibilities) or a public user's repos."""
    if user:
        return ghApi(pat, "GET", f"/users/{urllib.parse.quote(user)}/repos?per_page=100&type=all")
    page = 1
    out: list[dict[str, Any]] = []
    while True:
        rows = ghApi(pat, "GET", f"/user/repos?per_page=100&page={page}&visibility={visibility}&affiliation=owner")
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    return out


def getRepo(pat: str, owner: str, repo: str) -> dict[str, Any] | None:
    try:
        return ghApi(pat, "GET", f"/repos/{owner}/{repo}")
    except GhPipelineError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def createRepo(pat: str, name: str, description: str, homepage: str, public: bool, autoInit: bool = False) -> dict[str, Any]:
    payload = {
        "name": name[: GH_LIMITS["repo_name"]],
        "description": description[: GH_LIMITS["repo_description"]],
        "homepage": homepage,
        "private": (not public),
        "has_issues": True,
        "has_projects": True,
        "has_wiki": True,
        "auto_init": autoInit,
    }
    return ghApi(pat, "POST", "/user/repos", payload)


def updateRepo(pat: str, owner: str, repo: str, fields: dict[str, Any]) -> dict[str, Any]:
    return ghApi(pat, "PATCH", f"/repos/{owner}/{repo}", fields)


def setTopics(pat: str, owner: str, repo: str, topics: list[str]) -> dict[str, Any]:
    clean: list[str] = []
    for raw in topics:
        slug = re.sub(r"[^a-z0-9-]+", "-", str(raw or "").strip().lower()).strip("-")
        if slug and slug not in clean:
            clean.append(slug[: GH_LIMITS["topic"]])
        if len(clean) >= GH_LIMITS["max_topics_per_repo"]:
            break
    return ghApi(pat, "PUT", f"/repos/{owner}/{repo}/topics", {"names": clean})


def listReleases(pat: str, owner: str, repo: str) -> list[dict[str, Any]]:
    return ghApi(pat, "GET", f"/repos/{owner}/{repo}/releases?per_page=100")


def createRelease(pat: str, owner: str, repo: str, tag: str, name: str, body: str, prerelease: bool = False, target: str = "main") -> dict[str, Any]:
    payload = {
        "tag_name": tag[: GH_LIMITS["release_tag"]],
        "name": (name or tag)[: GH_LIMITS["release_name"]],
        "body": (body or "")[: GH_LIMITS["release_body"]],
        "draft": False,
        "prerelease": bool(prerelease),
        "target_commitish": target,
    }
    return ghApi(pat, "POST", f"/repos/{owner}/{repo}/releases", payload)


def listBranches(pat: str, owner: str, repo: str) -> list[dict[str, Any]]:
    return ghApi(pat, "GET", f"/repos/{owner}/{repo}/branches?per_page=100")


# ---------------------------------------------------------------------------
# Local git helpers
# ---------------------------------------------------------------------------

def runGit(projectRoot: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(projectRoot), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")  # lifecycle-bypass-ok: short-lived git invocation
    if check and proc.returncode != 0:
        raise GhPipelineError(f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def gitStatus(projectRoot: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"branch": "", "remote": "", "ahead": 0, "behind": 0, "dirty": False, "untracked": []}
    if not (projectRoot / ".git").exists():
        out["is_git_repo"] = False
        return out
    out["is_git_repo"] = True
    try:
        out["branch"] = runGit(projectRoot, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    except GhPipelineError:
        out["branch"] = ""
    try:
        out["remote"] = runGit(projectRoot, "remote", "get-url", "origin", check=False).stdout.strip()
    except GhPipelineError:
        out["remote"] = ""
    porcelain = runGit(projectRoot, "status", "--porcelain", check=False).stdout.splitlines()
    out["dirty"] = any(line.strip() for line in porcelain)
    out["untracked"] = [line[3:] for line in porcelain if line.startswith("??")]
    return out


def gitCommitAll(projectRoot: Path, message: str) -> int:
    runGit(projectRoot, "add", "-A")
    proc = runGit(projectRoot, "commit", "-m", message, check=False)
    return proc.returncode


def gitPush(projectRoot: Path, remote: str = "origin", branch: str = "") -> int:
    if not branch:
        branch = runGit(projectRoot, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    proc = runGit(projectRoot, "push", "-u", remote, branch, check=False)
    return proc.returncode


def gitTag(projectRoot: Path, tag: str, message: str = "") -> int:
    proc = runGit(projectRoot, "tag", "-a", tag, "-m", message or tag, check=False)
    return proc.returncode


def gitPushTag(projectRoot: Path, tag: str, remote: str = "origin") -> int:
    proc = runGit(projectRoot, "push", remote, tag, check=False)
    return proc.returncode


def ensureGitRepo(projectRoot: Path) -> None:
    if not (projectRoot / ".git").exists():
        runGit(projectRoot, "init", "-b", "main")
        if not (projectRoot / ".gitignore").exists():
            (projectRoot / ".gitignore").write_text("config.ini\nconfig.local.ini\n__pycache__/\n*.pyc\n*.log\n.venv/\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-file header refresh — bumps the "Codex by ..." line in every Python
# file at the project root that already carries the SuperGrok banner.
# ---------------------------------------------------------------------------

HEADER_BANNER_RE = re.compile(
    r"^(#\s*={60,}.*?#\s*={60,})",
    re.DOTALL | re.MULTILINE,
)


def buildHeader(projectName: str, oneLineDesc: str, author: dict[str, str], githubUrl: str, codexCredit: str) -> str:
    name = author.get("name", "Your Name")
    email = author.get("email", "")
    phone = author.get("phone", "")  # noqa: redundant
    blurb = author.get("consulting_blurb") or ""
    if phone and not blurb:
        blurb = f"Need help on your next project? Call me at {phone} for a free consultation!"
    lines = [
        "# " + "=" * 76,
        f"#  {projectName}",
        "#  " + "-" * 73,
    ]
    for chunk in textwrap.wrap(oneLineDesc, width=73):
        lines.append(f"#  {chunk}")
    lines.append("#")
    lines.append(f"#  Author : {name}  <{email}>")
    if phone:
        lines.append(f"#  Phone  : {phone}")
    if githubUrl:
        lines.append(f"#  GitHub : {githubUrl}")
    if blurb:
        lines.append("#")
        for chunk in textwrap.wrap(blurb, width=73):
            lines.append(f"#  {chunk}")
    if codexCredit:
        lines.append("#")
        lines.append(f"#  {codexCredit}")
    lines.append("# " + "=" * 76)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ChatGPT bridge integration — calls `python <supergrok>/start.py --chatgpt`
# and parses the JSON response. Requires that the user is already logged into
# ChatGPT in the SuperGrok bridge profile.
# ---------------------------------------------------------------------------

def findSuperGrokStart(projectRoot: Path) -> Path | None:
    candidates = [
        projectRoot / "start.py",
        Path("C:/SuperGrok/start.py"),
        Path.home() / "SuperGrok" / "start.py",
    ]
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")[:1500]
            if "SuperGrok Bridge" in text or "--chatgpt" in text:
                return path
    return None


def askChatGpt(prompt: str, supergrokStart: Path, timeoutSec: int = 240) -> str:
    """Send a single prompt to ChatGPT via the SuperGrok bridge and return stdout text."""
    if not supergrokStart.exists():
        raise GhPipelineError(f"SuperGrok start.py not found at {supergrokStart}; cannot drive ChatGPT.")
    cmd = [sys.executable, str(supergrokStart), "--chatgpt", prompt, "--chat-timeout", str(timeoutSec)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeoutSec + 30)  # lifecycle-bypass-ok: drives ChatGPT bridge CLI
    if proc.returncode != 0:
        raise GhPipelineError(f"ChatGPT bridge failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout


def extractJson(text: str) -> dict[str, Any]:
    """Extract the first {...} JSON object from a free-form text response."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace:
        return json.loads(brace.group(1))
    raise GhPipelineError(f"could not find a JSON object in ChatGPT response (first 400 chars): {text[:400]!r}")


# ---------------------------------------------------------------------------
# Release content generator — builds the ChatGPT prompt, sends it, parses,
# returns a structured dict with every field the pipeline needs.
# ---------------------------------------------------------------------------

def requestReleaseContent(
    projectRoot: Path,
    projectName: str,
    summary: str,
    changeSummary: str,
    version: str,
    author: dict[str, str],
    repoUrl: str,
    supergrokStart: Path | None = None,
    license: str = "MIT",
) -> dict[str, Any]:
    """Ask ChatGPT to generate every GH field for a release, returned as a dict."""
    if supergrokStart is None:
        supergrokStart = findSuperGrokStart(projectRoot) or Path("C:/SuperGrok/start.py")
    schema = {
        "repo_description": f"<= {GH_LIMITS['repo_description']} chars",
        "repo_homepage": "URL, can be empty string",
        "topics": f"array of {GH_LIMITS['max_topics_per_repo']} lowercase hyphenated tags, each <= {GH_LIMITS['topic']} chars",
        "readme_md": "full GitHub-flavored markdown README, no hard limit but aim for 1500-6000 chars; include badges, install, usage, screenshots-section, contributing, license, author",
        "readme_txt": "plain-text mirror of the README (no markdown, fewer headings)",
        "license_txt": (f"full {license} license text. If {license} == MIT, fill copyright with {author.get('name','')} {datetime.date.today().year}."),
        "release_tag": "semver-style tag like v1.0.0",
        "release_name": f"<= {GH_LIMITS['release_name']} chars",
        "release_body": f"GitHub release notes in markdown, <= {GH_LIMITS['release_body']} chars but aim for 1500-5000 chars. Sections: Highlights, What's New, Bug Fixes, Breaking Changes, Install, Coming Next, Author",
        "header_comment": "10-15 line block comment to drop at the top of source files. Should include project name, one-line description, author/email/phone/github/consulting blurb/Codex credit. Use # prefixes (Python comment style).",
        "social_preview_alt": "1-sentence description suitable for Open Graph preview",
        "tagline": f"<= 120 chars marketing-style one-liner",
    }
    promptParts = [
        f"You are generating publishing assets for an open-source project called {projectName!r}.",
        f"Version: {version}.",
        f"Project root: {projectRoot}.",
        f"Repo URL: {repoUrl}.",
        f"License: {license}.",
        "",
        "Author/contact info (use exactly as written):",
        f"  Name : {author.get('name','')}",
        f"  Email: {author.get('email','')}",
        f"  Phone: {author.get('phone','')}",
        f"  Blurb: {author.get('consulting_blurb','')}",
        "",
        "Project summary (use as ground truth):",
        summary,
        "",
        "What changed in this release:",
        changeSummary or "(initial release)",
        "",
        "Respond with ONLY a single JSON object (no prose, no markdown fences). The JSON must have these keys (and stay within the documented GitHub limits):",
        json.dumps(schema, indent=2),
        "",
        "Hard rules:",
        f"- repo_description: ASCII printable, no newlines, <= {GH_LIMITS['repo_description']} characters.",
        f"- topics: array of exactly {GH_LIMITS['max_topics_per_repo']} unique lowercase slugs (a-z, 0-9, hyphen), each <= {GH_LIMITS['topic']} characters, no leading/trailing hyphens.",
        f"- release_body: GitHub-flavored markdown, <= {GH_LIMITS['release_body']} characters.",
        f"- release_tag: starts with 'v' and matches /^v\\d+\\.\\d+\\.\\d+/.",
        f"- license_txt: full text of the {license} license with year={datetime.date.today().year} and copyright holder={author.get('name','')!r}.",
        "- All strings are UTF-8. Use straight quotes only (no smart quotes).",
        "- The Codex credit line `Codex by Claude Opus 4.7 and ChatGPT 5.5.` MUST appear at the bottom of header_comment, readme_md, readme_txt, and release_body.",
        "- If you cannot fit a field within its limit, truncate cleanly at a sentence boundary.",
        "Respond now with ONLY the JSON object.",
    ]
    prompt = "\n".join(promptParts)
    raw = askChatGpt(prompt, supergrokStart)
    return extractJson(raw)


# ---------------------------------------------------------------------------
# Top-level CLI handlers — start.py dispatches into these
# ---------------------------------------------------------------------------

def cliListRepos(projectRoot: Path, user: str = "") -> int:
    pat = getPat(projectRoot)
    repos = listRepos(pat, user=user)
    for repo in repos:
        visibility = "private" if repo.get("private") else "public"
        stars = repo.get("stargazers_count", 0)
        print(f"{repo.get('full_name','?'):<50}  {visibility:<8}  stars={stars:<5}  {(repo.get('description') or '')[:80]}")
    return 0


def cliStatus(projectRoot: Path) -> int:
    pat = getPat(projectRoot)
    gs = gitStatus(projectRoot)
    print(json.dumps({"git": gs}, indent=2))
    if gs.get("remote"):
        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", gs["remote"])
        if match:
            owner, repo = match.group(1), match.group(2)
            info = getRepo(pat, owner, repo)
            if info:
                print(json.dumps({"github": {"full_name": info.get("full_name"), "visibility": "private" if info.get("private") else "public", "topics": info.get("topics", []), "description": info.get("description"), "homepage": info.get("homepage"), "stars": info.get("stargazers_count"), "default_branch": info.get("default_branch")}}, indent=2))
                releases = listReleases(pat, owner, repo)
                latest = releases[0] if releases else None
                if latest:
                    print(json.dumps({"latest_release": {"tag": latest.get("tag_name"), "name": latest.get("name"), "published_at": latest.get("published_at")}}, indent=2))
    return 0


def cliPublish(projectRoot: Path, public: bool = True, projectName: str = "", description: str = "", version: str = "", interactive: bool = True, autoGenerate: bool = True) -> int:
    pat = getPat(projectRoot)
    ghUser = getGhUsername(projectRoot, pat)
    author = getAuthor(projectRoot)
    if not projectName:
        projectName = projectRoot.name
    ensureGitRepo(projectRoot)
    gs = gitStatus(projectRoot)
    print(f"[gh] project={projectName} branch={gs.get('branch')} dirty={gs.get('dirty')} remote={gs.get('remote') or '(none)'}")
    if gs.get("dirty"):
        msg = f"chore: publish {projectName} {version or ''}".strip()
        if interactive:
            print(f"[gh] working tree dirty — committing all changes with message: {msg}")
        gitCommitAll(projectRoot, msg)
    remote = gs.get("remote") or ""
    needsCreate = not remote
    if not needsCreate:
        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", remote)
        if match:
            owner, repo = match.group(1), match.group(2)
            if getRepo(pat, owner, repo) is None:
                needsCreate = True
    if needsCreate:
        print(f"[gh] creating new repo {ghUser}/{projectName} (public={public})")
        info = createRepo(pat, projectName, (description or projectName)[:GH_LIMITS["repo_description"]], "", public)
        runGit(projectRoot, "remote", "remove", "origin", check=False)
        runGit(projectRoot, "remote", "add", "origin", info.get("clone_url", "") or f"https://github.com/{ghUser}/{projectName}.git")
    rc = gitPush(projectRoot)
    if rc != 0:
        return rc
    print("[gh] push complete.")
    if interactive:
        answer = (input("[gh] publish a new release? [y/N] ").strip().lower() or "n")
        if answer not in ("y", "yes"):
            print("[gh] skipping release.")
            return 0
    if autoGenerate:
        version = version or "v1.0.0"
        print(f"[gh] asking ChatGPT to generate release content for {projectName} {version}…")
        repoUrl = f"https://github.com/{ghUser}/{projectName}"
        content = requestReleaseContent(
            projectRoot=projectRoot,
            projectName=projectName,
            summary=description or projectName,
            changeSummary="",
            version=version,
            author=author,
            repoUrl=repoUrl,
        )
        writeArtifacts(projectRoot, content)
        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", gitStatus(projectRoot).get("remote", ""))
        if match:
            owner, repo = match.group(1), match.group(2)
            tag = str(content.get("release_tag") or version)
            name = str(content.get("release_name") or tag)
            body = str(content.get("release_body") or "")
            try:
                setTopics(pat, owner, repo, list(content.get("topics") or []))
            except GhPipelineError as exc:
                print(f"[gh] topics update skipped: {exc}")
            try:
                updateRepo(pat, owner, repo, {
                    "description": str(content.get("repo_description") or description or projectName)[:GH_LIMITS["repo_description"]],
                    "homepage": str(content.get("repo_homepage") or repoUrl),
                })
            except GhPipelineError as exc:
                print(f"[gh] repo metadata update skipped: {exc}")
            gitCommitAll(projectRoot, f"docs: refresh README/LICENSE for {tag}")
            gitPush(projectRoot)
            gitTag(projectRoot, tag, name)
            gitPushTag(projectRoot, tag)
            release = createRelease(pat, owner, repo, tag, name, body)
            print(f"[gh] release published: {release.get('html_url')}")
    return 0


def writeArtifacts(projectRoot: Path, content: dict[str, Any]) -> None:
    mapping = {
        "readme_md": projectRoot / "README.md",
        "readme_txt": projectRoot / "README.txt",
        "license_txt": projectRoot / "LICENSE.txt",
    }
    for key, path in mapping.items():
        text = content.get(key)
        if isinstance(text, str) and text.strip():
            path.write_text(text, encoding="utf-8")
            print(f"[gh] wrote {path.name} ({len(text)} chars)")
    if not (projectRoot / "LICENSE").exists() and (projectRoot / "LICENSE.txt").exists():
        try:
            (projectRoot / "LICENSE").write_text((projectRoot / "LICENSE.txt").read_text(encoding="utf-8"), encoding="utf-8")
            print("[gh] mirrored LICENSE.txt -> LICENSE")
        except Exception:  # swallow-ok
            pass
    # release.md kept as a project artifact (handy for changelog history).
    release_md = projectRoot / "release.md"
    body = content.get("release_body") or ""
    if isinstance(body, str) and body.strip():
        release_md.write_text(body, encoding="utf-8")
        print(f"[gh] wrote release.md ({len(body)} chars)")


def cliCreateRelease(projectRoot: Path, version: str = "", auto: bool = True, projectName: str = "", description: str = "") -> int:
    pat = getPat(projectRoot)
    ghUser = getGhUsername(projectRoot, pat)
    author = getAuthor(projectRoot)
    if not projectName:
        projectName = projectRoot.name
    if not version:
        version = "v1.0.0"
    gs = gitStatus(projectRoot)
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", gs.get("remote", ""))
    if not match:
        raise GhPipelineError("no GitHub remote configured. run --gh-publish first.")
    owner, repo = match.group(1), match.group(2)
    repoUrl = f"https://github.com/{owner}/{repo}"
    if auto:
        print(f"[gh] asking ChatGPT to generate release content for {projectName} {version}…")
        content = requestReleaseContent(
            projectRoot=projectRoot,
            projectName=projectName,
            summary=description or projectName,
            changeSummary="",
            version=version,
            author=author,
            repoUrl=repoUrl,
        )
        writeArtifacts(projectRoot, content)
        tag = str(content.get("release_tag") or version)
        name = str(content.get("release_name") or tag)
        body = str(content.get("release_body") or "")
    else:
        tag = version
        name = version
        body = (projectRoot / "release.md").read_text(encoding="utf-8") if (projectRoot / "release.md").exists() else f"## {projectName} {version}\n\n(no notes)\n"
    try:
        setTopics(pat, owner, repo, list((content.get("topics") if auto else None) or []))
    except Exception:  # swallow-ok
        pass
    gitCommitAll(projectRoot, f"docs: refresh release artifacts for {tag}")
    gitPush(projectRoot)
    gitTag(projectRoot, tag, name)
    gitPushTag(projectRoot, tag)
    release = createRelease(pat, owner, repo, tag, name, body)
    print(f"[gh] release published: {release.get('html_url')}")
    return 0


def cliWriteLicense(projectRoot: Path, kind: str = "MIT") -> int:
    author = getAuthor(projectRoot)
    if kind.upper() == "MIT":
        text = MIT_LICENSE_TEMPLATE.format(year=datetime.date.today().year, name=author.get("name", "Your Name"))
    else:
        raise GhPipelineError(f"unsupported license: {kind}. supported: MIT")
    (projectRoot / "LICENSE").write_text(text, encoding="utf-8")
    (projectRoot / "LICENSE.txt").write_text(text, encoding="utf-8")
    print(f"[gh] wrote MIT LICENSE / LICENSE.txt ({len(text)} chars)")
    return 0


# ---------------------------------------------------------------------------
# Public façade — start.py calls into this single dispatcher.
# ---------------------------------------------------------------------------

def dispatch(args: Any, projectRoot: Path, projectName: str = "", description: str = "") -> int | None:
    """Return an exit code if a --gh-* flag matched, else None so start.py continues."""
    if getattr(args, "gh_list_repos", False):
        return cliListRepos(projectRoot, user=getattr(args, "gh_user", "") or "")
    if getattr(args, "gh_status", False):
        return cliStatus(projectRoot)
    if getattr(args, "gh_license", ""):
        return cliWriteLicense(projectRoot, kind=str(getattr(args, "gh_license", "MIT")) or "MIT")
    if getattr(args, "gh_release", False):
        return cliCreateRelease(projectRoot, version=str(getattr(args, "gh_version", "") or ""), auto=not bool(getattr(args, "gh_no_ai", False)), projectName=projectName, description=description)
    if getattr(args, "gh_publish", False):
        public = not bool(getattr(args, "gh_private", False))
        return cliPublish(projectRoot, public=public, projectName=projectName, description=description, version=str(getattr(args, "gh_version", "") or ""), interactive=not bool(getattr(args, "gh_yes", False)), autoGenerate=not bool(getattr(args, "gh_no_ai", False)))
    return None


def addArgparseFlags(parser: Any) -> None:
    """Attach the --gh-* flag set to an argparse parser."""
    parser.add_argument("--gh-publish", action="store_true", help="Auto-publish: ensure GH remote exists (create if not), commit/push, then optionally drive ChatGPT to generate release content and cut a release.")
    parser.add_argument("--gh-list-repos", action="store_true", help="List your GitHub repos via the PAT in config.ini.")
    parser.add_argument("--gh-status", action="store_true", help="Print local git status + remote GitHub metadata (visibility, topics, latest release).")  # noqa: redundant
    parser.add_argument("--gh-release", action="store_true", help="Create a GitHub release. With --gh-no-ai, uses release.md verbatim; otherwise asks ChatGPT to generate every field.")  # noqa: redundant
    parser.add_argument("--gh-license", default="", help="Write a LICENSE / LICENSE.txt of the given kind (MIT supported) and exit.")
    parser.add_argument("--gh-version", default="", help="Version tag for --gh-publish / --gh-release. Defaults to v1.0.0.")  # noqa: redundant
    parser.add_argument("--gh-private", action="store_true", help="Make the repo private when --gh-publish creates a new one. Default: public.")
    parser.add_argument("--gh-yes", action="store_true", help="Non-interactive: skip the 'publish release?' prompt.")  # noqa: redundant
    parser.add_argument("--gh-no-ai", action="store_true", help="Disable ChatGPT content generation; use existing README.md / release.md verbatim.")  # noqa: redundant
    parser.add_argument("--gh-user", default="", help="List repos for a specific GitHub user (default: authenticated user).")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="gh_pipeline standalone CLI")
    addArgparseFlags(ap)
    ap.add_argument("--root", default=".", help="Project root (default: cwd).")
    ap.add_argument("--name", default="", help="Override project name (default: dir name).")
    ap.add_argument("--desc", default="", help="Project description fallback (used if no AI generation).")  # noqa: redundant
    args = ap.parse_args()
    root = Path(args.root).resolve()
    code = dispatch(args, root, projectName=args.name, description=args.desc)
    sys.exit(int(code) if code is not None else 0)
