#!/usr/bin/env python3
"""Reproduce a user-local FlClash compatibility repair, without root access."""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path, PurePosixPath


UBUNTU_BASE = "https://security.ubuntu.com/ubuntu/"
PACKAGE_INDEX = UBUNTU_BASE + "dists/jammy-security/main/binary-amd64/Packages.gz"
OLD_GLIB_SYMBOL = b"g_spawn_check_wait_status\0"
NEW_GLIB_SYMBOL = b"g_spawn_check_exit_status\0"
MARKER = "flclash-ubuntu20-compat"


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, text=True, **kwargs)


def clean_environment():
    env = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "LD_DEBUG", "LD_WARN",
                "LD_BIND_NOW", "LD_TRACE_LOADED_OBJECTS"):
        env.pop(key, None)
    return env


def libc_package(index):
    """Select the official amd64 libc6 entry, retaining its integrity metadata."""
    for stanza in index.split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields.get("Package") != "libc6" or fields.get("Architecture") != "amd64":
            continue
        filename = fields.get("Filename", "")
        path = PurePosixPath(filename)
        if (path.is_absolute() or ".." in path.parts
                or not filename.startswith("pool/main/g/glibc/")):
            raise ValueError("Unexpected Ubuntu package path")
        if not fields.get("Version", "").startswith("2.35-"):
            raise ValueError("Expected a GLIBC 2.35 package from Jammy")
        if not re.fullmatch(r"[0-9a-f]{64}", fields.get("SHA256", "")):
            raise ValueError("Missing or invalid package SHA256")
        if not fields.get("Size", "").isdigit() or int(fields["Size"]) <= 0:
            raise ValueError("Missing or invalid package size")
        return {k: fields[k] for k in ("Package", "Version", "Filename", "SHA256", "Size")}
    raise ValueError("Official package index contains no amd64 libc6 entry")


def verify_package(data, metadata):
    if len(data) != int(metadata["Size"]):
        raise ValueError("Downloaded libc6 package has an unexpected size")
    if hashlib.sha256(data).hexdigest() != metadata["SHA256"]:
        raise ValueError("Downloaded libc6 package failed SHA256 verification")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": MARKER + "/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def rewrite_glib_symbol(data):
    if len(OLD_GLIB_SYMBOL) != len(NEW_GLIB_SYMBOL):
        raise ValueError("GLib symbol lengths differ")
    count = data.count(OLD_GLIB_SYMBOL)
    if not count:
        if NEW_GLIB_SYMBOL in data:
            return data, 0
        raise ValueError("Unrecognized Wi-Fi plugin; refusing to patch it")
    return data.replace(OLD_GLIB_SYMBOL, NEW_GLIB_SYMBOL), count


def desktop_quote(value):
    # Desktop Entry Exec uses its own double-quote escaping, not shell quoting.
    value = value.replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value + '"'


