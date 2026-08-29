#!/usr/bin/env python3
"""Install the devopen CLI. Nothing listens on a port — it's a pure CLI.

Run it standalone (no clone needed):
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3

Overrides (env): DEVOPEN_HOME (default ~/DevOpen), DEVOPEN_WORKSPACES,
DEVOPEN_SKIP_UPDATE=1 (skip git pull of an existing checkout).
"""

import json
import os
import shutil
import subprocess
import sys

REPO_URL = "https://github.com/nickbrett1/devopen.git"


def sh(cmd, check=True):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check)


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


def main():
    home = os.path.expanduser("~")
    devopen_home = os.environ.get("DEVOPEN_HOME") or os.path.join(home, "DevOpen")
    install_dir = os.path.join(devopen_home, "devopen")
    workspaces_dir = os.environ.get("DEVOPEN_WORKSPACES") or os.path.join(devopen_home, "workspaces")
    config_path = os.path.join(home, ".devopen", "config.json")

    print(f"Installing devopen (CLI only — no port open)…\n  install dir:   {install_dir}\n  workspaces:    {workspaces_dir}")

    # 1. Clone / update the repo
    os.makedirs(devopen_home, exist_ok=True)
    if not os.path.isdir(os.path.join(install_dir, ".git")):
        print("\n[1/4] Cloning devopen repository…")
        sh(["git", "clone", REPO_URL, install_dir])
    elif os.environ.get("DEVOPEN_SKIP_UPDATE") != "1":
        print("\n[1/4] Updating devopen repository…")
        sh(["git", "-C", install_dir, "pull", "--ff-only"], check=False)
    else:
        print("\n[1/4] Skipping repo update (DEVOPEN_SKIP_UPDATE=1).")

    # 2. CLI script (no venv — the CLI is pure stdlib)
    print("\n[2/4] Installing CLI script…")
    _install_script(os.path.join(install_dir, "scripts", "devopen"), "/usr/local/bin/devopen")

    # 3. Config
    print("\n[3/4] Writing config…")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            pass
    cfg["install_dir"] = install_dir
    cfg["workspaces_dir"] = workspaces_dir
    _write_json_600(config_path, cfg)
    print(f"Config written to {config_path} (mode 600).")

    # 4. devcontainer CLI (needed by the opener)
    print("\n[4/4] Checking devcontainer CLI…")
    if shutil.which("devcontainer") is None:
        if shutil.which("npm") is None:
            print("⚠️  devcontainer CLI is missing and npm is not available — the opener will need it on first use.")
        else:
            print("Installing @devcontainers/cli (used by the opener)…")
            sh(["npm", "install", "-g", "@devcontainers/cli"], check=False)
    else:
        print(f"devcontainer CLI found: {shutil.which('devcontainer')}")

    print()
    print("=" * 62)
    print("  devopen installed successfully! 🎉")
    print("=" * 62)
    print(f"  Workspaces:   {workspaces_dir}")
    print()
    print("Use it:")
    print("  devopen                     # interactive picker (workspaces + all GitHub repos)")
    print("  devopen nickbrett1/your-repo")
    print("  ssh -t mac-studio devopen   # same picker from Blink")


if __name__ == "__main__":
    main()
