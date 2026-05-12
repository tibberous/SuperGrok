#!/usr/bin/env python3
"""Detects third-party imports that are not registered in PYTHON_DEPENDENCIES."""
from __future__ import annotations
import ast
import datetime
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding, discover_project_root
from vendor.claude.detector_runtime import tracedWriteText

# Import names that map to non-obvious pip package names.
# Value is the pip package you actually install.
WEIRD_IMPORT_INSTALL_MAP: dict[str, list[str]] = {
    # Font / graphics weirdos
    "fontforge": ["fontforge", "python3-fontforge"],
    "psMat": ["fontforge", "python3-fontforge"],

    # Famous pip-name != import-name cases
    "PIL": ["Pillow", "python3-pil"],
    "cv2": ["opencv-python", "opencv-python-headless", "python3-opencv"],
    "sklearn": ["scikit-learn", "python3-sklearn"],
    "skimage": ["scikit-image", "python3-skimage"],
    "yaml": ["PyYAML", "python3-yaml"],
    "bs4": ["beautifulsoup4", "python3-bs4"],
    "serial": ["pyserial", "python3-serial"],
    "dateutil": ["python-dateutil", "python3-dateutil"],
    "dotenv": ["python-dotenv"],
    "fitz": ["PyMuPDF"],
    "wx": ["wxPython"],

    # Windows / COM
    "win32api": ["pywin32"],
    "win32con": ["pywin32"],
    "win32gui": ["pywin32"],
    "win32com": ["pywin32"],
    "pythoncom": ["pywin32"],
    "pywintypes": ["pywin32"],

    # Database/client drivers
    "MySQLdb": ["mysqlclient"],
    "psycopg2": ["psycopg2-binary", "psycopg2"],
    "pymysql": ["PyMySQL"],

    # Crypto/security
    "OpenSSL": ["pyOpenSSL"],
    "Crypto": ["pycryptodome"],
    "Cryptodome": ["pycryptodomex"],
    "jwt": ["PyJWT"],
    "jose": ["python-jose"],
    "dns": ["dnspython"],

    # Documents / files
    "docx": ["python-docx"],
    "pptx": ["python-pptx"],
    "magic": ["python-magic", "python-magic-bin", "libmagic"],
    "barcode": ["python-barcode"],
    "slugify": ["python-slugify"],

    # Google / cloud namespace packages
    "google.genai": ["google-genai"],
    "google.generativeai": ["google-generativeai"],
    "googleapiclient": ["google-api-python-client"],
    "google.auth": ["google-auth"],
    "google.protobuf": ["protobuf"],
    "grpc": ["grpcio"],

    # Linux GUI / native bindings
    "gi": ["PyGObject", "python3-gi", "python3-gobject"],
    "gi.repository": ["PyGObject", "python3-gi", "python3-gobject", "gir1.2-* typelibs"],
    "cairo": ["pycairo", "python3-cairo"],
    "dbus": ["dbus-python", "python3-dbus"],
    "apt": ["python3-apt"],
    "apt_pkg": ["python3-apt"],
    "rpm": ["python3-rpm"],
    "dnf": ["python3-dnf"],

    # Misc common traps
    "usb": ["pyusb"],
    "Levenshtein": ["python-Levenshtein"],
    "Bio": ["biopython"],
    "pkg_resources": ["setuptools"],
    "mpl_toolkits": ["matplotlib"],
    "qdarkstyle": ["QDarkStyle"],
}

# Packages that are always present (stdlib, builtins, or local project modules).
# Top-level names only — we strip dotted suffixes before checking.
_STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names) | frozenset({
    '__future__', '_thread', 'antigravity',
})

# Local package roots — imports that start with these are project-internal.
_LOCAL_ROOTS: frozenset[str] = frozenset({
    'classes', 'languages', 'vendor', 'tools', 'data', 'trio',
    'models', 'pyaudioencoder_gui', 'hooks', 'start', 'cutiepy', 'check_font', 'config',
})

# Import names explicitly known-good without being in PYTHON_DEPENDENCIES
# (e.g. installed by the OS package manager, or ships with the app).
_KNOWN_SYSTEM: frozenset[str] = frozenset({
    'openshot',       # C extension installed separately
    'libopenshot',
    'shiboken6',      # bundled with PySide6
    'shiboken2',      # bundled with PySide2
})

OK_MARKER = 'depcheck-ok'


def _top(module: str) -> str:
    """Return the top-level name: 'PySide6.QtCore' → 'PySide6'."""
    return module.split('.')[0]


def _project_local_module_names(root: Path) -> set[str]:
    names: set[str] = set(_LOCAL_ROOTS)
    try:
        for candidate in root.rglob('*.py'):
            if any(part in {'.git', '__pycache__', 'snapshots', 'vendor'} for part in candidate.parts):
                continue
            names.add(candidate.stem)
            try:
                rel = candidate.relative_to(root)
                dotted = '.'.join(rel.with_suffix('').parts)
                if dotted:
                    names.add(dotted)
                    names.add(dotted.split('.')[0])
            except Exception:
                pass
    except Exception:
        pass
    return names


