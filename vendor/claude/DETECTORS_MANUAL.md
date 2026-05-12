# Claude Detector Manual

This folder contains report-only Python detectors used by the launcher. They
look for code patterns that are easy to miss during large refactors: raw SQL,
raw file I/O, swallowed exceptions, lifecycle bypasses, unlocalized Qt strings,
and similar project-survival bugs.

The detectors are not linters in the Ruff/Pyright sense. They are project
policy checks. They encode rules that matter for these desktop apps: work must
flow through lifecycle/phase/process wrappers, failures must surface to the
debugger, UI text must be localizable, and dangerous shortcuts must be visible.

## Safe execution model

Detector routes are report-only.

They do not launch Qt. They do not run the child application. They do not import
application modules. Each detector reads `.py` files as text, parses them with
`ast.parse()`, walks the AST or source lines, writes a report under `logs/`,
prints the report, and exits.

The detectors intentionally scan Python source only. They do not scan `.pyd`,
`.dll`, `.so`, images, logs, databases, or generated caches.

Common skipped directories:

```text
.git
__pycache__
.mypy_cache
.ruff_cache
node_modules
vendor
```

The `vendor` skip is important. The detector bundle lives in `vendor/claude`,
so a health run against only the detector harness can report zero findings even
though it did not audit the real application. Always read the scan coverage.

## Correct mental model

A clean detector report means:

```text
No matching pattern was found in the files that were actually scanned.
```

It does not automatically mean:

```text
The whole project is clean.
```

Before trusting a clean result, confirm the report or launcher output shows the
expected project root and a realistic file count. A run that scans only
`start.py` is a harness smoke test, not a full app audit.

## --chat: AI model chat and deployment

The `--chat` route lets you talk to AI models directly from the CLI, spin up new Azure
deployments, sync API keys between config.ini and the DB, and health-check all models.

### Quick chat (one-shot)

```text
python start.py --chat grok "Hello, what can you do?"
python start.py --chat claude "Explain this code"
python start.py --chat codex "Write a Python sort"
python start.py --chat sora "Generate a sunset video"
python start.py --chat azure "How are you?"
python start.py --chat gemini "Summarize this"
python start.py --chat ollama "Run local model"
```

Provider/model shortcuts (always resolves to the latest deployed version):

```text
grok          → grok-4-20-reasoning  (xAI Grok 4, reasoning mode)
chatgpt / gpt → gpt-5-chat           (GPT-5 chat, latest)
codex         → gpt-5-codex          (GPT-5 Codex, Sep 2025)
sora          → sora-2               (Sora video generation)
claude        → claude-opus-4-7      (Claude Opus 4.7 on Azure)
deepseek      → DeepSeek-R1
kimi          → Kimi-K2-Thinking
copilot       → gpt-4.1
```

You can also specify provider + model explicitly:

```text
python start.py --chat azure grok "Hello"
python start.py --chat azure codex "Write a merge sort"
```

### Interactive mode

```text
python start.py --chat
```

Picks from a numbered list of all configured providers and live Azure deployments.
Supports multi-turn conversation history.

### Spin up a new deployment

```text
python start.py --chat deploy
```

Lists all 216+ available Azure base models, prompts for a model name and deployment
name, and creates the deployment via ARM. Requires the Trio service principal to have
Cognitive Services Contributor on the resource group (already assigned).

### Current Azure deployments (triodesktop)

```text
gpt-4.1           gpt-5-chat        gpt-5.1-chat      gpt-5.4
gpt-5-chat        gpt-5-codex       grok-4-20-reasoning
sora-2            gpt-5.4-pro       (claude-opus-4-7 pending Anthropic terms)
```

### Key/endpoint config

