# devopen

Open any of your devcontainer projects in VS Code from a terminal — locally
or over SSH from your phone. `devopen`:

1. clones/fetches the repo
2. builds & starts its devcontainer (`devcontainer up --remove-existing-container`)
3. opens a **new VS Code window** attached to that container on the Mac

Run it bare and it shows an interactive picker of your workspaces plus **all
your GitHub repos** (private included); run it with an argument and it goes
straight to work. It's a pure CLI — nothing listens on a port.

```
[terminal / iPhone (Blink)] ──Tailscale──▶ [Mac Studio]
    devopen [nickbrett1/repo]
        │ 1. clone/fetch repo → ~/DevOpen/workspaces
        │ 2. devcontainer up --remove-existing-container
        │ 3. code --new-window --folder-uri vscode-remote://dev-container+<id>
        ▼
[VS Code window on Mac display] + [container on tailnet for mosh]
```

- **Core logic** (`devopen/opener.py`): ensures Docker is running (auto-launches
  Docker Desktop), ensures the `devcontainer` CLI (installs `@devcontainers/cli`
  via npm on first use), clones or updates the repo, runs `devcontainer up`,
  and opens the VS Code window.
- **Repo picker** (`devopen/repos.py` + CLI): lists already-cloned workspaces,
  then all GitHub repos via the keychain PAT (`git credential fill`), so your
  private repos show up with zero extra setup.
- **Config**: `~/.devopen/config.json` (mode 600) — workspaces dir.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3
```

The installer:

1. clones the repo to `~/DevOpen/devopen`
2. installs `/usr/local/bin/devopen` (CLI — pure stdlib, no venv needed)
3. writes `~/.devopen/config.json` (mode 600)
4. ensures the `devcontainer` CLI is present

## Uninstallation

```bash
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/uninstall.py | python3
```

Removes the script and `~/.devopen`. Your `~/DevOpen` workspaces are left
untouched (they're your data) — remove with `rm -rf ~/DevOpen` if you want
them gone too.

## Usage

### CLI (from the Mac or via SSH)

```bash
devopen https://github.com/you/your-repo.git
devopen you/your-repo              # owner/repo shorthand works too
devopen you/your-repo -b feature/x
devopen                            # interactive picker: workspaces + all your GitHub repos
```

Run `devopen` with no arguments for the interactive picker — it lists your
already-cloned workspaces first, then **all of your GitHub repos** (private
included — it reads the PAT from your macOS keychain via `git credential
fill`, falling back to the public API). Pick a number, or press `0` to paste
any URL:

```
Pick a repo to open:

  Workspaces (already cloned):
    [ 1] agy-telemetry

  Your GitHub repos:
    [ 2] nickbrett1/agent-swarm
    [ 3] nickbrett1/dagster-tutorial
    [ 4] nickbrett1/data-science-on-gcp
    ...

    [ 0] Enter a repo URL
    [ q] Quit
>
```

Workspaces and GitHub repos are each sorted alphabetically (case-insensitive)
so the list is easy to scan.

### Tailscale registration

Some devcontainers join your tailnet (e.g. genproj projects with a
`scripts/cloud_login.sh`). When you open a repo whose container has tailscale
but isn't registered yet, devopen **asks** you:

```
[devopen] tailscale: container has tailscale but is not registered.
Register this container on Tailscale? [Y/n]
```

The prompt only appears when the container has tailscale but isn't registered —
i.e. registering is almost always the intent — so **Enter registers** (answer
`n` to skip, Ctrl-C to cancel). devopen then runs `tailscale up` inside the
container: without an authkey it streams the `login.tailscale.com/a/...` URL
(open it on any device and the container joins); with one it's non-interactive.
Already-registered or tailscale-less containers are skipped silently.

Control the prompt:

```bash
devopen --tailscale nickbrett1/parquet-peek            # always register, never ask
devopen --no-tailscale parquet-peek                    # never register, never ask
devopen --tailscale --authkey tskey-... parquet-peek   # non-interactive registration
```

- **hostname** defaults to the repo name (`parquet-peek`); override with `--hostname`.
- **authkey** comes from `--authkey`, the `DEVOPEN_TAILSCALE_AUTHKEY` env var,
  or `"tailscale_authkey"` in `~/.devopen/config.json` (the file is mode 600).
- **State persists:** the auth lives in the container's `tailscale` volume, so
  once registered, later devopen opens (which recreate the container via
  `--remove-existing-container`) come back on the tailnet automatically —
  no prompt, no flag.
- **ssh/mosh prep:** after a successful registration devopen also re-runs the
  SSH host-key restore/backup (stable keys via the tailscale volume) and makes
  sure `sshd` is up, then prints the connection command:
  `[devopen] mosh-ready: mosh node@100.x.x.x (ssh node@100.x.x.x)`.
- **Blink host one-tap:** devopen also prints a `blink://host/?…` deep link
  using the container's MagicDNS name (the `--hostname` you registered it
  under), so you can add it to Blink Shell on your iPhone without typing:
  ```
  [devopen] blink-host: blink://host/?host=parquet-peek&username=root&port=22
  ```
  Copy the link and open it somewhere iOS routes into Blink (Safari / Notes) —
  Blink adds the host. Omit `--blink-key` to use Blink's default key; set
  `"blink_key"` in `~/.devopen/config.json` to pin a named key instead.
- Note: closing the VS Code window stops the container (Dev Containers
  shutdown behavior) — keep the window open (or restart the container) when
  you want to mosh in from away.

