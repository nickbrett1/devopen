#!/usr/bin/env python3
"""Uninstall the devopen CLI and (if present) the HTTP server.

Removes /usr/local/bin scripts, the LaunchAgent + plist, and ~/.devopen
config/logs. Leaves ~/DevOpen (service code + workspaces) intact — those are
user data; remove manually with: rm -rf ~/DevOpen

Run it standalone:
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/uninstall.py | python3
"""

import os
import shutil
import subprocess

PLIST_LABEL = "com.nick.devopen"


def main():
    home = os.path.expanduser("~")
    uid = os.getuid()
    config_dir = os.path.join(home, ".devopen")
    plist_dest = os.path.join(home, "Library", "LaunchAgents", f"{PLIST_LABEL}.plist")

    print("Uninstalling devopen…")

    # 1. CLI script
    cli = "/usr/local/bin/devopen"
    if os.path.exists(cli):
        try:
            os.remove(cli)
            print(f"Removed {cli}")
        except PermissionError:
            print(f"⚠️  Cannot remove {cli} (permissions). Run with sudo.")

    # 2. HTTP server artifacts (if present)
    server_wrapper = "/usr/local/bin/devopen-server"
    if os.path.exists(plist_dest) or os.path.exists(server_wrapper):
        print("Removing HTTP server (LaunchAgent, wrapper)…")
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"])
        if os.path.exists(plist_dest):
            os.remove(plist_dest)
            print(f"Removed {plist_dest}")
        if os.path.exists(server_wrapper):
            try:
                os.remove(server_wrapper)
                print(f"Removed {server_wrapper}")
            except PermissionError:
                print(f"⚠️  Cannot remove {server_wrapper} (permissions). Run with sudo.")

    # 3. Config + logs
    if os.path.isdir(config_dir):
        shutil.rmtree(config_dir)
        print(f"Removed {config_dir}")

    print()
    print("Uninstall complete! 🎉")
    print(f"Service code and workspaces remain in {os.path.join(home, 'DevOpen')}.")
    print(f"To remove those too: rm -rf {os.path.join(home, 'DevOpen')}")


if __name__ == "__main__":
    main()
