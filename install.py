#!/usr/bin/env python3
"""Install the devopen CLI (and optionally the HTTP server).

Default: CLI only (`devopen` + interactive picker) — nothing listens on a port.
Add `--with-server` (or set DEVOPEN_ENABLE_SERVER=1) to also install the
FastAPI server + LaunchAgent for iPhone one-tap / Shortcut / genproj hooks.

Run it standalone (no clone needed):
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3 - --with-server

Overrides (env): DEVOPEN_HOME (default ~/DevOpen), DEVOPEN_WORKSPACES,
DEVOPEN_SKIP_UPDATE=1 (skip git pull of an existing checkout).
"""

import json
import os
import secrets
import shutil
import subprocess
import sys

REPO_URL = "https://github.com/nickbrett1/devopen.git"
PLIST_LABEL = "com.nick.devopen"
DEFAULT_HOST = "0.0.0.0"
# NOTE: 8787 is used by VS Code's Code Helper process, so we use 8797.
DEFAULT_PORT = 8797


def sh(cmd, check=True):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def _python_with_ssl():
    """Return a python3 that can actually do HTTPS (import ssl works).

    pyenv builds sometimes lack SSL support, which silently breaks pip.
    """
    candidates = [sys.executable, "/opt/homebrew/bin/python3", "/usr/bin/python3"]
    seen = set()
    for cand in candidates:
        if not cand or cand in seen or not os.path.exists(cand):
            continue
        seen.add(cand)
        try:
            r = subprocess.run(
                [cand, "-c", "import ssl"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return None


def _install_script(src, dest):
    print(f"Installing {src} → {dest}")
    try:
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
    except PermissionError:
        print(f"⚠️  Cannot write {dest} (permissions). Run with sudo or copy manually:")
        print(f"    sudo cp {src} {dest} && sudo chmod 755 {dest}")


def _write_json_600(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True


def _wait_health(port, attempts=30, delay=1):
    import time
    import urllib.request
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def main():
    enable_server = "--with-server" in sys.argv or os.environ.get("DEVOPEN_ENABLE_SERVER") == "1"
    home = os.path.expanduser("~")
    devopen_home = os.environ.get("DEVOPEN_HOME") or os.path.join(home, "DevOpen")
    install_dir = os.path.join(devopen_home, "devopen")
    workspaces_dir = os.environ.get("DEVOPEN_WORKSPACES") or os.path.join(devopen_home, "workspaces")
    config_dir = os.path.join(home, ".devopen")
    config_path = os.path.join(config_dir, "config.json")
    plist_dest = os.path.join(home, "Library", "LaunchAgents", f"{PLIST_LABEL}.plist")

    mode = "CLI + HTTP server" if enable_server else "CLI only (no port open)"
    print(f"Installing devopen ({mode})…\n  install dir:   {install_dir}\n  workspaces:    {workspaces_dir}")

    # 1. Clone / update the repo
    os.makedirs(devopen_home, exist_ok=True)
    if not os.path.isdir(os.path.join(install_dir, ".git")):
        print("\n[1/7] Cloning devopen repository…")
        sh(["git", "clone", REPO_URL, install_dir])
    elif os.environ.get("DEVOPEN_SKIP_UPDATE") != "1":
        print("\n[1/7] Updating devopen repository…")
        sh(["git", "-C", install_dir, "pull", "--ff-only"], check=False)
    else:
        print("\n[1/7] Skipping repo update (DEVOPEN_SKIP_UPDATE=1).")

    # 2. Python venv + dependencies
    venv_dir = os.path.join(install_dir, ".venv")
    venv_python = os.path.join(venv_dir, "bin", "python")
    print("\n[2/7] Setting up Python environment…")
    py = _python_with_ssl()
    if not py:
        print("⚠️  No Python with SSL support found (pip needs it). Install one, e.g. `brew install python`.")
        sys.exit(1)
    if os.path.exists(venv_python):
        r = subprocess.run([venv_python, "-c", "import ssl"], capture_output=True)
        if r.returncode != 0:
            print(f"Existing venv at {venv_dir} has a broken SSL module — recreating with {py}…")
            shutil.rmtree(venv_dir)
    if not os.path.exists(venv_python):
        sh([py, "-m", "venv", venv_dir])
    print(f"Using {py} → venv at {venv_dir}")
    try:
        sh([venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        sh([venv_python, "-m", "pip", "install", "--quiet", "-r", os.path.join(install_dir, "requirements.txt")])
    except subprocess.CalledProcessError:
        print(f"⚠️  pip install failed — try manually: {venv_python} -m pip install -r {install_dir}/requirements.txt")
        sys.exit(1)

    # 3. CLI script (always)
    print("\n[3/7] Installing CLI script…")
    _install_script(os.path.join(install_dir, "scripts", "devopen"), "/usr/local/bin/devopen")

    # 4. Config + token (token only matters if the server is enabled later)
    print("\n[4/7] Writing config + token…")
    os.makedirs(config_dir, exist_ok=True)
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            pass
    cfg.setdefault("token", secrets.token_hex(24))
    cfg["install_dir"] = install_dir
    cfg["workspaces_dir"] = workspaces_dir
    cfg.setdefault("host", DEFAULT_HOST)
    cfg.setdefault("port", DEFAULT_PORT)
    _write_json_600(config_path, cfg)
    print(f"Config written to {config_path} (mode 600).")

    # 5. HTTP server (optional)
    if enable_server:
        print("\n[5/7] Installing HTTP server (LaunchAgent)…")
        _install_script(
            os.path.join(install_dir, "scripts", "devopen-server"),
            "/usr/local/bin/devopen-server",
        )
        if _port_in_use(cfg["port"]):
            print(f"⚠️  Port {cfg['port']} is already in use — the server will fail to start.")
            print("   Pick a free port: edit \"port\" in " + config_path + " and rerun.")
        with open(os.path.join(install_dir, "com.nick.devopen.plist"), encoding="utf-8") as f:
            plist = f.read().replace("__HOME__", home)
        with open(plist_dest, "w", encoding="utf-8") as f:
            f.write(plist)
        print(f"LaunchAgent written to {plist_dest}.")
        uid = os.getuid()
        sh(["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"], check=False)
        sh(["launchctl", "bootstrap", f"gui/{uid}", plist_dest])
        sh(["launchctl", "kickstart", "-k", f"gui/{uid}/{PLIST_LABEL}"], check=False)
    else:
        print("\n[5/7] Skipping HTTP server (CLI-only mode).")
        print("   Re-run with `--with-server` (or DEVOPEN_ENABLE_SERVER=1) to enable one-tap / Shortcut.")

    # 6. devcontainer CLI (needed by the opener regardless of mode)
    print("\n[6/7] Checking devcontainer CLI…")
    if shutil.which("devcontainer") is None:
        if shutil.which("npm") is None:
            print("⚠️  devcontainer CLI is missing and npm is not available — the opener will need it on first use.")
        else:
            print("Installing @devcontainers/cli (used by the opener)…")
            sh(["npm", "install", "-g", "@devcontainers/cli"], check=False)
    else:
        print(f"devcontainer CLI found: {shutil.which('devcontainer')}")

    # 7. Health check (server mode only)
    if enable_server:
        port = cfg["port"]
        print(f"\n[7/7] Waiting for server on 127.0.0.1:{port}…")
        if not _wait_health(port):
            print("⚠️  Server did not respond to /health. Check ~/.devopen/server.err.log")
    else:
        print("\n[7/7] Skipping health check (no server in CLI-only mode).")

    print()
    print("=" * 62)
    print("  devopen installed successfully! 🎉")
    print("=" * 62)
    print(f"  Mode:         {mode}")
    if enable_server:
        print(f"  Token:        {cfg['token']}")
        print(f"  Server:       http://{cfg['host']}:{cfg['port']}")
    print(f"  Workspaces:   {workspaces_dir}")
    print()
    print("Use it:")
    print("  devopen                     # interactive picker (workspaces + all GitHub repos)")
    print("  devopen nickbrett1/your-repo")
    print("  ssh -t mac-studio devopen   # same picker from Blink")
    if enable_server:
        print()
        print("Trigger from your iPhone (Tailscale node: mac-studio):")
        print(f'  curl -X POST http://mac-studio:{cfg["port"]}/open \\')
        print(f"       -H 'Authorization: Bearer {cfg['token']}' \\")
        print(f'       -d \'{{"repo": "https://github.com/you/your-repo.git"}}\'')
        print(f"  Safari:  http://mac-studio:{cfg['port']}/open?token={cfg['token']}&repo=…")
        print("  Keep the token safe — it is also in ~/.devopen/config.json.")
    else:
        print()
        print("HTTP server not installed (CLI-only). To enable the iPhone one-tap / Shortcut path:")
        print("  curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3 - --with-server")


if __name__ == "__main__":
    main()
