#!/usr/bin/env python3
"""
unrar.py — Simple RAR extractor. Wraps 7-Zip or system unrar.
Compile with: pyinstaller --onefile unrar.py
"""

import sys, os, subprocess, shutil
from pathlib import Path

VERSION = "1.0.0"
NAME    = "unrar"

HELP = f"""
{NAME} v{VERSION} — RAR extraction utility
Author: Trent Tompkins <trenttompkins@gmail.com>
https://www.trentontompkins.com

USAGE:
  {NAME} <input.rar> <output_dir>

ARGUMENTS:
  input.rar     Path to the RAR archive to extract
  output_dir    Directory to extract files into (created if it doesn't exist)

EXAMPLES:
  {NAME} archive.rar C:\\extracted
  {NAME} "C:\\my files\\backup.rar" D:\\restore

NOTES:
  Requires 7-Zip (7z.exe) or unrar.exe to be installed and on PATH.
  7-Zip is preferred and supports RAR, RAR5, ZIP, 7Z, TAR, GZ, and more.
  Get 7-Zip at: https://www.7-zip.org

EXIT CODES:
  0   Success
  1   Bad arguments / usage error
  2   Input file not found
  3   Extraction failed
"""

HELP_FLAGS = {"-h", "--help", "help", "/?", "/help", "-?", "man"}
VER_FLAGS  = {"-v", "--version", "-ver", "/ver", "--ver", "version", "/version"}

def find_extractor():
    for exe in ["7z", "7za", "unrar"]:
        path = shutil.which(exe)
        if path:
            return exe, path
    # common install locations
    for path in [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        r"C:\ProgramData\chocolatey\bin\7z.exe",
    ]:
        if Path(path).exists():
            return "7z", path
    return None, None

def extract(input_path, output_dir, exe, exe_path):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if "7z" in exe:
        cmd = [exe_path, "x", input_path, f"-o{output_dir}", "-y"]
    else:
        cmd = [exe_path, "x", input_path, output_dir]
    r = subprocess.run(cmd, text=True)
    return r.returncode

def main():
    args = [a for a in sys.argv[1:]]

    if not args or any(a.lower() in HELP_FLAGS for a in args):
        print(HELP)
        sys.exit(0)

    if any(a.lower() in VER_FLAGS for a in args):
        print(f"{NAME} v{VERSION}")
        sys.exit(0)

    if len(args) != 2:
        print(f"Usage: {NAME} <input.rar> <output_dir>")
        print(f"Run '{NAME} --help' for full usage.")
        sys.exit(1)

    input_path, output_dir = args[0], args[1]

    if not Path(input_path).exists():
        print(f"Error: input file not found: {input_path}")
        sys.exit(2)

    exe, exe_path = find_extractor()
    if not exe:
        print("Error: no extractor found. Please install 7-Zip: https://www.7-zip.org")
        sys.exit(3)

    print(f"Extracting {input_path} -> {output_dir} (using {exe})")
    code = extract(input_path, output_dir, exe, exe_path)
    if code == 0:
        print("Done.")
    else:
        print(f"Extraction failed (exit code {code})")
        sys.exit(3)

if __name__ == "__main__":
    main()
