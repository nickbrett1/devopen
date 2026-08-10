"""Core devopen logic: clone -> devcontainer up -> open VS Code window."""

import os
import re
import shutil
import subprocess
import time


class DevopenError(RuntimeError):
    """A user-facing failure in the open flow."""


def log_stdout(line):
    print(line, flush=True)


def _run(cmd, on_log=log_stdout):
    on_log("$ " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in (result.stdout or "").splitlines():
        on_log(line)
    if result.stderr:
        for line in (result.stderr or "").splitlines():
            on_log(line)
    if result.returncode != 0:
        raise DevopenError(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result


# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------

def docker_ready():
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        return r.returncode == 0
    except OSError:
        return False


def ensure_docker(on_log=log_stdout, timeout=180):
    """Make sure Docker is running, auto-launching Docker Desktop on macOS."""
    if docker_ready():
        return
    if shutil.which("open") is None:
        raise DevopenError("Docker is not running (and cannot be auto-launched on this OS).")
    on_log("Docker not running — launching Docker Desktop…")
    subprocess.run(["open", "-a", "Docker"], check=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker_ready():
            on_log("Docker is ready.")
            return
        time.sleep(3)
    raise DevopenError(f"Docker did not become ready within {timeout}s. Is Docker Desktop installed?")


def ensure_devcontainer_cli(on_log=log_stdout):
    """Ensure the devcontainer CLI is available, installing it via npm if needed."""
    found = shutil.which("devcontainer")
    if found:
        on_log(f"devcontainer CLI found: {found}")
        return found
    if shutil.which("npm") is None:
        raise DevopenError("devcontainer CLI is missing and npm is not available to install it.")
    on_log("devcontainer CLI not found — installing @devcontainers/cli via npm…")
    result = subprocess.run(
        ["npm", "install", "-g", "@devcontainers/cli"],
        capture_output=True, text=True,
    )
    for line in (result.stdout or "").splitlines():
        on_log(line)
    if result.stderr:
        for line in (result.stderr or "").splitlines():
            on_log(line)
    if result.returncode != 0:
        raise DevopenError("npm install @devcontainers/cli failed.")
    on_log("devcontainer CLI installed.")
    return shutil.which("devcontainer")


# --------------------------------------------------------------------------
# Repo handling
# --------------------------------------------------------------------------

def normalize_repo(repo):
    """Expand 'owner/repo' → 'https://github.com/owner/repo.git'; pass URLs through."""
    repo = (repo or "").strip()
    if not repo:
        return repo
    if repo.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        return repo
    if "/" in repo and not repo.startswith("/"):
        return f"https://github.com/{repo}" if repo.endswith(".git") else f"https://github.com/{repo}.git"
    return repo


def repo_dir_name(repo_url):
    """Derive a directory name from a repo URL."""
    name = repo_url.rstrip("/").split("/")[-1] or "workspace"
    return name[:-4] if name.endswith(".git") else name


def clone_or_update(workspaces_dir, repo_url, branch, on_log=log_stdout):
    """Clone the repo into workspaces_dir, or fetch/checkout if it exists."""
    os.makedirs(workspaces_dir, exist_ok=True)
    dest = os.path.join(workspaces_dir, repo_dir_name(repo_url))
    if not os.path.isdir(dest):
        on_log(f"Cloning {repo_url} → {dest}")
        cmd = ["git", "clone"]
        if branch:
            cmd += ["--branch", branch, "--single-branch"]
        cmd += [repo_url, dest]
        _run(cmd, on_log=on_log)
    else:
        on_log(f"Workspace exists: {dest} — fetching latest…")
        _run(["git", "-C", dest, "fetch", "--all", "--prune"], on_log=on_log)
        if branch:
            _run(["git", "-C", dest, "checkout", branch], on_log=on_log)
            _run(["git", "-C", dest, "pull", "--ff-only"], on_log=on_log)
        else:
            _run(["git", "-C", dest, "pull", "--ff-only"], on_log=on_log)
    return dest


# --------------------------------------------------------------------------
# Devcontainer + VS Code
# --------------------------------------------------------------------------

def _devcontainer_up(workspace_folder, on_log=log_stdout):
    cmd = [
        "devcontainer", "up",
        "--workspace-folder", workspace_folder,
        "--remove-existing-container",
    ]
    on_log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    container_id = None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            on_log(line)
        m = re.search(r"Container ID:\s*(\S+)", line)
        if m:
            container_id = m.group(1)
    rc = proc.wait()
    if rc != 0:
        raise DevopenError(f"devcontainer up failed (exit {rc}).")
    if not container_id:
        r = subprocess.run(["docker", "ps", "-q", "--latest"], capture_output=True, text=True)
        container_id = r.stdout.strip()
    if not container_id:
        raise DevopenError("Could not determine the container ID after devcontainer up.")
    return container_id


def _container_workspace_path(container_id, fallback_name):
    """Path of the workspace *inside* the container (for the vscode-remote URI)."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.WorkingDir}}", container_id],
            capture_output=True, text=True,
        )
        wd = r.stdout.strip()
        if wd:
            return wd
    except OSError:
        pass
    return f"/workspaces/{fallback_name}"


def open_in_vscode(container_id, workspace_folder, on_log=log_stdout):
    workspace_path = _container_workspace_path(container_id, os.path.basename(workspace_folder))
    uri = f"vscode-remote://dev-container+{container_id}{workspace_path}"
    on_log(f"Opening VS Code window: {uri}")
    subprocess.Popen(["code", "--new-window", "--folder-uri", uri])
    return uri


def open_repo(repo_url, branch=None, workspaces_dir=None, on_log=log_stdout):
    """Full open flow. Returns the vscode-remote URI on success."""
    repo_url = normalize_repo(repo_url)
    if not repo_url:
        raise DevopenError("repo URL is required.")
    if not workspaces_dir:
        from . import config
        workspaces_dir = config.load()["workspaces_dir"]

    ensure_docker(on_log=on_log)
    ensure_devcontainer_cli(on_log=on_log)
    folder = clone_or_update(workspaces_dir, repo_url, branch, on_log=on_log)
    on_log(f"Running devcontainer up on {folder} (first build can take minutes)…")
    container_id = _devcontainer_up(folder, on_log=on_log)
    on_log(f"Container ready: {container_id}")
    uri = open_in_vscode(container_id, folder, on_log=on_log)
    on_log("Done.")
    return uri
