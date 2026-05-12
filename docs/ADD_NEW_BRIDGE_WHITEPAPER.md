# Adding a New Web-Bridge Provider to SuperGrok — Whitepaper

**Audience:** an agent or engineer adding a new chat target (Claude / Copilot / Azure / future) to the SuperGrok stack.

**Last updated:** 2026-05-11. Reflects the post-flatten layout (no `supergrok_bridge/` subfolder) and the Gemini + Claude bridges as reference implementations.

---

## 1. Architecture in one paragraph

SuperGrok is a PySide6 / Qt WebEngine app at `C:\SuperGrok\` that boots a hidden Qt window, loads a target site (e.g. `https://gemini.google.com/app`) inside a `QWebEngineView` with a persistent profile, and exposes a TCP service on `127.0.0.1:8767` that accepts JSON commands like `{action: "chat", target: "gemini", message: "..."}`. The chat handler injects JavaScript into the page (`page.runJavaScript(buildGrokDomSendScript(msg, sendId, target=...))`), which fills the composer using a React-native-setter trick, clicks the submit button, then polls the DOM for the assistant's reply. The reply is captured and returned over TCP. The same service can be hit by:

- the SuperGrok CLI (`python start.py --chat <target> "msg"`),
- a Python hook on the Desktop (`Desktop\claude\hooks\<target>_bridge_hook.py`),
- the Claude Codex Black VSCode extension (`Desktop\Claude Codex Black\` — the `superGrok: true` providers).

One service answers one target at a time. To switch targets you stop the running service and start another.

---

## 2. Files to modify (FOUR places + ONE new file)

| File | What changes |
|---|---|
| `C:\SuperGrok\start.py` | Add CLI aliases, argparse flag, login-bridge config, dispatch hook |
| `C:\SuperGrok\app.py` | Add server-side alias/label/URL funcs, target validation, DOM selectors |
| `C:\Users\moren\Desktop\claude\hooks\<target>_bridge_hook.py` | NEW — clone `gemini_bridge_hook.py`, swap target |
| `C:\Users\moren\Desktop\Claude Codex Black\extension.js` | Add provider entry to `PROVIDERS` registry |
| `C:\Users\moren\Desktop\Claude Codex Black\panel\index.html` | Add target to the `niceName` map in settings modal |

---

## 3. The pattern, in 10 anchor-based edits

The reference implementations (Gemini and Claude already done) give you the exact text patterns. Use these as templates. **Each edit is anchor-based** — find the existing line, add the new one after it. This lets parallel work proceed without line-number drift.

### 3.1 start.py — DEFAULT URL constant

**Anchor:** `DEFAULT_GEMINI_URL = "https://gemini.google.com/app"` (or `DEFAULT_CLAUDE_URL = "https://claude.ai/new"` if it exists).

**Add:** `DEFAULT_<TARGET>_URL = "<home url>"` immediately after the last existing default URL.

### 3.2 start.py — alias sets

**Anchor:** the line containing `GEMINI_TARGET_ALIASES = {...}` (or any existing TARGET_ALIASES line).

**Add:** `<TARGET>_TARGET_ALIASES = {"<canonical>", "<short>", ...}` right after. Then update `ALL_CHAT_TARGET_ALIASES = ... | <TARGET>_TARGET_ALIASES`.

Also add `<TARGET>_CLI_FLAG_ALIASES = {"--<target>", "-<target>", "/<target>"}` and include in `CHAT_CLI_FLAG_ALIASES = {"--chat", "-chat", "/chat"} | ... | <TARGET>_CLI_FLAG_ALIASES`.

### 3.3 start.py — normalize/url/look-like funcs

Three small additions inside existing functions. Use ELIF chains:

```python
def normalizeChatTarget(value):
    ...
    if target in <TARGET>_TARGET_ALIASES:
        return "<target>"

def defaultUrlForChatTarget(target):
    t = normalizeChatTarget(target)
    ...
    if t == "<target>":
        return DEFAULT_<TARGET>_URL

def urlLooksLikeTarget(url, target):
    ...
    if wanted == "<target>":
        return "<host>" in text  # e.g. "claude.ai" or "copilot.microsoft.com"
```

### 3.4 start.py — argparse flag

**Anchor:** `parser.add_argument("--gemini", "--gem", "--bard", dest="gemini", ...)`.

**Add:** `parser.add_argument("--<target>", "--<alias>", dest="<target>", nargs="*", default=None, help="Send a CLI chat request through <Target> at <home url>. With no message, opens the visible <Target> bridge so you can log in.")`.

Also update `parser.add_argument("--target", choices=[...])` to include your target.

### 3.5 start.py — login-bridge trio

**Anchor:** `def forceGeminiChatParts(parts):`.

Clone the three functions (rename gemini→target everywhere) — `<target>BridgeLoginRequested`, `configure<Target>LoginBridgeArgs`, `force<Target>ChatParts`. Don't forget `args.<target> = []` inside the configure function.

### 3.6 start.py — `<target>FlagPresent` + `activeChatArgName` + `normalizeChatModeArgs` + main() dispatch

Four anchors:

1. Beside `geminiFlagPresent`: add `def <target>FlagPresent(argv):` returning the alias-set probe.
2. In `activeChatArgName`: add an `if getattr(args, "<target>", None) is not None: return "<target>"`.
3. In `normalizeChatModeArgs`: add a branch reading args.<target> when args.chat is None.
4. In `main()`: alongside `gemini_login_bridge = geminiBridgeLoginRequested(args)`, add `<target>_login_bridge = <target>BridgeLoginRequested(args)` and an `elif` branch that calls `configure<Target>LoginBridgeArgs(args)`.

### 3.7 start.py — failure-mode hint + save-as label

Two small anchors:
- The `target_for_hint == "gemini":` block → add a `target_for_hint == "<target>":` block printing helpful login hints.
- `chatProviderLabelLocal`: add `if t == "<target>": return "<Label>"`.

### 3.8 app.py — alias/label/URL funcs

Mirror what you did in start.py. Four small ELIF additions inside `normalizeChatTarget`, `chatTargetFromUrl`, `chatProviderLabel`, `chatProviderHomeUrl`.

### 3.9 app.py — bridge service target validation

**Anchor:** `if target not in {"grok", "chatgpt", "gemini", "claude"}:`.

**Replace:** add your target to the set. **THIS IS EASY TO MISS** — without it the service rejects your chat with `unsupported target`.

### 3.10 app.py — DOM selectors (the meat)

Two locations in `buildGrokDomSendScript`:

**`__findEditorRow()`** — add a `<target>Selectors` array with composer selectors, then a branch:
```js
else if (__target === '<target>') selectors = <target>Selectors.concat(generic);
```

**`__messages()` ELIF chain** — add a `if (__target === '<target>')` block with response selectors, then `return out`.

See section 5 below for selector-hunting guidance.

---

## 4. The hook — new file

Copy `C:\Users\moren\Desktop\claude\hooks\gemini_bridge_hook.py` → `<target>_bridge_hook.py`. Find/replace:
- `gemini` → `<target>` (lowercase, everywhere — docstring, log key, target param)
- `Gemini` → `<Target>` (Title case — docstring text)
- `Google` → `<provider company>` (e.g. Microsoft, Anthropic) in the docstring
- `gemini.google.com/app` → your home URL
- `claude_hook.py` reference in the "For direct API chat" line → keep if there's a direct API hook for your target

Public surface stays: `chat`, `chat_with_file`, `chat_to_file`, `chat_full`, `ensure_logged_in`, `status`, `is_running`. Don't add features specific to one provider here unless they genuinely need new function signatures — the SuperGrok-side already handles attachments and save-as.

---

## 5. CBE provider entry

**File:** `C:\Users\moren\Desktop\Claude Codex Black\extension.js`.

**Anchor:** the closing brace of the `claudeBridge` entry inside the `PROVIDERS = { ... }` block.

**Add after it:**
```js
<target>Bridge: {
    label: '<Target> (SuperGrok)',
    superGrok: true,
    target: '<target>',
    superGrokRoot: 'C:\\SuperGrok',
    defaultModel: '(web)',
    models: ['(web)'],
},
```

**Then:** `C:\Users\moren\Desktop\Claude Codex Black\panel\index.html` — anchor on `prov.id === 'claudeBridge' ? 'Claude'   :` and add:
```js
prov.id === '<target>Bridge' ? '<Target>'   :
```

**Bump** `package.json` version one minor (e.g. 1.5.0 → 1.6.0). **Rebuild** the VSIX:
```powershell
cd "C:\Users\moren\Desktop\Claude Codex Black"
npx --yes @vscode/vsce package --allow-missing-repository
```

---

## 6. DOM-selector hunting (how to fill in section 3.10)

You typically can't test live without an active login. Your best play:

1. **Look at the reference implementations.** Gemini and Claude branches in `app.py` show the patterns that work today.
2. **If you have a Chrome session at the site:** right-click the composer → Inspect → look for stable IDs / data-testid / unique class. Same for an assistant message after sending one prompt manually.
3. **Common attributes that survive multiple DOM refactors** (priority order, most stable first):
   - `data-testid="..."` (Anthropic, OpenAI, Microsoft) — usually stable
   - `aria-label="..."` (everyone) — stable
   - Custom element names (e.g. `<message-content>` for Gemini) — fairly stable
   - `data-*` attributes that ship in product UI — moderately stable
   - Classes like `.markdown` / `.ProseMirror` / `.ql-editor` — stable (third-party)
   - One-off Tailwind class strings — DO NOT RELY ON, these churn weekly
4. **Composer types you'll see:**
   - `<textarea>` (rare modern; OpenAI ChatGPT uses one)
   - ProseMirror (`div.ProseMirror[contenteditable=true]`) — Anthropic, Notion, Substack
   - Quill (`.ql-editor[contenteditable=true]`) — Google products (Gemini)
   - Plain `[contenteditable=true][role=textbox]` — Microsoft Copilot, others
5. **Submit button:** the existing `__buttonInfo` scorer picks up "send", "submit", "send message", `data-testid="send-button"`, `aria-label*="Send"`. Most providers will work without extra selectors. If it picks up the wrong button (attach, mic, etc.), add a name to the excluded set in `__buttonInfo` for your target.
6. **Response container:** look for an element that wraps ONE assistant turn. Look at the HTML before AND after the user sends a message. The difference is your selector. Examples that worked:
   - ChatGPT: `[data-message-author-role="assistant"]`
   - Gemini: `message-content`, `.model-response-text`
   - Claude: `.font-claude-message`, `.font-claude-response`

**Best-effort first pass is fine.** The bridge will surface a visible window with `reveal-for-human-repair` if it can't find selectors after 100 ticks. You can refine after a real round-trip.

---

## 7. Common pitfalls (the landmines)

1. **Forgot to add the target to the service-side validation set in app.py (~line 3747).** You'll see `{"ok": false, "error": "unsupported target 'X'"}` even though the CLI accepted everything.
2. **Forgot underscore→hyphen normalization.** `parseChatArgs` and `force<Target>ChatParts` both compare against alias sets that contain hyphenated tokens (e.g. `gem-bridge`). Apply `.replace("_", "-")` before the `in targets` check.
3. **`activeChatArgName` doesn't know about your target.** When `--<target>` is the only flag, `applyChatUnknownTail` will set `args.chat = []` (empty list). Then `normalizeChatModeArgs` reads from the empty `args.chat` instead of `args.<target>`. ALWAYS extend `activeChatArgName`.
4. **One target per service.** The running SuperGrok service answers one target. To test a NEW target, kill the existing service first:
   ```powershell
   Get-Process python | Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match 'C:\\SuperGrok\\' } | Stop-Process -Force
   ```
5. **CBE `SuperGrokBridge.ensureRunning()` doesn't validate target.** If a Gemini service is running and CBE asks for Claude, the request goes to the Gemini service and fails with "unsupported target." Stop the service manually between targets.
6. **Speaker-tag regex can clip valid response prefixes.** The `text.replace(/^(ChatGPT|Grok|Claude)\s+said:\s*/i, '')` in `__messages` already covers the major providers; add your target name if you see leading "X said:" stripping needed.
7. **First-launch login is visible by design.** `configure<Target>LoginBridgeArgs` sets `show_bridge=True` and `offscreen=False`. After login, the service stays running with that window. Future chats reuse it. To make it offscreen post-login, stop and restart with `--offscreen` (CBE does this automatically via `SuperGrokBridge.ensureRunning()`).
8. **Per-target profile dir.** Cookies live in `C:\SuperGrok\data\<target>_profile\`. Different targets get separate profiles. Logging into Anthropic in one doesn't help Microsoft for Copilot.

---

## 8. How to test

After your edits:

```powershell
# 1. Syntax checks
python -c "import ast; ast.parse(open(r'C:\SuperGrok\start.py').read()); ast.parse(open(r'C:\SuperGrok\app.py').read())"
node --check "C:\Users\moren\Desktop\Claude Codex Black\extension.js"

