"""CLI entrypoint: python3 -m devopen <repo> [--branch X]

Installed as /usr/local/bin/devopen by install.py.

With no repo argument, shows an interactive picker: already-cloned
workspaces first, then all of your GitHub repos, then a free-text option.
"""

import argparse
import glob
import os
import subprocess
import sys
import warnings

# subprocess text-mode pipes leave unclosed TextIOWrappers until GC; silence
# the resulting shutdown noise (CPython quirk, harmless in a short-lived CLI).
warnings.filterwarnings("ignore", category=ResourceWarning)

from . import config
from .opener import DevopenError, open_repo


def _workspace_entries(workspaces_dir):
    """(label, origin_url) for every already-cloned workspace."""
    entries = []
    for d in sorted(glob.glob(os.path.join(workspaces_dir, "*")), key=lambda p: os.path.basename(p).lower()):
        if not (os.path.isdir(d) and os.path.isdir(os.path.join(d, ".git"))):
            continue
        url = d
        try:
            r = subprocess.run(
                ["git", "-C", d, "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                url = r.stdout.strip()
        except Exception:
            pass
        entries.append((os.path.basename(d), url))
    return entries


def _github_entries(workspaces_dir, cfg):
    """(label, url) for GitHub repos, skipping ones already in workspaces."""
    try:
        from .repos import list_repos
        names = list_repos(cfg.get("github_username", "nickbrett1"), limit=100)
    except Exception:
        return []
    have = {os.path.basename(d) for d, _ in _workspace_entries(workspaces_dir)}
    return [
        (name, f"https://github.com/{name}.git")
        for name in names
        if name.split("/")[-1] not in have
    ]


def _interactive_pick(workspaces_dir):
    if not sys.stdin.isatty():
        print("No repo given and stdin is not interactive — pass a repo URL:", file=sys.stderr)
        print("  devopen nickbrett1/your-repo", file=sys.stderr)
        sys.exit(1)

    cfg = config.load()
    workspaces = _workspace_entries(workspaces_dir)
    github = _github_entries(workspaces_dir, cfg)

    print("Pick a repo to open:")
    if workspaces:
        print("\n  Workspaces (already cloned):")
        for i, (label, _url) in enumerate(workspaces, 1):
            print(f"    [{i:>2}] {label}")
    if github:
        start = len(workspaces) + 1
        print("\n  Your GitHub repos:")
        for i, (label, _url) in enumerate(github, start):
            print(f"    [{i:>2}] {label}")
    print("\n    [ 0] Enter a repo URL")
    print("    [ q] Quit")
    print()

    entries = workspaces + github
    while True:
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            sys.exit(0)
        if not choice:
            continue
        if choice in ("q", "quit", "exit"):
            print("Bye.")
            sys.exit(0)
        if choice.isdigit():
            n = int(choice)
            if n == 0:
                url = input("Repo URL or owner/repo: ").strip()
                if url:
                    return url
                continue
            if 1 <= n <= len(entries):
                return entries[n - 1][1]
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
    parser.add_argument(
        "--tailscale", action="store_true",
        help="Register the container on Tailscale after opening (hostname = repo name)",
    )
    parser.add_argument(
        "--authkey", default=None,
        help="Tailscale authkey (default: DEVOPEN_TAILSCALE_AUTHKEY env or config 'tailscale_authkey')",
    )
    parser.add_argument(
        "--hostname", default=None,
        help="Tailscale hostname (default: repo name)",
    )
    args = parser.parse_args(argv)

    repo = args.repo
    workspaces_dir = args.workspaces_dir
    if repo is None:
        if workspaces_dir is None:
            workspaces_dir = config.load()["workspaces_dir"]
        repo = _interactive_pick(workspaces_dir)

    cfg = config.load()
    authkey = args.authkey or os.environ.get("DEVOPEN_TAILSCALE_AUTHKEY") or cfg.get("tailscale_authkey")
    try:
        open_repo(
            repo, branch=args.branch, workspaces_dir=workspaces_dir,
            tailscale=args.tailscale, tailscale_authkey=authkey,
            tailscale_hostname=args.hostname,
        )
        return 0
    except DevopenError as e:
        print(f"[devopen] ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
