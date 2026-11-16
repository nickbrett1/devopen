"""Workspace refresh must not be blocked by a remote it does not need.

The bug these cover: opening a workspace that has a second remote whose SSH
host key this non-interactive process cannot accept (`ghssh`) aborted the whole
open. `git fetch --all` exits non-zero on the first unreachable remote and the
call went through _run, which raises — even though `origin` had fetched and
only `origin` is used by the pull that follows.
"""

import subprocess

from devopen import opener


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _origin_with_workspace(tmp_path):
    """A bare origin + the workspace clone clone_or_update would have made."""
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "--initial-branch=main", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "--initial-branch=main", cwd=seed)
    _git("config", "user.email", "test@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    (seed / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-u", "origin", "main", cwd=seed)
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    dest = opener.clone_or_update(str(workspaces), str(origin), None,
                                  on_log=lambda _: None)
    return origin, dest


def test_unreachable_secondary_remote_does_not_block_the_update(tmp_path):
    """A dead extra remote (the ghssh case) only warns; the open continues."""
    origin, dest = _origin_with_workspace(tmp_path)
    _git("remote", "add", "ghssh", str(tmp_path / "gone.git"), cwd=dest)

    logs = []
    opener.clone_or_update(str(tmp_path / "workspaces"), str(origin), None,
                           on_log=logs.append)

    assert any("could not fetch remote 'ghssh'" in line for line in logs), logs
    assert any("fetch incomplete for: ghssh" in line for line in logs), logs


def test_unreachable_origin_warns_and_continues(tmp_path):
    """Even a failed origin fetch must not raise: the pull below already
    degrades to 'continue with local state' when the network is gone."""
    origin, dest = _origin_with_workspace(tmp_path)
    _git("remote", "set-url", "origin", str(tmp_path / "gone.git"), cwd=dest)

    logs = []
    opener.clone_or_update(str(tmp_path / "workspaces"), str(origin), None,
                           on_log=logs.append)

    assert any("could not fetch remote 'origin'" in line for line in logs), logs
