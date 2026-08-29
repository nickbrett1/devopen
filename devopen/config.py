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
    """Atomically write config with 0600 permissions."""
    path = config_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load(home=None):
    """Load config, creating a default one if missing."""
    cfg = _defaults(home)
    path = config_path(home)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, ValueError) as e:
            raise RuntimeError(f"Could not read {path}: {e}") from e
    save(cfg, home=home)
    return cfg