Chat reads from (in priority order):
1. Environment variables: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY1`, `OPENAI_API_KEY`, etc.
2. `flatline.db` settings table
3. `config.ini` `[azure]` / `[api_keys]` sections

Missing keys prompt interactively and are saved back to both config.ini and flatline.db.

Logs are written to `logs/azure.log`.

### Chat key-sync detector

```text
python start.py --chat-sync
```

Compares API keys between config.ini and flatline.db, copies the newer value to
whichever store is missing or stale, then smoke-tests each configured model with
a short ping. Report written to `logs/chat_sync.txt`.

---

## Launcher usage

Run from the project root when possible:

```text
python start.py --health
python start.py --certify
python start.py --detector-selftest
python start.py --man
```

Run one detector:

```text
python start.py --monkey
python start.py --lifecycle-bypass
python start.py --raw-sql
python start.py --recursion
python start.py --swallowed
python start.py --redundant
python start.py --file-io
python start.py --process-faults
python start.py --phase-ownership
python start.py --threads
python start.py --bad-code
python start.py --unlocalized
python start.py --stubs
```

Run against a different application root:

```text
python start.py --health --root C:\Path\To\App
python start.py --raw-sql --root C:\Path\To\App
```

Run against specific seed paths:

```text
python start.py --swallowed --root . start.py app_main.py package_dir
```

The launcher normalizes flags, so dash variants are accepted. For example,
`--raw-sql`, `-rawsql`, `/raw_sql`, and `rawsql` all normalize to the same route
when the route alias exists.

## Direct detector usage

Most detectors support this shape:

```text
python vendor/claude/badcodedetector.py --root . --output logs/badcode.txt .
```

The monkey-patch detector is the legacy oddball. It uses `--out` instead of
`--output`:

```text
python vendor/claude/monkeypatchdetect.py . --out logs/monkeypatches.txt
```

`font_terminal.py` is a utility module, not one of the health/certify detector
routes. It may live beside the detectors, but it is not run by `--health`.

## Health, certify, and self-test

`--certify` runs every detector route and writes all reports.

`--health` currently runs the same routes, but it also prints scan coverage. If
only one non-vendor Python file is visible, treat the run as a harness check and
not as proof that the real app is clean.

`--detector-selftest` creates a temporary dirty canary file and runs the
detectors against it. The self-test is useful because it proves the routes are
actually connected. A detector that reports zero findings against the canary is
probably miswired or too narrow.

## Reports

Reports are written under `logs/` relative to the selected root.

```text
--monkey             logs/monkeypatches.txt
--lifecycle-bypass   logs/lifecyclebypass.txt
--raw-sql            logs/rawsql.txt
--recursion          logs/recursion.txt
--swallowed          logs/swallowed.txt
--redundant          logs/redundant.txt
--file-io            logs/fileio.txt
--process-faults     logs/process_faults.txt
--phase-ownership    logs/phase_ownership.txt
--threads            logs/thread_safety.txt
--bad-code           logs/badcode.txt
--unlocalized        logs/unlocalized.txt
--stubs              (prints to stdout, no log file)
```

Most detectors exit with code `0` when clean and `1` when findings are present.
Infrastructure failures, missing scripts, parse failures, or timeouts may return
other non-zero codes.

## Suppression markers

Suppressions are local comments. Use them only when the code was reviewed and
the exception is intentional.

```text
badcodedetector.py              # noqa: badcode
fileiodetector.py               # file-io-ok
fileiodetector.py               # lifecycle-file-ok
fileiodetector.py               # qt-open-ok
find_stubs.py                   # stub-ok
lifecyclebypassdetector.py      # lifecycle-bypass-ok
phaseownershipdetector.py       # phase-ownership-ok
phaseownershipdetector.py       # qt-main-thread-ok
phaseownershipdetector.py       # detector-runner-ok
processfaultdetector.py         # process-fault-ok
processfaultdetector.py         # phase-process-ok
rawsqldetector.py               # raw-sql-ok
rawsqldetector.py               # noqa: raw-sql
swallowedexceptionsdetector.py  # swallow-ok
swallowedexceptionsdetector.py  # noqa: swallowed
threaddetector.py               # thread-ok
unlocalizeddetector.py          # // localized
unlocalizeddetector.py          # noqa: unlocalized
unlocalizeddetector.py          # unlocalized-ok
```

## Detector details

### monkeypatchdetect.py

Route: `--monkey`

Finds runtime mutation of imported, external, standard-library, or framework
objects. Examples include assigning to `os.path.exists`, changing
`sys.modules`, calling `setattr()` on imported objects, changing dunder fields,
and using patch helpers.

The detector favors false positives over misses. High-confidence findings are
usually real monkey patches. Low-confidence findings should be reviewed.

### lifecyclebypassdetector.py

Route: `--lifecycle-bypass`

Finds direct process, thread, and blocking runtime calls that bypass lifecycle
wrappers. Examples include `subprocess.run()`, `subprocess.Popen()`,
`threading.Thread()`, and `os.system()` outside allowed launcher functions.

The intent is simple: processes and threads should be created by project-owned
wrappers so timeouts, faults, logging, and debugger surfaces are consistent.

### rawsqldetector.py

Route: `--raw-sql`

Finds raw database access that bypasses the ORM or database abstraction. It
looks for connector calls, cursor calls, and raw `.execute()` patterns. It has
allowances for known Google API `.execute()` call text and explicit raw-SQL
suppressions.

### recursiondetector.py

Route: `--recursion`

Finds direct self-recursion candidates. This is intentionally narrow. It does
not prove all recursion-like cycles are gone, but it catches the obvious case:
a function calling itself by name.

### swallowedexceptionsdetector.py

Route: `--swallowed`

Finds exception handlers that hide failures. A trace line alone is not enough.
An exception is handled only when it is raised, converted into an explicit
failure result, recorded as a fault/exception, persisted for the debugger, or
surfaced as a warning.

The bundled runtime provides `InsertDebuggerException(...)`. It writes to
`logs/debugger_exceptions.sqlite3`, where `start.py --exceptions` can display
recent rows. Use that surface when a handler must continue instead of raising.

Rules:

```text
SE001  empty except handler
SE002  pass/return/continue/break-only handler
SE003  silent null/false/empty return from handler
SE004  control-flow exit without fault propagation
SE005  trace-and-swallow handler
SE006  catch-all handler that only logs/traces
SE007  catch-all handler lacks a recognized warning/fault surface
```

Recognized surfaces include names such as `recordException`,
`InsertDebuggerException`, `captureFault`, `onFault`, `onError`, `onException`,
`warn`, and `showWarning`.

### redundant.py

Route: `--redundant`

Finds three or more consecutive near-identical line shapes. This catches code
that should probably be a loop or a table-driven helper. It is source-shape
based, not a semantic proof.

The canonical file is `redundant.py`. Do not keep a second
`redundantdetector.py` copy in the bundle unless the launcher is intentionally
changed to route to it.

### fileiodetector.py

Route: `--file-io`

Finds raw file access outside traced wrappers. Examples include `open()`,
`Path.read_text()`, `Path.write_text()`, `Path.open()`, `shutil.copy2()`, and
`ZipFile()` when they are not routed through project-managed helpers.

The goal is not to ban file I/O. The goal is to make file I/O visible to the
launcher/debugger and to keep permissions, tracing, and error handling uniform.

### processfaultdetector.py

Route: `--process-faults`

Finds `Process(...).spawn()` and `Process(...).start()` chains that are missing
fault callbacks. A managed process should expose `onError`, `onException`, and
`onFault`, or be covered by a reviewed suppression.

### phaseownershipdetector.py

Route: `--phase-ownership`

Finds direct runtime work that should belong to a lifecycle phase. It overlaps
with lifecycle bypass checks, but the purpose is architectural: startup and
runtime work should be owned by phases, not scattered through random UI or
utility methods.

### threaddetector.py

Route: `--threads`

Reports in two sections.

The thread/process section checks `Thread`, `Timer`, `ThreadPoolExecutor`,
`ProcessPoolExecutor`, `Future`, `submit`, `Process`, `spawn`, `fork`, and
`Popen` style calls. It looks for:

```text
TTL or timeout coverage
exception handlers that save to the DB/debugger
fault handlers that save to the DB/debugger
lifecycle callbacks such as onError, onComplete, and onTimeout
```

The phase section checks `Phase(...)` constructor calls and reports which phase
hooks are wired: `onError`, `onFault`, `onException`, `onComplete`, `onTimeout`,
`onStart`, and `onStop`.

Processes created inside a phase with `phaseKey=` are suppressed from the
thread/process section because the phase should own and wire those processes.

### badcodedetector.py

Route: `--bad-code`

Finds general bad-code patterns that are cheap to detect with AST. Current rule
families include dead writes, self-assignment, augmented no-ops, double
negation, tautological comparisons, redundant else blocks, boolean-return
boilerplate, dead code, useless f-strings, discarded results, and similar
obvious cleanup targets.

This detector is not a replacement for Ruff. It catches project-specific smells
that are worth reviewing during refactor passes.

### unlocalizeddetector.py

Route: `--unlocalized`

Finds raw string literals passed directly to Qt UI methods or constructors.
Examples include `setText("Save")`, `setWindowTitle("Settings")`,
`QLabel("Name")`, `QPushButton("OK")`, and `addTab(widget, "Tab")`.

The preferred pattern is one canonical localization entry point:

```text
Localize("settings.saveButton")
localize("settings.saveButton")
self.Localize("settings.saveButton")
self.loc("settings.saveButton")
```

Older wrappers such as `localizationButtonText()` and
`localizationLabelText()` may be accepted while migrating, but they should not
be the long-term style unless they enforce real type-specific behavior.

## Phase model guidance

A startup phase should not be a parallel lifecycle class. The cleaner model is
one `Phase` type with properties such as:

```text
startup=True
required=True
statusText=...
order=...
```

That means startup is data on a phase, not a second lifecycle system. Startup
phases still use the same callback, fault, timeout, completion, and process
ownership paths as every other phase.

If a codebase already has `StartPhase`, it should normally be deleted or
reduced to a thin alias/factory that returns `Phase(startup=True, ...)`. A real
subclass is only justified when it adds behavior that ordinary phases cannot
share. A separate parallel class with duplicated hooks, process ownership,
timeout handling, and fault routing is a refactor smell.

## Reading findings

Treat findings as review tickets, not automatic edits. Some hits are real bugs.
Some are intentional exceptions that need a suppression and a comment. Some are
false positives that reveal the detector should be tuned.

A good detector pass ends with three numbers:

```text
findings fixed
findings suppressed as intentional
findings left open
```

Do not report "100% clean" unless the correct root was scanned, the expected
number of app files was visible, every detector route ran, and the generated
reports were checked.

### find_stubs.py

Route: `--stubs` (aliases: `--stub-detector`, `--find-stubs`)

Finds stub functions and methods — bodies that consist only of `pass`, `...`,
`return None`, `return False`, `return 0`, `return ""`, `return {}`, `return []`,
or `return ()` after an optional docstring. These are implementation gaps rather
than intentional no-ops.

Default scan targets:

```text
cutiepy.py
cutiepy.headless.py
classes/_config.py
classes/_lifecycle.py
classes/_themes.py
classes/_localization.py
classes/_updates.py
```

Pass specific paths to override the defaults:

```text
python vendor/claude/find_stubs.py classes/_config.py classes/_updates.py
```

Output format (prints to stdout, exits 1 when stubs found):

```text
=== classes/_updates.py (2 stubs) ===
  line     42: ollamaListInstalledModels  [return_empty_list]
  line     58: _httpJson  [return_empty_dict]