### Smart container reuse

devopen checks for an existing container for the workspace before building:

- **none** → full build (`devcontainer up --remove-existing-container`)
- **existing** → asks `rebuild it? [y/N]` on an interactive terminal, then
  either rebuilds (y) or reuses (default) — running containers are reused
  as-is (instant), stopped ones (e.g. its VS Code window was closed — that
  stops the container) are started again, with `tailscaled` + `sshd` + host
  keys brought back up

The prompt only appears on an interactive terminal — over plain `ssh` (no
`-t`), or from scripts it silently reuses, so the phone/automation flow is
never blocked. Use `-t` when you want the prompt:

```bash
ssh -t mac-studio devopen nickbrett1/parquet-peek   # asks: rebuild? [y/N]
```

### Diverged local branch

If the workspace has local commits that origin doesn't (e.g. the same fix
committed from two machines), `git pull --ff-only` can't fast-forward and
devopen used to hard-fail. Now it:

- **interactive terminal** → asks `local branch diverged (N local commits
  not on origin) — rebase onto origin? [y/N]`; `y` replays your local
  commits on top of origin via `git pull --rebase --autostash` (uncommitted
  changes survive too). If the rebase conflicts, it aborts safely and
  continues with your local state.
- **non-interactive** (plain `ssh`, scripts) → never rewrites history
  silently: it warns and opens the container with your local state. Run
  `git pull --rebase` in the workspace when you want to sync.

Reuse is fast and keeps the tailscale registration and SSH host keys (they live
in the workspace's volumes). A rebuild recreates the container from the
Dockerfile but keeps the bind-mounted workspace and named volumes, so
tailscale/SSH state survives. Force a rebuild with no prompt via `--fresh`:

```bash
devopen --fresh nickbrett1/parquet-peek
```

### Clean rebuilds (no cache)

Rebuilds (`--fresh` or answering `y` to the prompt) remove the container but
still build the image **with Docker's layer cache** — stale cached layers are
reused when their inputs are unchanged. If you suspect something stale in the
image itself (old `npm ci`/`apt` results baked into a layer, etc.), use
`--clean`, which passes `--build-no-cache` to `devcontainer up`:

```bash
devopen --clean nickbrett1/parquet-peek
```

`--clean` implies a rebuild (no prompt). It keeps the bind-mounted workspace
and named volumes, so tailscale/SSH state survives — but **host-side files in
the workspace (e.g. `node_modules` on the host bind mount) are NOT touched**.
If stale dependencies are the suspect, also remove them from the host:

```bash
rm -rf ~/DevOpen/workspaces/parquet-peek/node_modules
devopen --clean nickbrett1/parquet-peek
```

### From Blink on your iPhone

The repo is just a command-line argument — type the URL, or the shorter
`owner/repo` form:

```bash
ssh mac-studio devopen nickbrett1/agy-telemetry
```

Run `devopen` with **no repo** for the interactive picker — workspaces plus
all your GitHub repos (needs a TTY, so use `-t`):

```bash
ssh -t mac-studio devopen
```

## Configuration

`~/.devopen/config.json`:

```json
{
  "install_dir": "/Users/<you>/DevOpen/devopen",
  "workspaces_dir": "/Users/<you>/DevOpen/workspaces",
  "github_username": "nickbrett1",
  "blink_key": "",
  "tailscale_authkey": ""
}
```

- `DEVOPEN_HOME` env var overrides the `~/DevOpen` default at install time (so
  the installer works for other users/machines too).
- `DEVOPEN_WORKSPACES` overrides the workspaces dir at install time.

## Gotchas

- **`docker: command not found` inside VS Code after opening?** The container
  is fine (devopen built it with docker). The usual cause is VS Code.app
  launched from the GUI getting a minimal PATH that omits `/usr/local/bin` —
  fix with `"dev.containers.dockerPath": "/usr/local/bin/docker"` in VS Code
  user settings, then fully quit (⌘Q) and relaunch.
- **OrbStack + VS Code 1.132 quirk:** the documented attach URI
  `vscode-remote://dev-container+<container-id>/<path>` resolves to a garbage
  workspace folder, making the extension spawn docker with a corrupted cwd
  (surfaces as the same "docker: command not found"). devopen therefore opens
  the container using the extension's own host-path URI
  (`dev-container+<hex-json of {hostPath, settings, configFile}>/<in-container
  workspaceFolder>`) — the same format VS Code stores for bind-mounted
  workspaces, which resolves cleanly.
- Docker Desktop must be installed; devopen launches it and waits if it's not
  running.
- `--remove-existing-container` = fresh container each time (matches the
  "new clone" habit); images are reused when unchanged.
- First build per repo takes minutes.
- Tailscale: the node name here is `mac-studio`; enable *Allow incoming
  connections* in the Tailscale macOS app.

## Project layout

```
devopen/
├── install.py / uninstall.py   # curl | python3 installers
├── devopen/
│   ├── opener.py               # core: docker → devcontainer CLI → clone → up → open
│   ├── config.py               # ~/.devopen/config.json handling
│   ├── repos.py                # GitHub repo listing (keychain PAT → API, via curl)
│   └── __main__.py             # CLI (`python3 -m devopen`) + interactive picker
└── scripts/
    └── devopen                 # → /usr/local/bin/devopen
```

## Ideas

- Have **genproj call devopen** after scaffolding a project — full end-to-end
  bootstrap with zero taps.
- `code-server` / `vscode tunnel` variant for a browser-based editor.
