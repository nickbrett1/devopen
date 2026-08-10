"""CLI entrypoint: python3 -m devopen <repo> [--branch X]

Installed as /usr/local/bin/devopen by install.py.

With no repo argument, shows an interactive picker of already-cloned
workspaces (plus the option to paste a new URL).
"""

import argparse
import glob
import os
import subprocess
import sys

from . import config
from .opener import DevopenError, open_repo


def _interactive_pick(workspaces_dir):
    if not sys.stdin.isatty():
        print("No repo given and stdin is not interactive — pass a repo URL:", file=sys.stderr)
        print("  devopen nickbrett1/your-repo", file=sys.stderr)
        sys.exit(1)
    existing = sorted(
        d for d in glob.glob(os.path.join(workspaces_dir, "*"))
        if os.path.isdir(d) and os.path.isdir(os.path.join(d, ".git"))
    )
    print("Select a workspace (existing clone) or enter a new repo:")
    for i, d in enumerate(existing, 1):
        print(f"  [{i}] {os.path.basename(d)}")
    print("  [0] Enter a new repo URL")
    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not choice:
            continue
        if choice.isdigit():
            n = int(choice)
            if n == 0:
                url = input("Repo URL or owner/repo: ").strip()
                if url:
                    return url
                continue
            if 1 <= n <= len(existing):
                r = subprocess.run(
                    ["git", "-C", existing[n - 1], "remote", "get-url", "origin"],
                    capture_output=True, text=True,
                )
                return r.stdout.strip() or existing[n - 1]
            print("  Not in range — try again.")
        else:
            return choice


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="devopen",
        description="Clone a repo, start its devcontainer, and open a VS Code window.",
    )
    parser.add_argument("repo", nargs="?", default=None, help="Git repo URL or owner/repo (default: interactive picker)")
    parser.add_argument("-b", "--branch", default=None, help="Branch to check out")
    parser.add_argument(
        "--workspaces-dir", default=None,
        help="Override the workspaces directory (default from ~/.devopen/config.json)",
    )
    args = parser.parse_args(argv)

    repo = args.repo
    workspaces_dir = args.workspaces_dir
    if repo is None:
        if workspaces_dir is None:
            workspaces_dir = config.load()["workspaces_dir"]
        repo = _interactive_pick(workspaces_dir)

    try:
        open_repo(repo, branch=args.branch, workspaces_dir=workspaces_dir)
        return 0
    except DevopenError as e:
        print(f"[devopen] ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
