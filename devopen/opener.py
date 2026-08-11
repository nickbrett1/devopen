"""Core devopen logic: clone -> devcontainer up -> open VS Code window."""

import json
import os
import re
import shutil
import subprocess
import sys
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


def _devcontainer_workspace_folder(local_folder):
    """workspaceFolder from the repo's devcontainer config, if declared.

    This is the path of the workspace *inside* the container. Using the
    image's WorkingDir instead is wrong for images like node (WORKDIR is
    /home/node, but the repo lives at /workspaces/<name>), and a bogus
    in-container path makes VS Code's Dev Containers extension fail to
    resolve the folder (it can even surface as "docker: command not found"
    because the extension spawns docker with the bad path as its cwd).
    """
    candidates = [
        os.path.join(local_folder, ".devcontainer", "devcontainer.json"),
        os.path.join(local_folder, ".devcontainer.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            wf = cfg.get("workspaceFolder")
            if wf:
                return wf
        except (OSError, ValueError):
            pass
    return None


def _container_workspace_path(container_id, local_folder):
    fallback_name = os.path.basename(local_folder)
    wf = _devcontainer_workspace_folder(local_folder)
    if wf:
        return wf
    # Fallback: the container's WorkingDir, then the devcontainer CLI default.
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


def _docker_context_name():
    try:
        r = subprocess.run(["docker", "context", "show"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return None


def _vscode_devcontainer_uri(local_folder, workspace_path):
    """Build the vscode-remote URI the way VS Code's Dev Containers extension does.

    The container-id form (`dev-container+<id>/<path>`) resolves to a garbage
    workspace folder on some setups — observed on OrbStack + VS Code 1.132 —
    which makes the extension spawn docker with a corrupted cwd and surface as
    "docker: command not found". The extension's own host-path form —
    `dev-container+<hex-json of {hostPath, settings, configFile}>/<in-container
    workspaceFolder>` — resolves cleanly (this is the URI the extension stores
    for bind-mounted workspaces).
    """
    cfg_file = os.path.join(local_folder, ".devcontainer", "devcontainer.json")
    if not os.path.isfile(cfg_file):
        cfg_file = os.path.join(local_folder, ".devcontainer.json")
    obj = {
        "hostPath": local_folder,
        "localDocker": False,
        "settings": {"context": _docker_context_name() or "orbstack"},
    }
    if os.path.isfile(cfg_file):
        obj["configFile"] = {
            "$mid": 1,
            "fsPath": cfg_file,
            "external": "file://" + cfg_file,
            "path": cfg_file,
            "scheme": "file",
        }
    authority = "dev-container+" + json.dumps(obj, separators=(",", ":")).encode("utf-8").hex()
    return f"vscode-remote://{authority}{workspace_path}"


def open_in_vscode(container_id, workspace_folder, on_log=log_stdout):
    workspace_path = _container_workspace_path(container_id, workspace_folder)
    uri = _vscode_devcontainer_uri(workspace_folder, workspace_path)
    on_log(f"Opening VS Code window: {uri}")
    subprocess.Popen(["code", "--new-window", "--folder-uri", uri])
    return uri


def tailscale_state(container_id):
    """Best-effort probe: 'connected' | 'logged_out' | 'absent'."""
    try:
        r = subprocess.run(
            ["docker", "exec", "-u", "root", container_id, "sh", "-c",
             "command -v tailscale >/dev/null 2>&1 && pgrep -x tailscaled >/dev/null 2>&1"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return "absent"
        r = subprocess.run(
            ["docker", "exec", "-u", "root", container_id, "tailscale", "status"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and "Logged out" not in r.stdout and r.stdout.strip():
            return "connected"
        return "logged_out"
    except Exception:
        return "absent"


def _prompt_tailscale():
    """Ask the user (only when stdin is interactive)."""
    try:
        if not sys.stdin.isatty():
            return False
        answer = input("Register this container on Tailscale? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def tailscale_up(container_id, hostname, authkey=None, on_log=log_stdout):
    """Best-effort registration of the container on Tailscale (optional).

    Runs `tailscale up` inside the container. With an authkey this is
    non-interactive; without one, tailscale prints a login URL that you open
    in a browser (state then persists in the container's tailscale volume, so
    future container recreations — e.g. devopen's --remove-existing-container —
    stay registered automatically).

    Returns True if connected/skipped-already, False if it failed.
    """
    def exec_in(cmd):
        return subprocess.run(
            ["docker", "exec", "-u", "root", container_id] + cmd,
            capture_output=True, text=True, timeout=30,
        )

    # Wait briefly for the tailscale binary + daemon (postCreate starts it).
    deadline = time.monotonic() + 30
    ready = False
    while time.monotonic() < deadline:
        r = exec_in(["sh", "-c", "command -v tailscale >/dev/null 2>&1 && pgrep -x tailscaled >/dev/null 2>&1"])
        if r.returncode == 0:
            ready = True
            break
        time.sleep(2)
    if not ready:
        on_log("[devopen] tailscale: not available in this container — skipping.")
        return False

    r = exec_in(["tailscale", "status"])
    if r.returncode == 0 and "Logged out" not in r.stdout and r.stdout.strip():
        on_log(f"[devopen] tailscale: already connected ({r.stdout.strip().splitlines()[0]}).")
        return True

    on_log(f"[devopen] tailscale: registering on the tailnet as '{hostname}'…")
    cmd = ["tailscale", "up", "--hostname=" + hostname]
    if authkey:
        cmd.append("--authkey=" + authkey)
        on_log("[devopen] tailscale: using authkey (non-interactive).")
    else:
        on_log("[devopen] tailscale: no authkey — a login URL will appear below; open it in a browser.")

    proc = subprocess.Popen(
        ["docker", "exec", "-u", "root", container_id] + cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            on_log(line)
    rc = proc.wait()
    if rc != 0:
        on_log(f"[devopen] tailscale: 'tailscale up' failed (exit {rc}).")
        return False
    on_log("[devopen] tailscale: registered ✓")
    return True


def open_repo(repo_url, branch=None, workspaces_dir=None, on_log=log_stdout,
              tailscale=None, tailscale_authkey=None, tailscale_hostname=None):
    """Full open flow. Returns the vscode-remote URI on success.

    tailscale: True = always register; False = never; None = ask on the CLI
    (only when the container has tailscale and is logged out).
    """
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
    hostname = tailscale_hostname or repo_dir_name(repo_url)
    if tailscale is True:
        tailscale_up(container_id, hostname, authkey=tailscale_authkey, on_log=on_log)
    elif tailscale is None:
        state = tailscale_state(container_id)
        if state == "connected":
            on_log(f"[devopen] tailscale: already connected as '{hostname}'.")
        elif state == "logged_out":
            on_log("[devopen] tailscale: container has tailscale but is not registered.")
            if _prompt_tailscale():
                tailscale_up(container_id, hostname, authkey=tailscale_authkey, on_log=on_log)
            else:
                on_log("[devopen] tailscale: skipped (use --tailscale to register, --no-tailscale to silence).")
        # 'absent' → silently skip
    on_log("Done.")
    return uri
