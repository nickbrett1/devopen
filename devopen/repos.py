"""List the user's GitHub repos for the interactive picker.

Strategy (best-effort, no setup required):
  1. Pull the GitHub PAT from the macOS keychain via `git credential fill`
     (the same credential used for `git push`) and query
     /user/repos — this includes private repos and collaborator repos.
  2. Fall back to the unauthenticated /users/<username>/repos endpoint
     (public repos only).

API calls go through `curl` so they work even under a Python without SSL.
"""

import os
import subprocess

GITHUB_API = "https://api.github.com"


def _keychain_token():
    """Return the GitHub PAT stored in the macOS keychain, or None."""
    try:
        env = dict(os.environ)
        # `git credential fill` falls back to an interactive username/password
        # prompt on the terminal when no credential is stored, which blocks the
        # repo picker for the full timeout below. Disable prompts so the lookup
        # is silent: keychain hit -> token; miss -> return None immediately and
        # fall back to the unauthenticated API in list_repos().
        env["GIT_TERMINAL_PROMPT"] = "0"
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=10,
            env=env,
        )
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return None


def _api(url, token=None):
    """GET a GitHub API URL via curl, returning parsed JSON (or None)."""
    cmd = ["curl", "-fsSL", "-m", "15", "-H", "Accept: application/vnd.github+json"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    cmd += [url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        import json
        return json.loads(r.stdout)
    except Exception:
        return None


def list_repos(username="nickbrett1", limit=100):
    """Return up to `limit` repo full_names (owner/name), newest-updated first."""
    repos = []
    token = _keychain_token()
    try:
        if token:
            for page in range(1, 4):  # up to 300 repos
                data = _api(
                    f"{GITHUB_API}/user/repos?per_page=100&page={page}"
                    "&affiliation=owner,collaborator&sort=updated",
                    token=token,
                )
                if not data:
                    break
                repos.extend(r.get("full_name") for r in data if r.get("full_name"))
                if len(data) < 100:
                    break
        else:
            data = _api(f"{GITHUB_API}/users/{username}/repos?per_page=100&sort=updated")
            if data:
                repos.extend(r.get("full_name") for r in data if r.get("full_name"))
    except Exception:
        pass

    seen = set()
    out = []
    for name in repos:
        if name not in seen:
            seen.add(name)
            out.append(name)
    # stable, scannable order for the picker
    out.sort(key=str.lower)
    return out[:limit]
