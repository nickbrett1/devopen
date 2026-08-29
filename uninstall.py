#!/usr/bin/env python3
"""Uninstall the devopen CLI.

Removes /usr/local/bin/devopen and ~/.devopen config. Leaves ~/DevOpen
(service code + workspaces) intact — those are user data; remove manually
with: rm -rf ~/DevOpen

Run it standalone:
    curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/uninstall.py | python3
"""

import os
import shutil


def main():
    home = os.path.expanduser("~")
    config_dir = os.path.join(home, ".devopen")

    print("Uninstalling devopen…")

    # 1. CLI script
    cli = "/usr/local/bin/devopen"
    if os.path.exists(cli):
        try:
            os.remove(cli)
            print(f"Removed {cli}")
        except PermissionError:
            print(f"⚠️  Cannot remove {cli} (permissions). Run with sudo.")

    # 2. Config
    if os.path.isdir(config_dir):
        shutil.rmtree(config_dir)
        print(f"Removed {config_dir}")

    print()
    print("Uninstall complete! 🎉")
    print(f"Service code and workspaces remain in {os.path.join(home, 'DevOpen')}.")
    print(f"To remove those too: rm -rf {os.path.join(home, 'DevOpen')}")


if __name__ == "__main__":
    main()
