"""CLI entrypoint: python3 -m devopen <repo> [--branch X]

Installed as /usr/local/bin/devopen by install.py.
"""

import argparse
import sys

from .opener import DevopenError, open_repo


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="devopen",
        description="Clone a repo, start its devcontainer, and open a VS Code window.",
    )
    parser.add_argument("repo", help="Git repository URL (https or git@…)")
    parser.add_argument("-b", "--branch", default=None, help="Branch to check out")
    parser.add_argument(
        "--workspaces-dir", default=None,
        help="Override the workspaces directory (default from ~/.devopen/config.json)",
    )
    args = parser.parse_args(argv)

    try:
        open_repo(args.repo, branch=args.branch, workspaces_dir=args.workspaces_dir)
        return 0
    except DevopenError as e:
        print(f"[devopen] ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