# 2. CLI parse dry run (no live chat)
python -c "
import sys; sys.path.insert(0, r'C:\SuperGrok')
import start
parser = start.buildParser()
for argv in [['--<target>', 'hello'], ['--<target>'], ['--chat', '<target>', 'hi'], ['--chat', '<short>', 'hi', '--file']]:
    args, _ = parser.parse_known_args(argv)
    args.chatgpt_alias_requested = start.chatGptFlagPresent(argv)
    args.gemini_alias_requested = start.geminiFlagPresent(argv)
    args.claude_alias_requested = start.claudeFlagPresent(argv)
    if getattr(start, '<target>FlagPresent')(argv): args.<target>_alias_requested = True
    # ... call the login dispatcher chain
    start.normalizeChatModeArgs(args)
    start.normalizeTargetUrlArgs(args)
    print(argv, '->', args.chat, getattr(args, 'chat_target', '-'), args.url)
"

# 3. Live login (interactive — user signs in)
python C:\SuperGrok\start.py --<target>

# 4. Live chat (after login)
python C:\SuperGrok\start.py --<target> "hello"
```

For the hook:
```powershell
python C:\Users\moren\Desktop\claude\hooks\<target>_bridge_hook.py status
python C:\Users\moren\Desktop\claude\hooks\<target>_bridge_hook.py chat "hello"
```

For CBE: install the VSIX in a NEW VSCode window (never reload the host window), then `Ctrl+Shift+B` → ⚙ → pick your provider → send.

---

## 9. What's been done as of 2026-05-11

| Target | CLI | DOM selectors | Hook | CBE provider | Live-tested |
|---|---|---|---|---|---|
| grok | ✅ | ✅ | (legacy via --chat) | grokWeb (own Chrome) | ✅ |
| chatgpt | ✅ | ✅ | (legacy via --chat) | chatgptWeb (own Chrome) | ✅ |
| gemini | ✅ | ✅ | gemini_bridge_hook.py | geminiBridge | ✅ round-trip (Hello → real reply, off-by-one fixed) |
| claude | ✅ | ✅ best-effort | claude_bridge_hook.py | claudeBridge | ⏳ blocked on Anthropic SSO login (window visible) |
| copilot | ❌ | ❌ | ❌ | ❌ | — |
| azure | ❌ web bridge | ❌ | ❌ | ❌ (only `azure` API entry exists) | — |

---

## 10. If you're an agent and you need clarification

Don't guess at the user's intent. Phrase open questions at the END of your report; the main agent will iterate with you or the user. Specifically:

- **For Copilot:** the user probably means `https://copilot.microsoft.com` (consumer Microsoft Copilot, replacing Bing Chat). If they meant Microsoft 365 Copilot business (m365.cloud.microsoft) or GitHub Copilot Chat (a VSCode panel, not a website), say so.
- **For Azure:** `https://ai.azure.com/` is the AI Foundry / Studio chat playground. Auth is Azure AD. Selecting a model deployment is an extra step inside the page. Default to the foundry URL but flag that the user may need to navigate to a specific deployment.

Don't pull in `axios`, `playwright`, `selenium`, or any new dependency. Stick to PySide6 / Qt / runJavaScript on the SuperGrok side and `child_process.spawn` / `net` / `ws` (already vendored) on the CBE side.

If you can't find a DOM selector that works, write your best guess with a code comment that says `TODO: verify after first round-trip — current selectors are speculative` and call it out in your report.