def _is_local(module: str, root: Path, path: Path) -> bool:
    top = _top(module)
    if top in _LOCAL_ROOTS:
        return True
    local_names = _project_local_module_names(root)
    if module in local_names or top in local_names:
        return True
    # Common hook modules are intentionally importable by basename because
    # the app adds hooks/ to sys.path when loading hook packs.
    if (root / 'hooks' / f'{top}.py').exists():
        return True
    if (root / f'{top}.py').exists():
        return True
    return False


def _collect_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, top-level-module-name) for every import in the file."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import — always local
            if node.module:
                found.append((node.lineno, node.module))
    return found


def _load_registered_imports(root: Path) -> set[str]:
    """Parse PYTHON_DEPENDENCIES from start.py and return the set of registered import_names."""
    start_py = root / 'start.py'
    if not start_py.exists():
        return set()
    try:
        text = start_py.read_text(encoding='utf-8', errors='replace')
        tree = ast.parse(text, filename=str(start_py))
    except Exception:
        return set()

    registered: set[str] = set()
    for node in ast.walk(tree):
        # Look for PYTHON_DEPENDENCIES = ( {'import_name': '...', ...}, ... )
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == 'PYTHON_DEPENDENCIES'):
                continue
            val = node.value
            if not isinstance(val, ast.Tuple):
                continue
            for elt in val.elts:
                if not isinstance(elt, ast.Dict):
                    continue
                for k, v in zip(elt.keys, elt.values):
                    if (isinstance(k, ast.Constant) and k.value == 'import_name'
                            and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                        registered.add(v.value)
                        registered.add(_top(v.value))
    return registered


class DepCheckDetector(Detector):
    NAME = 'depcheck'
    VERSION = '1.0.0'
    REPORT_HEADER = 'DEPENDENCY CHECK DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/depcheck.txt'

    def __init__(self):
        self._registered: set[str] = set()

    def _load(self, root: Path) -> None:
        self._registered = _load_registered_imports(root)

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        if not self._registered:
            self._load(root)

        findings: list[Finding] = []
        seen_this_file: set[str] = set()

        for lineno, module in _collect_imports(tree):
            top = _top(module)
            if top in _STDLIB:
                continue
            if _is_local(module, root, path) or top in _KNOWN_SYSTEM:
                continue
            # Check ok marker on the line
            snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
            if OK_MARKER in snippet:
                continue

            # Already registered?
            if module in self._registered or top in self._registered:
                continue

            if top in seen_this_file:
                continue
            seen_this_file.add(top)

            # Build a helpful install hint
            candidates = WEIRD_IMPORT_INSTALL_MAP.get(module) or WEIRD_IMPORT_INSTALL_MAP.get(top)
            if candidates:
                hint = f"pip install {candidates[0]}  (import '{top}' maps to: {', '.join(candidates)})"
            else:
                hint = f"pip install {top}  (add to PYTHON_DEPENDENCIES in start.py)"

            sample = lines[lineno - 1].strip()[:120] if 0 < lineno <= len(lines) else ''
            findings.append(Finding(
                path, lineno, 0, 'HIGH', 'UNREGISTERED_IMPORT',
                f"'{top}' is not in PYTHON_DEPENDENCIES — {hint}",
                sample,
            ))
        return findings

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        lines_out = [
            self.REPORT_HEADER,
            '=' * len(self.REPORT_HEADER),
            f'Version: {self.VERSION}',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Findings: {len(findings)}',
            '',
        ]
        if findings:
            lines_out += [
                'Every finding below is a third-party import with no entry in PYTHON_DEPENDENCIES.',
                'Either add it to PYTHON_DEPENDENCIES in start.py, or suppress with:  # depcheck-ok',
                '',
            ]
            # Group by module (top-level) for readability
            by_module: dict[str, list[Finding]] = {}
            for f in findings:
                key = f.message.split("'")[1]
                by_module.setdefault(key, []).append(f)
            for mod in sorted(by_module):
                group = sorted(by_module[mod], key=lambda x: (str(x.path), x.line))
                lines_out.append(f'  {mod}:')
                for f in group:
                    lines_out.append(f'    {f.render(root)}')
                lines_out.append('')
        else:
            lines_out.append('All imports are covered by PYTHON_DEPENDENCIES.')
        return '\n'.join(lines_out) + '\n'

    def _run(self, argv=None):
        import argparse
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default='.')
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)

        from vendor.claude.detector_base import discover_project_root, iter_py
        discovered = discover_project_root()
        root = Path(ns.root if ns.root != '.' else str(discovered)).resolve()
        if ns.paths:
            seeds = [Path(x).resolve() for x in ns.paths]
        else:
            default_seeds = [root / 'start.py', root / 'classes', root / 'data.py', root / 'trio.py']
            seeds = [p for p in default_seeds if p.exists()] or [root]

        self._load(root)
        from vendor.claude.detector_base import iter_py
        files = iter_py(seeds, root)
        findings = self.run_parallel(files, root)
        report = self.render_report(root, files, findings)
        out = Path(ns.output)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        tracedWriteText(out, report, encoding='utf-8')
        try:
            print(report)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
            print(report.encode(enc, errors='replace').decode(enc, errors='replace'))
        return self._exit_code(findings)


if __name__ == '__main__':
    DepCheckDetector.main()