def require_commands():
    missing = [name for name in ("apt-get", "dpkg-deb", "readelf")
               if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Required commands missing: " + ", ".join(missing))


def running_private_processes(root):
    processes = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return processes
    private_app = str(root / "app") + "/"
    for item in proc_root.iterdir():
        if not item.name.isdigit():
            continue
        try:
            if item.stat().st_uid == os.getuid():
                target = os.readlink(str(item / "exe"))
                if target.startswith(private_app):
                    processes.append(int(item.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return processes


def check(root):
    executable = root / "app/FlClash"
    if not executable.is_file():
        raise RuntimeError("Private FlClash is not installed at " + str(root))
    env = clean_environment()
    env.update(LD_TRACE_LOADED_OBJECTS="1", LD_WARN="1", LD_BIND_NOW="1")
    result = subprocess.run([str(executable)], env=env, text=True,
                            capture_output=True, timeout=20)
    output = result.stdout + result.stderr
    errors = ("not found", "undefined symbol", "symbol lookup error", "error while loading")
    if result.returncode != 0 or any(error in output for error in errors):
        raise RuntimeError("Dependency/relocation check failed:\n" + output)
    libc_lines = [line for line in output.splitlines() if line.strip().startswith("libc.so.6 ")]
    expected = str(root / "runtime/lib/x86_64-linux-gnu/libc.so.6")
    if not any(expected in line for line in libc_lines):
        raise RuntimeError("FlClash is not resolving the intended private libc")
    print("PASS: private GLIBC, shared libraries, and symbol relocations")
    print("This check does not start the GUI, proxy, or TUN.")


def diagnose(source):
    print("Platform:", platform.system(), platform.machine())
    print("Host libc:", " ".join(platform.libc_ver()))
    print("Source:", source)
    paths = [source / "FlClash", source / "FlClashHelperService"]
    paths.extend(sorted((source / "lib").glob("*.so")))
    for path in paths:
        if not path.is_file():
            continue
        result = run(["readelf", "--version-info", path], capture_output=True)
        versions = set(re.findall(r"GLIBC_\d+(?:\.\d+)+", result.stdout))
        required = max(versions, key=lambda v: tuple(map(int, v.split("_")[1].split("."))),
                       default="none")
        print(path.name + ":", required)


def backup_existing(path, backup):
    if path.is_symlink():
        (backup / path.name).symlink_to(os.readlink(str(path)))
    elif path.exists():
        shutil.copy2(str(path), str(backup / path.name))


def install(source, root):
    if os.getuid() == 0:
        raise RuntimeError("Run as your desktop user, without sudo")
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("This repair is limited to Linux x86_64")
    require_commands()
    for name in ("FlClash", "FlClashHelperService", "data", "lib"):
        if not (source / name).exists():
            raise RuntimeError("Incomplete FlClash source directory: " + str(source))
    if source == root or source in root.parents or root in source.parents:
        raise RuntimeError("Source and private runtime must be separate directories")
    pids = running_private_processes(root)
    if pids:
        raise RuntimeError("Exit FlClash before reinstalling; running PIDs: "
                           + ", ".join(map(str, pids)))

    for name in ("downloads", "runtime", "tools", "logs", "backups"):
        (root / name).mkdir(parents=True, exist_ok=True)
    print("Reading the official Ubuntu Jammy security package index...", flush=True)
    metadata = libc_package(gzip.decompress(fetch(PACKAGE_INDEX)).decode("utf-8"))
    data = fetch(UBUNTU_BASE + metadata["Filename"])
    verify_package(data, metadata)
    package = root / "downloads" / PurePosixPath(metadata["Filename"]).name
    package.write_bytes(data)
    run(["dpkg-deb", "-x", package, root / "runtime"])
    print("Verified private libc6:", metadata["Version"], flush=True)

    # apt-get download checks package integrity but does not install anything.
    run(["apt-get", "download", "patchelf", "libayatana-ido3-0.4-0"], cwd=str(root / "downloads"))
    for pattern, destination in (("patchelf_*.deb", "tools"),
                                 ("libayatana-ido3-0.4-0_*.deb", "runtime")):
        packages = sorted((root / "downloads").glob(pattern), key=lambda p: p.stat().st_mtime)
        if not packages:
            raise RuntimeError("APT did not provide " + pattern)
        run(["dpkg-deb", "-x", packages[-1], root / destination])

    print("Copying the application into the user-local runtime...", flush=True)
    app = root / "app"
    # Recreate only our managed app subtree, so an update cannot leave stale libraries.
    if app.exists():
        shutil.rmtree(str(app))
    shutil.copytree(str(source), str(app))
    patchelf = root / "tools/usr/bin/patchelf"
    loader = root / "runtime/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    rpath = ":".join((str(loader.parent), str(root / "runtime/usr/lib/x86_64-linux-gnu"),
                      "$ORIGIN/lib", "$ORIGIN"))
    for name in ("FlClash", "FlClashHelperService"):
        executable = app / name
        run([patchelf, "--set-interpreter", loader, executable])
        # patchelf 0.10 must remove RUNPATH first to create an inheritable RPATH.
        run([patchelf, "--remove-rpath", executable])
        run([patchelf, "--force-rpath", "--set-rpath", rpath, executable])
    for library in (app / "lib").glob("*.so"):
        dynamic = run(["readelf", "-d", library], capture_output=True).stdout
        if "RUNPATH" in dynamic or "RPATH" in dynamic:
            run([patchelf, "--remove-rpath", library])
    wifi = app / "lib/libwifi_ssid_plugin.so"
    patched, count = rewrite_glib_symbol(wifi.read_bytes())
    wifi.write_bytes(patched)
    check(root)

    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    launcher = Path.home() / ".local/bin/FlClash"
    desktop = data_home / "applications/FlClash.desktop"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / "backups" / stamp
    backup.mkdir()
    for path in (launcher, desktop):
        backup_existing(path, backup)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n# " + MARKER + "\n"
                        "unset LD_LIBRARY_PATH LD_PRELOAD\n"
                        "exec " + shlex.quote(str(app / "FlClash")) + ' "$@"\n')
    launcher.chmod(0o755)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nType=Application\nName=FlClash\n"
                       "GenericName=FlClash\nIcon=FlClash\nExec=" + desktop_quote(str(launcher))
                       + " %U\nTerminal=false\nCategories=Network;\nStartupNotify=true\n"
                       "StartupWMClass=com.follow.clash\n"
                       "MimeType=x-scheme-handler/clash;x-scheme-handler/clashmeta;x-scheme-handler/flclash;\n"
                       "Comment=FlClash with a private Ubuntu 20.04 compatibility runtime\n")
    desktop.chmod(0o644)
    if shutil.which("desktop-file-validate"):
        run(["desktop-file-validate", desktop])
    if shutil.which("update-desktop-database"):
        run(["update-desktop-database", desktop.parent])
    state = {"tool": MARKER, "source": str(source), "private_runtime_glibc": metadata["Version"],
             "libc6_sha256": metadata["SHA256"], "symbol_replacements": count,
             "launcher": str(launcher), "desktop_entry": str(desktop),
             "system_files_modified": False, "installed_at_utc": stamp}
    (root / "repair-state.json").write_text(json.dumps(state, indent=2) + "\n")
    print("Installed. Start FlClash from the application menu or", launcher)
    print("No GUI, proxy, or TUN has been started by this installer.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="install user-local compatibility files")
    action.add_argument("--check", action="store_true", help="check an existing private runtime without launching it")
    action.add_argument("--diagnose", action="store_true", help="report host and installed binary requirements")
    parser.add_argument("--source", type=Path, default=Path("/opt/FlClash"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    parser.add_argument("--root", type=Path, default=data_home / "flclash-compat")
    args = parser.parse_args(argv)
    source, root = args.source.expanduser().resolve(), args.root.expanduser().resolve()
    try:
        if args.check:
            check(root)
        elif args.diagnose:
            diagnose(source)
        else:
            install(source, root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("ERROR:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
