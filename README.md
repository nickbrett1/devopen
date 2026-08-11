# devopen

Open any of your devcontainer projects in VS Code from a terminal — locally
or over SSH from your phone. `devopen`:

1. clones/fetches the repo
2. builds & starts its devcontainer (`devcontainer up --remove-existing-container`)
3. opens a **new VS Code window** attached to that container on the Mac

Run it bare and it shows an interactive picker of your workspaces plus **all
your GitHub repos** (private included); run it with an argument and it goes
straight to work. An optional HTTP server (`install.py --with-server`) adds a
one-tap URL for iPhone Shortcuts / Safari — but the CLI alone needs nothing
listening on any port.

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
- **HTTP server** (`devopen/server.py`, *optional*): FastAPI on `0.0.0.0:8797`,
  installed with `--with-server` (LaunchAgent `com.nick.devopen.plist`).
- **Config**: `~/.devopen/config.json` (mode 600) — workspaces dir, and the
  token/host/port for the optional server.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3
# add the optional HTTP server (iPhone one-tap / Shortcut / genproj hook):
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3 - --with-server
```

The installer:

1. clones the repo to `~/DevOpen/devopen`
2. creates a venv and installs `fastapi` + `uvicorn`
3. installs `/usr/local/bin/devopen` (CLI)
4. writes `~/.devopen/config.json` (fresh token included, mode 600)
5. with `--with-server`: also installs `/usr/local/bin/devopen-server` +
   `~/Library/LaunchAgents/com.nick.devopen.plist` (per-user agent —
   `KeepAlive` + `RunAtLoad`)
6. ensures the `devcontainer` CLI is present; with the server, verifies
   `/health`

## Uninstallation

```bash
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/uninstall.py | python3
```

Unloads the agent, removes the scripts and `~/.devopen`. Your `~/DevOpen`
workspaces are left untouched (they're your data) — remove with
`rm -rf ~/DevOpen` if you want them gone too.

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
but isn't registered yet, devopen **asks** you after the window opens:

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

Or fire-and-forget the HTTP API (log streams to the terminal):

```bash
curl -s -X POST http://mac-studio:8797/open \
     -H "Authorization: Bearer <token>" \
     -d '{"repo": "nickbrett1/agy-telemetry"}'
```

The `owner/repo` shorthand works everywhere — CLI, picker, and the HTTP API.

### HTTP API (requires the optional `--with-server` install)

```bash
curl -X POST http://mac-studio:8797/open \
     -H "Authorization: Bearer <token>" \
     -d '{"repo": "https://github.com/you/your-repo.git", "branch": "optional"}'
```

Streams the full build log; ends with `[devopen] SUCCESS <uri>` or
`[devopen] FAILED: <reason>`.

- `POST /open` — JSON body `{"repo", "branch?"}` + `Authorization: Bearer <token>`
- `GET /open?token=<token>&repo=<url>&branch=<optional>` — one-tap from Safari / a Home Screen shortcut; logs render in the browser
- `GET /health` — liveness

Only one open runs at a time — a concurrent request gets `409`. Wrong/missing
token gets `401`.

### iPhone (Blink — works with CLI-only install)

```bash
ssh mac-studio devopen              # pick from workspaces + all your GitHub repos
ssh mac-studio devopen nickbrett1/your-repo
```

### iPhone one-tap (requires the optional `--with-server` install)

- **iOS Shortcut — share sheet (recommended, no repo typing)**: the shortcut
  starts with *Receive [URLs] from Share Sheet*, then *Get Contents of URL* →
  `POST http://mac-studio:8797/open`, headers `Authorization: Bearer <token>`,
  body JSON `{"repo": "{{Shortcut Input}}"}`. Open a repo on GitHub in Safari →
  Share → the shortcut. Or use *Ask for Input* + the same request for a
  Home Screen icon that prompts for a URL.
- **Safari bookmark**: `http://mac-studio:8797/open?token=<token>&repo=<url>`
- **Blink**: `curl -s -X POST http://mac-studio:8797/open -H "Authorization: Bearer <token>" -d '{"repo":"nickbrett1/your-repo"}'`

## Configuration

`~/.devopen/config.json`:

```json
{
  "token": "<hex>",
  "install_dir": "/Users/<you>/DevOpen/devopen",
  "workspaces_dir": "/Users/<you>/DevOpen/workspaces",
  "host": "0.0.0.0",
  "port": 8797
}
```

- `DEVOPEN_HOME` env var overrides the `~/DevOpen` default at install time (so
  the installer works for other users/machines too).
- Bind to the tailnet IP only (instead of `0.0.0.0`) by editing `host` — e.g.
  `100.x.y.z` from `tailscale ip -4`.

## Gotchas

- **Port is 8797, not 8787** — VS Code's own Code Helper process listens on
  8787 (and 8788), so devopen uses 8797 to avoid the clash.
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
- **LaunchAgent, not LaunchDaemon** — the service must run in your GUI session
  so `code` can open a window on the display.
- Docker Desktop must be installed; devopen launches it and waits if it's not
  running.
- `--remove-existing-container` = fresh container each time (matches the
  "new clone" habit); images are reused when unchanged.
- First build per repo takes minutes — the HTTP stream stays open for the whole
  build (set a generous timeout on your client).
- Tailscale: the node name here is `mac-studio`; enable *Allow incoming
  connections* in the Tailscale macOS app.

## Project layout

```
devopen/
├── install.py / uninstall.py   # agy-telemetry-style installers (curl | python3)
├── devopen/
│   ├── opener.py               # core: docker → devcontainer CLI → clone → up → open
│   ├── server.py               # FastAPI app
│   ├── config.py               # ~/.devopen/config.json handling
│   ├── repos.py                # GitHub repo listing (keychain PAT → API, via curl)
│   └── __main__.py             # CLI (`python3 -m devopen`) + interactive picker
├── scripts/
│   ├── devopen                 # → /usr/local/bin/devopen
│   └── devopen-server          # → /usr/local/bin/devopen-server (launchd wrapper)
└── com.nick.devopen.plist      # LaunchAgent template (__HOME__ substituted)
```

## Ideas

- Have **genproj call devopen** after scaffolding a project — full end-to-end
  bootstrap with zero taps.
- `code-server` / `vscode tunnel` variant for a browser-based editor.
- SSE log streaming + a small status page for in-flight builds.
