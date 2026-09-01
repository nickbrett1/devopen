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


def _prompt_rebase(local_count=""):
    """Ask whether to rebase local commits onto origin (interactive only).

    Default is N — devopen must never rewrite history without consent.
    Non-interactive runs (plain ssh, scripts, HTTP server) skip the prompt
    and continue with the local state.
    """
    try:
        if not sys.stdin.isatty():
            return False
        suffix = ""
        if local_count:
            noun = "commit" if local_count == "1" else "commits"
            suffix = f" ({local_count} local {noun} not on origin)"
        answer = input(f"[devopen] local branch diverged{suffix} — rebase onto origin? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _untracked_overwrites(dest, upstream="@{upstream}"):
    """Untracked files in the working tree that would be clobbered by
    bringing in `upstream` (tracked there but not locally) — e.g. a
    locally generated package-lock.json that origin now tracks."""
    try:
        changed = subprocess.run(
            ["git", "-C", dest, "diff", "--name-only", "HEAD", upstream],
            capture_output=True, text=True, timeout=15,
        )
        if changed.returncode != 0:
            return []
        incoming = set(changed.stdout.splitlines())
    except Exception:
        return []
    try:
        untracked = subprocess.run(
            ["git", "-C", dest, "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=15,
        )
        if untracked.returncode != 0:
            return []
        return sorted(set(untracked.stdout.splitlines()) & incoming)
    except Exception:
        return []


def _prompt_remove_untracked(files):
    """Ask whether to delete untracked files that origin now tracks.

    Default is N — devopen never deletes untracked work without consent.
    """
    try:
        if not sys.stdin.isatty():
            return False
        print("[devopen] untracked file(s) would be overwritten by origin:")
        for f in files:
            print(f"        {f}")
        answer = input("Remove them and continue? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _ff_pull_or_rebase(dest, on_log=log_stdout):
    """Update the workspace without hard-failing on divergence or collisions.

    Tries `git pull --ff-only` first. If the local branch has diverged
    (local commits not on origin), offer an interactive rebase (preserves
    the local commits, autostash protects uncommitted changes). If the pull
    failed because untracked files would be overwritten by origin (e.g. a
    locally generated package-lock.json that origin now tracks), offer to
    remove them and retry. Otherwise warn and continue with the local state
    so the container still opens.
    Returns True if the workspace is up to date with origin, False if it
    is running on diverged local state.
    """
    r = subprocess.run(["git", "-C", dest, "pull", "--ff-only"], capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        on_log(line)
    if r.returncode != 0:
        for line in (r.stderr or "").splitlines():
            on_log(line)
        if not (r.stdout or "").strip() and not (r.stderr or "").strip():
            on_log(f"[devopen] git pull --ff-only failed (exit {r.returncode}).")
    if r.returncode == 0:
        return True

    # Untracked files that origin now tracks would block the pull (autostash
    # does NOT stash untracked files). Offer to remove them and retry.
    conflicts = _untracked_overwrites(dest)
    if conflicts:
        if _prompt_remove_untracked(conflicts):
            ok = True
            for f in conflicts:
                path = os.path.join(dest, f)
                on_log(f"[devopen] removing untracked {f}")
                try:
                    os.remove(path)
                except OSError as e:
                    on_log(f"[devopen] could not remove {f}: {e}")
                    ok = False
            if ok:
                r2 = subprocess.run(["git", "-C", dest, "pull", "--ff-only"], capture_output=True, text=True)
                for line in (r2.stdout or "").splitlines():
                    on_log(line)
                if r2.returncode != 0:
                    for line in (r2.stderr or "").splitlines():
                        on_log(line)
                if r2.returncode == 0:
                    on_log("[devopen] synced after removing untracked files.")
                    return True
        else:
            on_log("[devopen] untracked files would be overwritten by origin — continuing with local state. "
                   "Remove them (or run 'git pull --ff-only') to sync.")
            return False

    count = ""
    try:
        c = subprocess.run(
            ["git", "-C", dest, "rev-list", "--count", "@{upstream}..HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if c.returncode == 0:
            count = c.stdout.strip()
    except Exception:
        pass

    if _prompt_rebase(count):
        on_log(f"[devopen] rebasing {count or 'local'} commit(s) onto origin (autostash)…")
        r2 = subprocess.run(
            ["git", "-C", dest, "pull", "--rebase", "--autostash"],
            capture_output=True, text=True,
        )
        for line in (r2.stdout or "").splitlines():
            on_log(line)
        if r2.returncode != 0:
            for line in (r2.stderr or "").splitlines():
                on_log(line)
        if r2.returncode == 0:
            on_log("[devopen] rebase complete.")
            return True
        subprocess.run(["git", "-C", dest, "rebase", "--abort"], capture_output=True, text=True)
        on_log("[devopen] rebase failed (conflicts?) — aborted; continuing with local state.")
    else:
        on_log("[devopen] local branch has diverged from origin — continuing with local state "
               "(run 'git pull --rebase' in the workspace to sync).")
    return False


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
        _ff_pull_or_rebase(dest, on_log=on_log)
    return dest


# --------------------------------------------------------------------------
# Devcontainer + VS Code
# --------------------------------------------------------------------------

def _devcontainer_up(workspace_folder, on_log=log_stdout, no_cache=False):
    cmd = [
        "devcontainer", "up",
        "--workspace-folder", workspace_folder,
        "--remove-existing-container",
    ]
    if no_cache:
        cmd.append("--build-no-cache")
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


def _find_existing_container(local_folder):
    """Full id of the newest existing container for this workspace folder
    (created by devopen or the Dev Containers extension), or None.

    Matches by the devcontainer.local_folder label first, then falls back to
    any container whose bind-mount source equals local_folder (covers label /
    path mismatches, e.g. resolved symlinks), so a running container is
    reliably found before a rebuild."""
    def by_label():
        r = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=devcontainer.local_folder={local_folder}"],
            capture_output=True, text=True, timeout=15,
        )
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return lines[0] if lines else None  # docker ps -a lists newest first

    try:
        hit = by_label()
        if hit:
            return hit
    except Exception:
        pass
    # Fallback: a container whose bind-mount source is this workspace folder.
    try:
        r = subprocess.run(["docker", "ps", "-aq"], capture_output=True, text=True, timeout=15)
        for cid in r.stdout.split():
            try:
                out = subprocess.run(
                    ["docker", "inspect", "-f", "{{range .Mounts}}{{.Source}}{{end}}", cid],
                    capture_output=True, text=True, timeout=15,
                )
                if local_folder in (out.stdout or ""):
                    return cid
            except Exception:
                continue
    except Exception:
        pass
    return None


def _container_running(container_id):
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip() == "true"
    except Exception:
        return False


def _stop_and_remove_container(container_id, on_log=log_stdout):
    """Cleanly stop + remove an existing container before a rebuild.

    A running container can hold the workspace's forwarded ports, named
    volumes, or stale ssh/tailscale sessions, so a rebuild while it is open
    can fail (devcontainer up --remove-existing-container removes it late and
    the freshly-built container then conflicts on ports/name). Stop + remove
    it first — this "closes" the old container before the new one opens. The
    bind-mounted workspace and named volumes survive a container removal."""
    if not container_id:
        return
    try:
        if _container_running(container_id):
            on_log(f"[devopen] stopping existing container {container_id[:12]}…")
            subprocess.run(
                ["docker", "stop", "-t", "10", container_id],
                capture_output=True, text=True, timeout=60,
            )
        on_log(f"[devopen] removing existing container {container_id[:12]}…")
        subprocess.run(
            ["docker", "rm", container_id],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        on_log(f"[devopen] could not stop/remove existing container {container_id[:12]}: {e}")


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
    def exec_in(cmd):
        try:
            return subprocess.run(
                ["docker", "exec", "-u", "root", container_id] + cmd,
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            return None

    # tailscale binary present at all?
    r = exec_in(["sh", "-c", "command -v tailscale >/dev/null 2>&1"])
    if not r or r.returncode != 0:
        return "absent"

    # Ensure the daemon is up — plain `docker start` doesn't run postStart,
    # which is what normally starts tailscaled.
    exec_in(["sh", "-c",
             "pgrep -x tailscaled >/dev/null 2>&1 || "
             "(sudo start-stop-daemon --start --background --oknodo "
             "--pidfile /var/run/tailscaled.pid --make-pidfile "
             "--exec /usr/sbin/tailscaled -- --state=/var/lib/tailscale/tailscaled.state)"])
    for _ in range(5):  # daemon may take a second to come up
        r = exec_in(["tailscale", "status"])
        if r is None:
            break
        out = (r.stdout or "") + (r.stderr or "")
        if "failed to connect to local tailscaled" in out:
            time.sleep(1)
            continue
        if r.returncode == 0 and "Logged out" not in out and r.stdout.strip():
            return "connected"
        return "logged_out"
    return "logged_out"


def _prompt_tailscale():
    """Ask the user (only when stdin is interactive). Y is the default —
    the prompt only appears when the container has tailscale and is logged
    out, i.e. registering is almost always the intent. Ctrl-C still skips.
    """
    try:
        if not sys.stdin.isatty():
            return False
        answer = input("Register this container on Tailscale? [Y/n] ").strip().lower()
        return answer not in ("n", "no")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _prompt_rebuild(container_id):
    """Ask whether to rebuild an existing container (only on interactive
    stdin). Default is N — reuse — so scripted/ssh-without-tty runs are
    never blocked; --fresh still forces a rebuild with no prompt. A rebuild
    recreates the container from the Dockerfile; the bind-mounted workspace
    and named volumes survive, so tailscale registration + SSH host keys
    persist.
    """
    try:
        if not sys.stdin.isatty():
            return False
        answer = input(f"[devopen] existing container {container_id[:12]} found — rebuild it? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _ensure_ssh_hostkeys_and_sshd(container_id, on_log=log_stdout):
    """Idempotent re-run of the postCreate SSH host-key restore/backup step
    (mirrors post-create-setup.sh), plus sshd — guarantees mosh/ssh into the
    container uses stable host keys (persisted in the tailscale volume).
    """
    script = r"""
set -e
sudo mkdir -p /var/lib/tailscale/ssh
if [ -n "$(ls -A /var/lib/tailscale/ssh/ssh_host_* 2>/dev/null)" ]; then
  sudo cp -f /var/lib/tailscale/ssh/ssh_host_* /etc/ssh/
  sudo chmod 600 /etc/ssh/ssh_host_*_key
  sudo chmod 644 /etc/ssh/ssh_host_*_key.pub 2>/dev/null || true
  echo "HOSTKEYS: restored from volume"
else
  sudo ssh-keygen -A || true
  sudo cp -f /etc/ssh/ssh_host_* /var/lib/tailscale/ssh/
  echo "HOSTKEYS: generated + backed up to volume"
fi
sudo service ssh restart >/dev/null 2>&1 || sudo /usr/sbin/sshd || true
echo "SSHD: $(pgrep -x sshd >/dev/null && echo running || echo NOT-running)"
"""
    try:
        r = subprocess.run(
            ["docker", "exec", "-u", "root", container_id, "sh", "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        for line in (r.stdout or "").splitlines():
            if line.strip():
                on_log("[devopen] " + line.strip())
        return r.returncode == 0
    except Exception:
        return False


def _tailnet_ip(container_id):
    try:
        r = subprocess.run(
            ["docker", "exec", "-u", "root", container_id, "tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def _remote_user(local_folder):
    """remoteUser from the repo's devcontainer config (default 'vscode')."""
    import json as _json
    for path in (os.path.join(local_folder, ".devcontainer", "devcontainer.json"),
                 os.path.join(local_folder, ".devcontainer.json")):
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return _json.load(f).get("remoteUser") or "vscode"
            except (OSError, ValueError):
                break
    return "vscode"


def blink_url(host, username, key=None, port=22):
    """Build a Blink Shell deep-link that adds/opens a host.

    Format: blink://host/?host=HOST&username=USER&key=KEY&port=PORT
    (param names verified against Blink 3.x; older versions may differ.)
    If key is omitted, Blink uses its default key.
    """
    from urllib.parse import quote
    params = {"host": host, "username": username}
    if key:
        params["key"] = key
    params["port"] = port
    return "blink://host/?" + "&".join(f"{k}={quote(str(v))}" for k, v in params.items())


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
    # If the container was stopped (e.g. its VS Code window closed), start it
    # and bring the daemon up ourselves (plain `docker start` doesn't run
    # postStart, which is what normally starts tailscaled).
    deadline = time.monotonic() + 30
    ready = False
    while time.monotonic() < deadline:
        r = exec_in(["sh", "-c", "command -v tailscale >/dev/null 2>&1 && pgrep -x tailscaled >/dev/null 2>&1"])
        if r.returncode == 0:
            ready = True
            break
        subprocess.run(["docker", "start", container_id], capture_output=True, text=True, timeout=30)
        exec_in(["sh", "-c",
                 "command -v tailscaled >/dev/null 2>&1 && "
                 "(pgrep -x tailscaled >/dev/null 2>&1 || "
                 "sudo start-stop-daemon --start --background --oknodo "
                 "--pidfile /var/run/tailscaled.pid --make-pidfile "
                 "--exec /usr/sbin/tailscaled -- --state=/var/lib/tailscale/tailscaled.state)"])
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
              tailscale=None, tailscale_authkey=None, tailscale_hostname=None,
              blink_key=None, fresh=False, clean=False):
    """Full open flow. Returns the vscode-remote URI on success.

    tailscale: True = always register; False = never; None = ask on the CLI
    (only when the container has tailscale and is logged out).

    blink_key: optional name of the SSH key in Blink Shell for this host.
    When omitted, devopen prints a `blink://host/?…` link without a key param
    so Blink uses its default key.
    """
    repo_url = normalize_repo(repo_url)
    if not repo_url:
        raise DevopenError("repo URL is required.")
    from . import config
    cfg = config.load()
    if not workspaces_dir:
        workspaces_dir = cfg["workspaces_dir"]
    # A bare repo name (e.g. `devopen pshelf`, no scheme and no "owner/" prefix)
    # resolves to the configured GitHub account, mirroring how the interactive
    # picker expands its entries to full URLs. Pass an explicit `owner/repo` or
    # full URL if the repo isn't under the default account.
    if "/" not in repo_url and not repo_url.startswith(
            ("http://", "https://", "git@", "ssh://", "git://")):
        username = cfg.get("github_username") or "nickbrett1"
        repo_url = f"https://github.com/{username}/{repo_url}.git"

    ensure_docker(on_log=on_log)
    ensure_devcontainer_cli(on_log=on_log)
    folder = clone_or_update(workspaces_dir, repo_url, branch, on_log=on_log)
    # Smart reuse: if a container already exists for this workspace, ask whether
    # to rebuild (interactive only; default N = reuse) unless --fresh was passed,
    # then start/reuse it instead of rebuilding (fast reopen, keeps tailscale
    # state + host keys).
    existing = _find_existing_container(folder)
    # Reuse only when NOT forcing a rebuild (--fresh/--clean) and the user
    # doesn't opt into a rebuild on an interactive terminal. Otherwise we
    # rebuild — stopping + removing any existing container first so the
    # freshly-built one doesn't conflict with an open one (ports/name/sessions).
    if existing and not (fresh or clean) and not _prompt_rebuild(existing):
        if _container_running(existing):
            on_log(f"[devopen] reusing running container {existing[:12]} (no rebuild).")
        else:
            on_log(f"[devopen] existing container {existing[:12]} is stopped — starting it.")
            subprocess.run(["docker", "start", existing], capture_output=True, text=True, timeout=60)
        container_id = existing
    else:
        if existing:
            on_log(f"[devopen] rebuilding container for {folder}…")
            _stop_and_remove_container(existing, on_log=on_log)
        if clean:
            on_log("[devopen] clean rebuild: building image with --no-cache (Docker layer cache skipped).")
        on_log(f"Running devcontainer up on {folder} (first build can take minutes)…")
        container_id = _devcontainer_up(folder, on_log=on_log, no_cache=clean)
        on_log(f"Container ready: {container_id}")
    hostname = tailscale_hostname or repo_dir_name(repo_url)
    # Register BEFORE opening the window: the Dev Containers extension stops
    # the container when its window closes, which would make the registration
    # unreachable (docker exec fails on a stopped container).
    registered = False
    if tailscale is True:
        registered = tailscale_up(container_id, hostname, authkey=tailscale_authkey, on_log=on_log)
    elif tailscale is None:
        state = tailscale_state(container_id)
        if state == "connected":
            on_log(f"[devopen] tailscale: already connected as '{hostname}'.")
            registered = True
        elif state == "logged_out":
            on_log("[devopen] tailscale: container has tailscale but is not registered.")
            if _prompt_tailscale():
                registered = tailscale_up(container_id, hostname, authkey=tailscale_authkey, on_log=on_log)
            else:
                on_log("[devopen] tailscale: skipped (use --tailscale to register, --no-tailscale to silence).")
        # 'absent' → silently skip
    if registered:
        # ssh/mosh prep: stable host keys (restore/backup) + sshd, then show
        # the exact mosh command with the container's tailnet IP, and a
        # blink://host deep link using the MagicDNS name we registered the
        # container under (durable across container recreations, unlike the
        # raw IP). If no Blink key is configured, the key param is omitted so
        # Blink uses its default key.
        _ensure_ssh_hostkeys_and_sshd(container_id, on_log=on_log)
        ip = _tailnet_ip(container_id)
        if ip:
            user = _remote_user(folder)
            on_log(f"[devopen] mosh-ready: mosh {user}@{ip}   (ssh {user}@{ip})")
        user = _remote_user(folder)
        on_log(f"[devopen] blink-host: {blink_url(hostname, user, key=blink_key or None)}")
    uri = open_in_vscode(container_id, folder, on_log=on_log)
    on_log("Done.")
    return uri