Total stubs found: 2
```

**Suppression — inline:** add `# stub-ok` on the `def` line when the stub is
intentional (abstract base method, Qt headless shim, interface placeholder):

```python
def title(self) -> str: ...  # stub-ok
```

**Suppression — ignore file:** add `filename:funcname` (case-insensitive) to
`vendor/claude/stubs_ignore.txt` for stubs that belong to headless shims or
fallback containers and cannot be annotated inline (e.g., generated or imported
bodies):

```text
# Qt headless shims
cutiepy.headless.py:connect
cutiepy.headless.py:emit

# SchemaManagerUnavailable fallbacks
_lifecycle.py:bootstrap
_lifecycle.py:ensure
```

Lines starting with `#` are comments. Blank lines are ignored.

The stub detector also runs the Peewee scan automatically (see below).

### find_stubs.py — Peewee scan

The same `--stubs` route runs `scan_peewee` / `run_peewee_scan` after the stub
pass. This warns on any direct Peewee usage in the default scan targets.

Patterns flagged:

```text
peewee  (case-insensitive, anywhere in a non-comment line)
.Model.select()
pw.Model
DoesNotExist
PeeweeException
```

`reuse_if_open` is intentionally excluded — it appears in the SQLAlchemy-based
`data.py` wrapper as a Peewee-compatible shim and is not real Peewee.

Output format:

```text
=== PEEWEE: classes/_config.py (1 hit) ===
  line     77:     from peewee import SqliteDatabase

Total Peewee references: 1
```

If the scan finds Peewee references, replace them with the SQLAlchemy ORM
session pattern (`_DataBase.session()`, `session.query(...)`, `session.add(...)`,
`session.commit()`). The Peewee scan exits 0 even when findings are present — it
is advisory only.


## TrioDesktop local integration notes

This repo also wires the newer dependency and import/file detectors into `start.py`:

```text
python start.py --depcheck   # logs/depcheck.txt
python start.py --bfabi      # logs/bfabi.txt
```

`--depcheck` compares imports against `PYTHON_DEPENDENCIES` in `start.py`.
`--bfabi` checks broken file reads and wrong/unknown import names. In TrioDesktop it
uses `PYTHON_DEPENDENCIES` plus `knownimports.txt` as the source of truth and avoids
running `pip list` during report-only health checks so detector runs stay bounded.
