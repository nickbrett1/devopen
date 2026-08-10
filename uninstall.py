#!/usr/bin/env python3
"""Uninstall the devopen service.

Removes the LaunchAgent, /usr/local/bin scripts, and ~/.devopen config/logs.
Leaves ~/DevOpen (service code + workspaces) intact — those are user data;
remove manually with: rm -rf ~/DevOpen

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

    # 1. Unload the LaunchAgent
    print("Unloading LaunchAgent…")
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{PLIST_LABEL}"])
    if os.path.exists(plist_dest):
        os.remove(plist_dest)
        print(f"Removed {plist_dest}")

    # 2. Remove CLI scripts
    for dest in ("/usr/local/bin/devopen", "/usr/local/bin/devopen-server"):
        if os.path.exists(dest):
            try:
                os.remove(dest)
                print(f"Removed {dest}")
            except PermissionError:
                print(f"⚠️  Cannot remove {dest} (permissions). Run with sudo.")

    # 3. Remove config + logs
    if os.path.isdir(config_dir):
        shutil.rmtree(config_dir)
        print(f"Removed {config_dir}")

    print()
    print("Uninstall complete! 🎉")
    print(f"Service code and workspaces remain in {os.path.join(home, 'DevOpen')}.")
    print(f"To remove those too: rm -rf {os.path.join(home, 'DevOpen')}")


if __name__ == "__main__":
    main()
