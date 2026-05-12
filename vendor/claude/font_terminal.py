#!/usr/bin/env python3
"""Font installation and terminal font-change utilities for Windows."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, TYPE_CHECKING

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, launcherRunCommand, tracedCopy2, tracedOpen

# ---------------------------------------------------------------------------
# Pylance stubs for Windows-only modules
# ---------------------------------------------------------------------------
if TYPE_CHECKING:
    import winreg  # type: ignore[import]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Registry:
    """Thin wrapper around winreg for reading and writing HKCU keys."""

    HKCU = 0x80000001  # winreg.HKEY_CURRENT_USER

    @staticmethod
    def _winreg():  # type: ignore[return]
        import winreg as _wr  # type: ignore[import]
        return _wr

    @classmethod
    def get(cls, path: str, name: str, default: Any = None) -> Any:
        """Read a single value from HKCU\\path."""
        if os.name != "nt":
            return default
        try:
            wr = cls._winreg()
            with wr.OpenKey(wr.HKEY_CURRENT_USER, path) as key:
                value, _ = wr.QueryValueEx(key, name)
                return value
        except Exception:
            InsertDebuggerException("font_terminal.py:51", "handled exception")
            return default

    @classmethod
    def set(cls, path: str, name: str, value: Any, reg_type: int | None = None) -> bool:
        """Write a single value to HKCU\\path, creating the key if needed."""
        if os.name != "nt":
            return False
        try:
            wr = cls._winreg()
            if reg_type is None:
                reg_type = wr.REG_SZ if isinstance(value, str) else wr.REG_DWORD
            with wr.CreateKey(wr.HKEY_CURRENT_USER, path) as key:
                wr.SetValueEx(key, name, 0, reg_type, value)
            return True
        except Exception:
            InsertDebuggerException("font_terminal.py:66", "handled exception")
            return False

    @classmethod
    def get_str(cls, path: str, name: str, default: str = "") -> str:
        return str(cls.get(path, name, default) or default)

    @classmethod
    def get_dword(cls, path: str, name: str, default: int = 0) -> int:
        try:
            return int(cls.get(path, name, default) or default)
        except Exception:
            InsertDebuggerException("font_terminal.py:77", "handled exception")
            return default


# ---------------------------------------------------------------------------
# ctypes structures (Windows only)
# ---------------------------------------------------------------------------

class _COORD(ctypes.Structure):  # type: ignore[misc]
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _CONSOLE_FONT_INFOEX(ctypes.Structure):  # type: ignore[misc]
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("nFont", ctypes.c_ulong),
        ("dwFontSize", _COORD),
        ("FontFamily", ctypes.c_uint),
        ("FontWeight", ctypes.c_uint),
        ("FaceName", ctypes.c_wchar * 32),
    ]


# ---------------------------------------------------------------------------
# Font
# ---------------------------------------------------------------------------

class Font:
    """Represents a font file and knows how to install it on Windows."""

    FONTS_REG_PATH = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"

    def __init__(self, path: str | Path, face_name: str = "") -> None:
        self.path = Path(path)
        self.face_name = face_name or self.path.stem

    @property
    def reg_value_name(self) -> str:
        ext = self.path.suffix.lstrip(".").upper() or "OTF"
        return f"{self.face_name} ({ext})"

    def installFont(self) -> bool:  # noqa: N802
        """Install font to %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts and register it."""
        if os.name != "nt":
            print("[Font] installFont is Windows-only")
            return False
        if not self.path.exists():
            print(f"[Font] file not found: {self.path}")
            return False
        try:
            local = os.environ.get("LOCALAPPDATA", "")
            if not local:
                print("[Font] LOCALAPPDATA not set")
                return False
            dest_dir = Path(local) / "Microsoft" / "Windows" / "Fonts"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / self.path.name
            tracedCopy2(self.path, dest)
            ok = Registry.set(self.FONTS_REG_PATH, self.reg_value_name, str(dest))
            if not ok:
                print(f"[Font:WARN] copied but registry write failed: {dest}")
            else:
                print(f"[Font] installed: {dest}")
            return True
        except Exception as exc:
            InsertDebuggerException("font_terminal.py:141", "handled exception")
            print(f"[Font:ERROR] {exc}")
            return False

    def changeFont(self, *args: Any, **kwargs: Any) -> bool:  # noqa: N802
        """No-op at the base level; subclasses wire this to a terminal."""
        return False


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

class Terminal:
    """Base class for terminal font management."""

    def installFont(self, font: Font) -> bool:  # noqa: N802
        return font.installFont()

    def changeFont(self, face_name: str, size: int = 16, weight: int = 400) -> bool:  # noqa: N802
        raise NotImplementedError

    # Shared ctypes helper for classic Windows console hosts
    @staticmethod
    def _set_console_font_ctypes(face_name: str, width: int = 0, height: int = 16, weight: int = 400) -> bool:
        if os.name != "nt":
            return False
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            fi = _CONSOLE_FONT_INFOEX()
            fi.cbSize = ctypes.sizeof(_CONSOLE_FONT_INFOEX)
            fi.nFont = 0
            fi.dwFontSize = _COORD(X=width, Y=height)
            fi.FontFamily = 0x36  # fixed-pitch TrueType
            fi.FontWeight = weight
            fi.FaceName = face_name
            result = kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(fi))
            return bool(result)
        except Exception as exc:
            InsertDebuggerException("font_terminal.py:180", "handled exception")
            print(f"[Terminal:ctypes] {exc}")
            return False


# ---------------------------------------------------------------------------
# PowerShell terminal
# ---------------------------------------------------------------------------

_PS_CONSOLE_REG = r"Console\%SystemRoot%_System32_WindowsPowerShell_v1.0_powershell.exe"
_PWSH_CONSOLE_REG = r"Console\%SystemRoot%_System32_WindowsPowerShell_v1.0_pwsh.exe"
_PS_MAJOR_REG_PATH = r"SOFTWARE\Microsoft\PowerShell\3\PowerShellEngine"
_PS_MAJOR_REG_KEY = "PowerShellVersion"


class PowerShell(Terminal):
    """Manage fonts for the classic PowerShell console (conhost-based)."""

    def _ps_version(self) -> int:
        """Return the installed PowerShell major version (or 0 if unknown)."""
        raw = Registry.get_str(_PS_MAJOR_REG_PATH, _PS_MAJOR_REG_KEY)
        try:
            return int(str(raw or "0").split(".")[0])
        except Exception:
            InsertDebuggerException("font_terminal.py:203", "handled exception")
            return 0

    def _upgrade_powershell(self) -> bool:
        """Attempt to install PowerShell 7+ via winget if current version < 7."""
        version = self._ps_version()
        print(f"[PowerShell] detected version: {version or '(unknown)'}")
        if version >= 7:
            return True
        print("[PowerShell] version < 7 — attempting upgrade via winget …")
        try:
            result = launcherRunCommand(
                ["winget", "install", "--id", "Microsoft.PowerShell", "--silent", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                print("[PowerShell] upgrade succeeded")
                return True
            print(f"[PowerShell:WARN] winget exit {result.returncode}: {result.stderr.strip()}")
            return False
        except FileNotFoundError:
            InsertDebuggerException("font_terminal.py:223", "handled exception")
            print("[PowerShell:WARN] winget not found; install manually from aka.ms/pscore6")
            return False
        except Exception as exc:
            InsertDebuggerException("font_terminal.py:226", "handled exception")
            print(f"[PowerShell:ERROR] upgrade failed: {exc}")
            return False

    def changeFont(self, face_name: str, size: int = 16, weight: int = 400) -> bool:  # noqa: N802
        """
        Persist font to the PowerShell console registry keys and apply at runtime.
        Registry change takes effect for new console windows; ctypes applies now.
        """
        font_size_dword = size << 16  # high word = height in pixels
        for reg_path in (_PS_CONSOLE_REG, _PWSH_CONSOLE_REG, r"Console"):
            Registry.set(reg_path, "FaceName", face_name)  # noqa: redundant
            Registry.set(reg_path, "FontSize", font_size_dword)  # noqa: redundant
            Registry.set(reg_path, "FontWeight", weight)  # noqa: redundant
            Registry.set(reg_path, "FontFamily", 0x36)
        runtime_ok = self._set_console_font_ctypes(face_name, height=size, weight=weight)
        print(f"[PowerShell] font set to '{face_name}' size={size} (registry=ok, runtime={runtime_ok})")
        return True

    def installFont(self, font: Font) -> bool:  # noqa: N802
        font.installFont()
        self._upgrade_powershell()
        return True


# ---------------------------------------------------------------------------
# PythonTerminal
# ---------------------------------------------------------------------------

_IDLE_CFG_DIR = Path.home() / ".idlerc"


class PythonTerminal(Terminal):
    """Manage fonts for the Python IDLE terminal and the running console window."""

    def changeFont(self, face_name: str, size: int = 16, weight: int = 400) -> bool:  # noqa: N802
        """
        Apply font to:
        1. The running console window via SetCurrentConsoleFontEx (ctypes).
        2. IDLE's config file (~/.idlerc/config-main.cfg) if IDLE is available.
        """
        runtime_ok = self._set_console_font_ctypes(face_name, height=size, weight=weight)
        self._write_idle_config(face_name, size)
        print(f"[PythonTerminal] font set to '{face_name}' size={size} (runtime={runtime_ok})")
        return True

    def _write_idle_config(self, face_name: str, size: int) -> None:
        """Persist font into ~/.idlerc/config-main.cfg for IDLE."""
        try:
            import configparser  # stdlib — always available
            cfg_path = _IDLE_CFG_DIR / "config-main.cfg"
            _IDLE_CFG_DIR.mkdir(parents=True, exist_ok=True)
            cfg = configparser.ConfigParser()
            if cfg_path.exists():
                cfg.read(str(cfg_path))
            if not cfg.has_section("EditorWindow"):
                cfg.add_section("EditorWindow")
            cfg.set("EditorWindow", "font-name", face_name)
            cfg.set("EditorWindow", "font-size", str(size))
            cfg.set("EditorWindow", "font-bold", "0")
            with tracedOpen(cfg_path, "w") as fh:
                cfg.write(fh)
        except Exception as exc:
            InsertDebuggerException("font_terminal.py:288", "handled exception")
            print(f"[PythonTerminal:WARN] IDLE config write failed: {exc}")

    def installFont(self, font: Font) -> bool:  # noqa: N802
        installed = font.installFont()
        if installed:
            self.changeFont(font.face_name)
        return installed


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def install_and_apply(font_path: str | Path, face_name: str = "", size: int = 16) -> None:
    """Install a font and apply it to both PowerShell and PythonTerminal."""
    f = Font(font_path, face_name)
    PowerShell().installFont(f)
    PowerShell().changeFont(f.face_name, size=size)
    PythonTerminal().changeFont(f.face_name, size=size)


# ---------------------------------------------------------------------------
# Windows Terminal detection and font application
# ---------------------------------------------------------------------------

def windows_terminal_settings_candidates() -> list[Path]:
    """Return candidate paths for Windows Terminal settings.json."""
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
    candidates = [
        local_appdata / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
        local_appdata / "Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json",
        local_appdata / "Microsoft/Windows Terminal/settings.json",
    ]
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _extract_terminal_font(payload: dict[str, object]) -> str:
    if not isinstance(payload, dict):
        return ""
    font_payload = payload.get("font")
    if isinstance(font_payload, dict):
        face = str(font_payload.get("face", "") or "").strip()
        if face:
            return face
    return str(payload.get("fontFace", "") or "").strip()


def detect_console_font_info() -> dict[str, str]:
    """Return the active terminal host and font face name."""
    info: dict[str, str] = {"host": "unknown", "font": "", "source": "", "path": ""}
    if os.name != "nt":
        return info
    if os.environ.get("WT_SESSION"):
        info["host"] = "windows-terminal"
        for settings_path in windows_terminal_settings_candidates():
            if not settings_path.exists():
                continue
            try:
                payload = json.loads(settings_path.read_text(encoding="utf-8", errors="replace") or "{}")
            except Exception:
                continue
            profiles = payload.get("profiles") if isinstance(payload, dict) else {}
            defaults = profiles.get("defaults") if isinstance(profiles, dict) else {}
            font = _extract_terminal_font(defaults)
            if not font:
                for profile in (profiles.get("list") or [] if isinstance(profiles, dict) else []):
                    if not isinstance(profile, dict):
                        continue
                    name = str(profile.get("name", "") or "").lower()
                    cmdline = str(profile.get("commandline", "") or "").lower()
                    if "powershell" in name or "pwsh" in name or "powershell" in cmdline or "pwsh" in cmdline:
                        font = _extract_terminal_font(profile)
                        if font:
                            break
            info["font"] = font
            info["source"] = "settings.json"
            info["path"] = str(settings_path)
            return info
        return info
    try:
        import winreg as _wr  # type: ignore[import]
        info["host"] = "console-host"
        with _wr.OpenKey(_wr.HKEY_CURRENT_USER, r"Console") as key:
            face, _ = _wr.QueryValueEx(key, "FaceName")
            info["font"] = str(face or "").strip()
            info["source"] = r"HKCU\Console"
            info["path"] = r"HKCU\Console"
    except Exception:
        pass
    return info


def apply_windows_terminal_font(face_name: str) -> bool:
    """Write face_name into Windows Terminal settings.json profiles.defaults.font."""
    for settings_path in windows_terminal_settings_candidates():
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {}
            if settings_path.exists():
                payload = json.loads(settings_path.read_text(encoding="utf-8", errors="replace") or "{}")
            if not isinstance(payload, dict):
                payload = {}
            profiles = payload.setdefault("profiles", {})
            if not isinstance(profiles, dict):
                profiles = {}
                payload["profiles"] = profiles
            defaults = profiles.setdefault("defaults", {})
            if not isinstance(defaults, dict):
                defaults = {}
                profiles["defaults"] = defaults
            font_payload = defaults.setdefault("font", {})
            if not isinstance(font_payload, dict):
                font_payload = {}
                defaults["font"] = font_payload
            font_payload["face"] = face_name
            settings_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: font_terminal.py <path/to/font.otf> [face_name] [size]")
        sys.exit(1)
    _path = sys.argv[1]
    _face = sys.argv[2] if len(sys.argv) > 2 else ""
    _size = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    install_and_apply(_path, _face, _size)
