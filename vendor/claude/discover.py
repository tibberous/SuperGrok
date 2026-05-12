#!/usr/bin/env python3
"""
discover.py — Azure OpenAI VS Code Extension environment discovery tool.
Scans PATH, ENV, registry, and common install dirs for developer tools.
Compile with: pyinstaller --onefile discover.py
"""

import os, sys, json, shutil, subprocess, platform
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

# ── Tool definitions ──────────────────────────────────────────────────────────
# Each entry: (display_name, executables, registry_keys, common_dirs)
TOOLS = [
    # Dev essentials
    ("Python",          ["python", "python3", "py"],          ["SOFTWARE\\Python"],              ["C:/Python*", "C:/Users/*/AppData/Local/Programs/Python/Python*"]),
    ("Git",             ["git"],                               ["SOFTWARE\\Git"],                 ["C:/Program Files/Git"]),
    ("Git Bash",        ["bash"],                              [],                               ["C:/Program Files/Git/bin", "C:/Program Files/Git/usr/bin"]),
    ("Node.js",         ["node"],                              ["SOFTWARE\\Node.js"],             ["C:/Program Files/nodejs"]),
    ("npm",             ["npm"],                               [],                               ["C:/Program Files/nodejs"]),
    ("Rust/cargo",      ["cargo", "rustc"],                   [],                               ["C:/Users/*/.cargo/bin"]),
    ("Go",              ["go"],                                ["SOFTWARE\\Go"],                  ["C:/Program Files/Go/bin"]),
    ("Java",            ["java", "javac"],                    ["SOFTWARE\\JavaSoft"],            ["C:/Program Files/Java/*", "C:/Program Files/Eclipse Adoptium/*"]),
    (".NET SDK",        ["dotnet"],                            ["SOFTWARE\\dotnet"],              ["C:/Program Files/dotnet"]),
    ("PHP",             ["php"],                               [],                               ["C:/php", "C:/xampp/php"]),
    ("Ruby",            ["ruby"],                              ["SOFTWARE\\RubyInstaller"],       ["C:/Ruby*"]),
    ("Perl",            ["perl"],                              ["SOFTWARE\\Perl"],                ["C:/Perl*/bin", "C:/Strawberry/perl/bin"]),
    # Build tools
    ("GCC",             ["gcc", "g++"],                       [],                               ["C:/MinGW/bin", "C:/msys64/mingw64/bin", "C:/Program Files/Git/usr/bin"]),
    ("Make",            ["make", "mingw32-make"],             [],                               ["C:/MinGW/bin", "C:/msys64/usr/bin"]),
    ("CMake",           ["cmake"],                            ["SOFTWARE\\Kitware\\CMake"],      ["C:/Program Files/CMake/bin"]),
    # Version control / SCM
    ("GitHub CLI",      ["gh"],                               [],                               ["C:/Program Files/GitHub CLI"]),
    # Editors / IDEs
    ("VS Code",         ["code"],                             ["SOFTWARE\\Microsoft\\VisualStudioCode"], ["C:/Program Files/Microsoft VS Code"]),
    ("Visual Studio",   ["devenv"],                           ["SOFTWARE\\Microsoft\\VisualStudio"], ["C:/Program Files/Microsoft Visual Studio"]),
    # Containers / virt
    ("Docker",          ["docker"],                           ["SOFTWARE\\Docker"],              ["C:/Program Files/Docker"]),
    ("WSL",             ["wsl"],                              [],                               ["C:/Windows/System32"]),
    # Databases
    ("MySQL/MariaDB",   ["mysql", "mysqladmin", "mariadb"],  ["SOFTWARE\\MySQL", "SOFTWARE\\MariaDB"], ["C:/Program Files/MySQL/*", "C:/xampp/mysql/bin"]),
    ("PostgreSQL",      ["psql"],                             ["SOFTWARE\\PostgreSQL"],          ["C:/Program Files/PostgreSQL/*/bin"]),
    ("Redis",           ["redis-cli", "redis-server"],        [],                               ["C:/Program Files/Redis"]),
    # Web servers
    ("nginx",           ["nginx"],                            [],                               ["C:/nginx", "C:/Program Files/nginx"]),
    ("Apache",          ["httpd", "apache2"],                 [],                               ["C:/xampp/apache/bin", "C:/Apache24/bin"]),
    # Media / imaging
    ("FFmpeg",          ["ffmpeg"],                           [],                               ["C:/ffmpeg/bin", "C:/Program Files/ffmpeg/bin"]),
    ("FFprobe",         ["ffprobe"],                          [],                               ["C:/ffmpeg/bin", "C:/Program Files/ffmpeg/bin"]),
    ("ImageMagick",     ["magick", "convert", "identify"],   ["SOFTWARE\\ImageMagick"],         ["C:/Program Files/ImageMagick*"]),
    # AI / ML
    ("Ollama",          ["ollama"],                           [],                               ["C:/Users/*/AppData/Local/Programs/Ollama"]),
    ("Conda/Anaconda",  ["conda"],                            [],                               ["C:/ProgramData/anaconda3/Scripts", "C:/Users/*/anaconda3/Scripts", "C:/Users/*/miniconda3/Scripts"]),
    # Shell / system utils
    ("PowerShell 7",    ["pwsh"],                             ["SOFTWARE\\Microsoft\\PowerShell\\7"], ["C:/Program Files/PowerShell/7"]),
    ("curl",            ["curl"],                             [],                               ["C:/Windows/System32", "C:/Program Files/Git/usr/bin"]),
    ("7-Zip",           ["7z"],                               ["SOFTWARE\\7-Zip"],               ["C:/Program Files/7-Zip"]),
    ("diff",            ["diff"],                             [],                               ["C:/Program Files/Git/usr/bin", "C:/msys64/usr/bin"]),
    # Remote / networking
    ("WinSCP",          ["winscp"],                           ["SOFTWARE\\Martin Prikryl\\WinSCP 2"], ["C:/Program Files (x86)/WinSCP", "C:/Program Files/WinSCP"]),
    ("PLink",           ["plink"],                            ["SOFTWARE\\SimonTatham\\PuTTY"],   ["C:/Program Files/PuTTY", "C:/Program Files (x86)/PuTTY"]),
    ("PSCP",            ["pscp"],                            [],                               ["C:/Program Files/PuTTY", "C:/Program Files (x86)/PuTTY"]),
    # Package managers
    ("Chocolatey",      ["choco"],                            [],                               ["C:/ProgramData/chocolatey/bin"]),
    ("winget",          ["winget"],                           [],                               ["C:/Users/*/AppData/Local/Microsoft/WindowsApps"]),
    ("pip",             ["pip", "pip3"],                      [],                               []),
    # Browsers
    ("Chrome",          ["chrome"],                           ["SOFTWARE\\Google\\Chrome"],      ["C:/Program Files/Google/Chrome/Application", "C:/Program Files (x86)/Google/Chrome/Application"]),
    ("Firefox",         ["firefox"],                          ["SOFTWARE\\Mozilla\\Firefox"],    ["C:/Program Files/Mozilla Firefox"]),
    # Email
    ("Thunderbird",     ["thunderbird"],                      ["SOFTWARE\\Mozilla\\Thunderbird"], ["C:/Program Files/Mozilla Thunderbird"]),
    # AI image gen
    ("Stable Diffusion", [],                                  [],                               ["C:/StableDiffusion*", "C:/Users/*/stable-diffusion*", "C:/Users/*/sd-webui*"]),
    # PyInstaller (meta!)
    ("PyInstaller",     ["pyinstaller"],                      [],                               []),
    # More runtimes / frameworks
    ("Ruby on Rails",   ["rails"],                            [],                               []),
    ("Rust",            ["rustc", "rustup"],                  [],                               ["C:/Users/*/.cargo/bin"]),
    ("Cargo",           ["cargo"],                            [],                               ["C:/Users/*/.cargo/bin"]),
    ("Go",              ["go"],                               ["SOFTWARE\\Go"],                  ["C:/Program Files/Go/bin"]),
    ("Deno",            ["deno"],                             [],                               ["C:/Users/*/AppData/Local/deno"]),
    ("Bun",             ["bun"],                              [],                               ["C:/Users/*/.bun/bin"]),
    ("pnpm",            ["pnpm"],                             [],                               []),
    ("yarn",            ["yarn"],                             [],                               []),
    # Data science
    ("Jupyter",         ["jupyter"],                          [],                               []),
    ("R",               ["Rscript", "R"],                     ["SOFTWARE\\R-core"],              ["C:/Program Files/R/R-*/bin"]),
    # Misc dev tools
    ("cmake",           ["cmake"],                            ["SOFTWARE\\Kitware\\CMake"],      ["C:/Program Files/CMake/bin"]),
    ("Vagrant",         ["vagrant"],                          ["SOFTWARE\\HashiCorp\\Vagrant"],  ["C:/HashiCorp/Vagrant/bin"]),
    ("Terraform",       ["terraform"],                        [],                               []),
    ("AWS CLI",         ["aws"],                              [],                               ["C:/Program Files/Amazon/AWSCLIV2"]),
    ("Azure CLI",       ["az"],                               [],                               ["C:/Program Files (x86)/Microsoft SDKs/Azure/CLI2/wbin"]),
    ("Heroku CLI",      ["heroku"],                           [],                               []),
    ("Stripe CLI",      ["stripe"],                           [],                               []),
    ("MongoDB",         ["mongod", "mongo", "mongosh"],       ["SOFTWARE\\MongoDB"],             ["C:/Program Files/MongoDB/Server/*/bin"]),
    ("SQLite",          ["sqlite3"],                          [],                               []),
    ("Redis",           ["redis-cli","redis-server"],         [],                               ["C:/Program Files/Redis"]),
    ("Elasticsearch",   ["elasticsearch"],                    [],                               ["C:/Program Files/Elastic/Elasticsearch/*"]),
    ("Newman",          ["newman"],                           [],                               []),
    ("kubectl",         ["kubectl"],                          [],                               ["C:/Program Files/Kubernetes/Minikube"]),
    ("Helm",            ["helm"],                             [],                               []),
    ("VirtualBox",      ["vboxmanage"],                       ["SOFTWARE\\Oracle\\VirtualBox"],  ["C:/Program Files/Oracle/VirtualBox"]),
    ("ngrok",           ["ngrok"],                            [],                               []),
    ("Packer",          ["packer"],                           [],                               []),
    ("SSH",             ["ssh","scp","sftp"],                 [],                               ["C:/Program Files/Git/usr/bin","C:/Windows/System32/OpenSSH"]),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

VERSION_FLAGS = {
    "putty":    ["-v"],
    "plink":    ["-V"],
    "pscp":     ["-V"],
    "winscp":   ["/?"],
    "nginx":    ["-v"],
    "java":     ["-version"],
    "javac":    ["-version"],
    "php":      ["-v"],
    "perl":     ["-v"],
    "curl":     ["-V"],
    "magick":   ["--version"],
    "convert":  ["--version"],
    "7z":       ["i"],
    "choco":    ["--version"],
}

VERSION_FROM_FILE_METADATA = {"putty", "plink", "pscp", "winscp", "winscp.com", "thunderbird", "firefox", "chrome"}

def file_version(path):
    if not IS_WINDOWS:
        return None
    try:
        import win32api
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms>>16}.{ms&0xFFFF}.{ls>>16}.{ls&0xFFFF}"
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f'(Get-Item "{path}").VersionInfo.FileVersion'],
            capture_output=True, text=True, timeout=5
        )
        v = r.stdout.strip()
        if v:
            return v
    except Exception:
        pass
    return None

