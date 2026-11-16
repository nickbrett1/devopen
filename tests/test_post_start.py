"""Re-running postStartCommand after a plain `docker start`.

The bug these cover: devopen reuses a stopped container with `docker start`,
which runs no devcontainer lifecycle commands. So sshd, socat, tailscaled and
the container agent — all started by postStartCommand — stayed down, and the
reopened workspace attached to a container nothing could reach.
"""

import json
import subprocess

from devopen import opener


def _workspace(tmp_path, config):
    ws = tmp_path / "acme"
    (ws / ".devcontainer").mkdir(parents=True)
    (ws / ".devcontainer" / "devcontainer.json").write_text(json.dumps(config))
    return ws


def _stub_run(monkeypatch, *, rc=0, out="", err="", calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        # run_post_start asks the container for the user's HOME first.
        stdout = "/home/node\n" if any("getent" in str(a) for a in argv) else out
        return subprocess.CompletedProcess(argv, rc, stdout, err)

    monkeypatch.setattr(opener.subprocess, "run", run)


def test_runs_the_hook_as_the_container_user_in_the_workspace(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {
        "remoteUser": "node",
        "workspaceFolder": "/workspaces/acme",
        "postStartCommand": "bash /workspaces/acme/.devcontainer/post-start.sh",
    })
    calls, logs = [], []
    _stub_run(monkeypatch, out="sshd: started", calls=calls)

    assert opener.run_post_start("c1", str(ws), on_log=logs.append) is True

    hook = calls[-1]
    assert hook[:4] == ["docker", "exec", "-u", "node"]
    assert hook[4:6] == ["-e", "HOME=/home/node"]
    assert hook[6:8] == ["-w", "/workspaces/acme"]
    assert hook[8] == "c1"
    assert hook[9:] == ["sh", "-lc", "bash /workspaces/acme/.devcontainer/post-start.sh"]
    assert "sshd: started" in logs


def test_does_nothing_when_the_repo_has_no_post_start_command(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {"remoteUser": "node"})
    calls = []
    _stub_run(monkeypatch, calls=calls)

    assert opener.run_post_start("c1", str(ws), on_log=lambda _: None) is False
    assert calls == []


def test_a_failing_hook_warns_but_does_not_fail_the_open(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {"remoteUser": "node", "postStartCommand": "false"})
    logs = []
    _stub_run(monkeypatch, rc=1, err="boom")

    assert opener.run_post_start("c1", str(ws), on_log=logs.append) is False
    assert "boom" in logs
    assert any("exited 1" in line for line in logs)


def test_accepts_the_array_form_of_post_start_command(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {
        "remoteUser": "node",
        "postStartCommand": ["bash", "/workspaces/acme/.devcontainer/post-start.sh"],
    })
    calls = []
    _stub_run(monkeypatch, calls=calls)

    assert opener.run_post_start("c1", str(ws), on_log=lambda _: None) is True
    assert calls[-1][-2:] == ["bash", "/workspaces/acme/.devcontainer/post-start.sh"]
