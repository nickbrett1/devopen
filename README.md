# devopen

Remote devcontainer opener service. One HTTP call (or CLI command) that:

1. clones/fetches a GitHub repo
2. builds & starts its devcontainer (`devcontainer up --remove-existing-container`)
3. opens a **new VS Code window** attached to that container on the Mac

Trigger it from your iPhone over Tailscale while you're away from the machine —
by the time you mosh in, the container is up and VS Code is open on the Mac
display.

## How it works

```
[iPhone] ──Tailscale──▶ [Mac Studio:8797 (FastAPI)]
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
- **HTTP server** (`devopen/server.py`): FastAPI on `0.0.0.0:8797`.
- **LaunchAgent** (`com.nick.devopen.plist`): per-user agent (needs the GUI
  session so the window can open on the display), `KeepAlive` + `RunAtLoad`.
- **Config**: `~/.devopen/config.json` (mode 600) — token, host, port,
  workspaces dir.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/nickbrett1/devopen/main/install.py | python3
```

The installer:

1. clones the repo to `~/DevOpen/devopen`
2. creates a venv and installs `fastapi` + `uvicorn`
3. installs `/usr/local/bin/devopen` (CLI) and `/usr/local/bin/devopen-server` (launchd wrapper)
4. writes `~/.devopen/config.json` with a fresh token (`openssl rand`-style hex)
5. installs + loads `~/Library/LaunchAgents/com.nick.devopen.plist`
6. ensures the `devcontainer` CLI is present, then verifies `/health`

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
devopen                            # interactive picker of existing workspaces
```

### From Blink on your iPhone

The repo is just a command-line argument — type the URL, or the shorter
`owner/repo` form:

```bash
ssh mac-studio devopen nickbrett1/agy-telemetry
```

Run `devopen` with **no repo** for an interactive picker of already-cloned
workspaces (needs a TTY, so use `-t`):

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

### HTTP API

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

### iPhone one-tap

- **iOS Shortcut**: *Get Contents of URL* → `POST http://mac-studio:8797/open`,
  headers `Authorization: Bearer <token>`, body JSON `{"repo": "…"}`. Add to
  Home Screen.
- **Safari bookmark**: `http://mac-studio:8797/open?token=<token>&repo=<url>`
- **Blink**: `curl` or `ssh mac-studio devopen <repo>`

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
│   └── __main__.py             # CLI (`python3 -m devopen`)
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