REAL_EXE_FOR = {
    "winscp": ["C:/Program Files (x86)/WinSCP/WinSCP.exe", "C:/Program Files/WinSCP/WinSCP.exe"],
}

def which_version(exe):
    path = shutil.which(exe)
    if not path:
        return None, None
    real_path = path
    for alt in REAL_EXE_FOR.get(exe.lower(), []):
        if Path(alt).exists():
            real_path = alt
            break
    if exe.lower().split(".")[0] in VERSION_FROM_FILE_METADATA or real_path != path:
        v = file_version(real_path)
        return path, v or "(found, version unknown)"
    flags_to_try = VERSION_FLAGS.get(exe.lower(), ["--version", "-version", "-V", "version"])
    for flag in flags_to_try:
        try:
            r = subprocess.run([exe, flag], capture_output=True, text=True, timeout=3)
            out = (r.stdout + r.stderr).strip().splitlines()
            out = [l for l in out if l.strip() and len(l.strip()) > 3]
            if out:
                return path, out[0][:80]
        except Exception:
            pass
    return path, "(found, version unknown)"

def registry_check(keys):
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        for key in keys:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    winreg.OpenKey(hive, key)
                    return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

def glob_dirs(patterns):
    import glob
    for pattern in patterns:
        if glob.glob(pattern):
            return True
    return False

