"""Container matching for an existing workspace.

The bug these cover: a workspace named `galactic-unicorn` shares a prefix with
its sibling `galactic-unicorn-remote`, and the matcher used substring tests, so
opening one silently reused the other's container.
"""

import json
import os
import subprocess

import pytest

from devopen import opener


def _ok(stdout):
    return subprocess.CompletedProcess(args=["docker"], returncode=0,
                                       stdout=stdout, stderr="")


def _fake_run(labels, mounts, vsch, ps_all):
    """A docker stub.

    labels: {cid: devcontainer.local_folder value}
    mounts: {cid: [source, ...]}
    vsch:   {cid: {label: value}}
    ps_all: the ids `docker ps -aq` lists, newest first.
    """
    def run(args, **kwargs):
        if args[:2] == ["docker", "ps"] and args[2:] == ["-aq"]:
            return _ok("\n".join(ps_all))
        prefix = "label=devcontainer.local_folder="
        if args[:2] == ["docker", "ps"] and args[-1].startswith(prefix):
            path = args[-1][len(prefix):]
            # docker ps is newest-first regardless of the filter, so honour the
            # caller's ps_all ordering here too.
            return _ok("\n".join(
                cid for cid in ps_all if labels.get(cid) == path))
        if args[:2] == ["docker", "inspect"] and args[2] == "-f":
            fmt, cid = args[3], args[4]
            if "Config.Labels" in fmt:
                return _ok(json.dumps(vsch.get(cid, {})))
            if "Mounts" in fmt:
                return _ok("".join(f"{s}\n" for s in mounts.get(cid, [])))
        return _ok("")

    return run


def test_sibling_with_a_shared_prefix_is_not_matched(monkeypatch):
    """The regression: opening galactic-unicorn must not grab the
    galactic-unicorn-remote container, by mount or by extension label."""
    local = "/Users/nick/DevOpen/workspaces/galactic-unicorn"
    sibling = "/Users/nick/DevOpen/workspaces/galactic-unicorn-remote"
    monkeypatch.setattr(opener.subprocess, "run", _fake_run(
        labels={"mine": local, "other": sibling},
        mounts={"mine": [local], "other": [sibling]},
        vsch={
            "other": {"vsch.local.repository":
                      "https://github.com/nickbrett1/galactic-unicorn-remote.git/tree/main"},
        },
        ps_all=["other", "mine"],
    ))

    found = opener._find_existing_containers(
        local, "https://github.com/nickbrett1/galactic-unicorn.git")

    assert found == ["mine"]


def test_mount_source_matches_whole_path_only(monkeypatch):
    """A container whose mounts merely *contain* the path as a prefix must not
    match; the resolved path itself must."""
    local = "/w/acme"
    monkeypatch.setattr(opener.subprocess, "run", _fake_run(
        labels={},
        mounts={"prefix": ["/w/acme-extra", "/w/acme-extra/data"],
                "exact": ["/w/acme"]},
        vsch={},
        ps_all=["prefix", "exact"],
    ))

    assert opener._find_existing_containers(local) == ["exact"]


def test_label_match_is_newest_first(monkeypatch):
    local = "/w/acme"
    monkeypatch.setattr(opener.subprocess, "run", _fake_run(
        labels={"old": local, "new": local}, mounts={}, vsch={},
        ps_all=["new", "old"],
    ))

    assert opener._find_existing_containers(local) == ["new", "old"]


def test_symlinked_workspace_still_matches_its_resolved_mount(tmp_path, monkeypatch):
    """The substring test existed partly to survive a symlinked workspace path;
    realpath keeps that, without the prefix collision."""
    real = tmp_path / "real" / "acme"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(real, link)
    monkeypatch.setattr(opener.subprocess, "run", _fake_run(
        labels={}, mounts={"c": [str(real)]}, vsch={}, ps_all=["c"],
    ))

    assert opener._find_existing_containers(str(link)) == ["c"]


def test_extension_label_requires_an_exact_repo(monkeypatch):
    """vsch label .../galactic-unicorn-remote.git must not match the
    .../galactic-unicorn repo, and vice versa."""
    local = "/w/galactic-unicorn"
    monkeypatch.setattr(opener.subprocess, "run", _fake_run(
        labels={},
        mounts={},
        vsch={
            "mine": {"vsch.local.repository":
                     "https://github.com/nickbrett1/galactic-unicorn.git/tree/main"},
            "other": {"vsch.local.repository":
                      "https://github.com/nickbrett1/galactic-unicorn-remote.git/tree/main"},
        },
        ps_all=["mine", "other"],
    ))

    found = opener._find_existing_containers(
        local, "https://github.com/nickbrett1/galactic-unicorn.git")

    assert found == ["mine"]
