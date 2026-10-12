"""Configuration handling for devopen.

Config lives at ~/.devopen/config.json (mode 600):

    {
      "install_dir":       "~/DevOpen/devopen",
      "workspaces_dir":    "~/DevOpen/workspaces",
      "github_username":   "nickbrett1",
      "blink_key":         "<name>",        # SSH key in Blink Shell (optional)
      "tailscale_authkey": "<hex>",         # optional, non-interactive registration
    }
"""

import json
import os
import tempfile

CONFIG_DIR_NAME = ".devopen"
CONFIG_FILE_NAME = "config.json"


def config_dir(home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, CONFIG_DIR_NAME)


def config_path(home=None):
    return os.path.join(config_dir(home), CONFIG_FILE_NAME)


def _defaults(home=None):
    home = home or os.path.expanduser("~")
    devopen_home = os.environ.get("DEVOPEN_HOME") or os.path.join(home, "DevOpen")
    return {
        "install_dir": os.path.join(devopen_home, "devopen"),
        "workspaces_dir": os.path.join(devopen_home, "workspaces"),
        "github_username": "nickbrett1",
        "blink_key": "",
        "tailscale_authkey": "",
    }


def save(cfg, home=None):
    """Atomically write config with 0600 permissions.

    The temp file is uniquely named on purpose: devdash reads this config from
    several threads at once (request handlers, job threads, a warm-up thread),
    and a fixed `path + ".tmp"` let one thread's `os.replace` pull the file out
    from under another's `open`/`os.chmod` — a spurious FileNotFoundError on
    config.json.tmp that surfaced during a provision."""
    path = config_path(home)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(home=None):
    """Load config, creating a default one if missing."""
    cfg = _defaults(home)
    path = config_path(home)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
            else:
                data = {}
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Could not read {path}: {e}") from e
    # Write only when it would change something — first run, or new defaults to
    # fold in. A plain read stays a read, so threads sharing this module cannot
    # collide in save() over a config they only came to read.
    if cfg != data:
        save(cfg, home=home)
    return cfg
