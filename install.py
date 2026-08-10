#!/usr/bin/env python3
"""Install the devopen service on macOS.

Clones the repo, creates a venv, installs the CLI scripts to /usr/local/bin,
writes ~/.devopen/config.json (with a fresh token), installs and loads the
LaunchAgent, and verifies /health.

Run it standalone (no clone needed):
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3

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
DEFAULT_PORT = 8787


def sh(cmd, check=True):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


def _write_json_600(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _install_script(src, dest):
    print(f"Installing {src} → {dest}")
    try:
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
    except PermissionError:
        print(f"⚠️  Cannot write {dest} (permissions). Run with sudo or copy manually:")
        print(f"    sudo cp {src} {dest} && sudo chmod 755 {dest}")


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
    home = os.path.expanduser("~")
    devopen_home = os.environ.get("DEVOPEN_HOME") or os.path.join(home, "DevOpen")
    install_dir = os.path.join(devopen_home, "devopen")
    workspaces_dir = os.environ.get("DEVOPEN_WORKSPACES") or os.path.join(devopen_home, "workspaces")
    config_dir = os.path.join(home, ".devopen")
    config_path = os.path.join(config_dir, "config.json")
    plist_dest = os.path.join(home, "Library", "LaunchAgents", f"{PLIST_LABEL}.plist")

    print(f"Installing devopen…\n  install dir:   {install_dir}\n  workspaces:    {workspaces_dir}")

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
    if not os.path.exists(venv_python):
        sh([sys.executable, "-m", "venv", venv_dir])
    sh([venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    sh([venv_python, "-m", "pip", "install", "--quiet", "-r", os.path.join(install_dir, "requirements.txt")])

    # 3. CLI scripts
    print("\n[3/7] Installing CLI scripts…")
    _install_script(os.path.join(install_dir, "scripts", "devopen"), "/usr/local/bin/devopen")
    _install_script(os.path.join(install_dir, "scripts", "devopen-server"), "/usr/local/bin/devopen-server")

    # 4. Config + token
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

    # 5. LaunchAgent plist
    print("\n[5/7] Installing LaunchAgent…")
    with open(os.path.join(install_dir, "com.nick.devopen.plist"), encoding="utf-8") as f:
        plist = f.read().replace("__HOME__", home)
    with open(plist_dest, "w", encoding="utf-8") as f:
        f.write(plist)
    print(f"LaunchAgent written to {plist_dest}.")

    # 6. Load the agent
    print("\n[6/7] Loading LaunchAgent…")
    uid = os.getuid()
    sh(["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"], check=False)
    sh(["launchctl", "bootstrap", f"gui/{uid}", plist_dest])
    sh(["launchctl", "kickstart", "-k", f"gui/{uid}/{PLIST_LABEL}"], check=False)

    # 7. devcontainer CLI + health check
    print("\n[7/7] Checking devcontainer CLI…")
    if shutil.which("devcontainer") is None:
        if shutil.which("npm") is None:
            print("⚠️  devcontainer CLI is missing and npm is not available — the opener will need it on first use.")
        else:
            print("Installing @devcontainers/cli (used by the opener)…")
            sh(["npm", "install", "-g", "@devcontainers/cli"], check=False)

    port = cfg["port"]
    print(f"Waiting for server on 127.0.0.1:{port}…")
    if not _wait_health(port):
        print("⚠️  Server did not respond to /health. Check ~/.devopen/server.err.log")

    print()
    print("=" * 62)
    print("  devopen installed successfully! 🎉")
    print("=" * 62)
    print(f"  Token:        {cfg['token']}")
    print(f"  Server:       http://{cfg['host']}:{port}")
    print(f"  Workspaces:   {workspaces_dir}")
    print()
    print("Trigger from your iPhone (Tailscale node: mac-studio):")
    print(f'  curl -X POST http://mac-studio:{port}/open \\')
    print(f"       -H 'Authorization: Bearer {cfg['token']}' \\")
    print(f'       -d \'{{"repo": "https://github.com/you/your-repo.git"}}\'')
    print(f"  Safari:  http://mac-studio:{port}/open?token={cfg['token']}&repo=https://github.com/you/your-repo.git")
    print("  Blink:   ssh mac-studio devopen https://github.com/you/your-repo.git")
    print()
    print("Keep the token safe — it is also in ~/.devopen/config.json.")


if __name__ == "__main__":
    main()