def check_tool(name, exes, reg_keys, dirs):
    for exe in exes:
        path, ver = which_version(exe)
        if path:
            return {"found": True, "exe": exe, "path": path, "version": ver, "via": "PATH"}
    if reg_keys and registry_check(reg_keys):
        return {"found": True, "path": "(registry)", "version": None, "via": "registry"}
    if dirs and glob_dirs(dirs):
        return {"found": True, "path": "(install dir found)", "version": None, "via": "filesystem"}
    return {"found": False}

# ── Main discovery ────────────────────────────────────────────────────────────

def discover():
    results = {}
    for (name, exes, reg_keys, dirs) in TOOLS:
        results[name] = check_tool(name, exes, reg_keys, dirs)

    env_snapshot = {
        k: v for k, v in os.environ.items()
        if any(x in k.upper() for x in ["PATH", "HOME", "USER", "TEMP", "PYTHON", "JAVA", "NODE", "GIT", "RUBY", "GO", "RUST", "CONDA", "DOCKER"])
    }

    return {
        "platform": platform.platform(),
        "python":   sys.version,
        "tools":    results,
        "env":      env_snapshot,
    }

def format_text(data):
    lines = []
    lines.append(f"Platform: {data['platform']}")
    lines.append(f"Python:   {data['python'].splitlines()[0]}")
    lines.append("")
    lines.append("── Installed Tools ──────────────────────────────────────────")
    found = [(n, v) for n, v in data["tools"].items() if v["found"]]
    missing = [n for n, v in data["tools"].items() if not v["found"]]
    for name, info in found:
        ver = f" ({info['version']})" if info.get("version") else ""
        lines.append(f"  ✓ {name:<22} {info.get('path','')}{ver}")
    lines.append("")
    lines.append("── Not Found ────────────────────────────────────────────────")
    for name in missing:
        lines.append(f"  ✗ {name}")
    return "\n".join(lines)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    data = discover()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
    else:
        print(format_text(data))
