import fcntl
import hashlib
import importlib.util
import json
import os
import pwd
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "matrix_updater", Path(__file__).resolve().parents[1] / "scripts/update_matrix.py"
)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=Updater test", "-c", "user.email=update@example.invalid",
         *args],
        cwd=repo, stderr=subprocess.PIPE, text=True,
    ).strip()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "main.py").write_text("VALUE = 1\n")
    (repo / "README.md").write_text("original\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / ".gitignore").write_text(".env\n.venv/\nlogs/\nreports/\ndata/\n*.lock\n")
    # Force-add uv.lock: the fixture's generic lock ignore is deliberately broad.
    git(repo, "add", ".")
    git(repo, "add", "-f", "uv.lock")
    git(repo, "commit", "-qm", "base")
    git(repo, "remote", "add", "origin", updater.ORIGIN)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    remote = tmp_path / "remote"
    git(tmp_path, "clone", "--quiet", str(repo), str(remote))
    (repo / ".venv/bin").mkdir(parents=True)
    (repo / ".venv/bin/python").symlink_to(sys.executable)
    (repo / ".env").write_text("DUMMY_TEST_VALUE=preserve-me\n")
    for name in ("data/example.db", "logs/log", "reports/summary.md", "notes.txt"):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"runtime content must survive\0\n")
    instance = updater.Updater(repo, tmp_path / "backups", pwd.getpwuid(os.getuid()).pw_name)
    original_git = instance.git

    def local_transport(*args, **kwargs):
        if args[0] == "fetch":
            # Exercise real fetch/merge against a local Git repository, without networking.
            return original_git(
                "fetch", "--no-tags", str(remote),
                "+refs/heads/main:refs/remotes/origin/main", timeout=90,
            )
        return original_git(*args, **kwargs)

    monkeypatch.setattr(instance, "git", local_transport)
    monkeypatch.setattr(instance, "idle_services", lambda: None)
    return instance, remote


def publish(remote, path="main.py", content="VALUE = 2\n"):
    target = remote / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git(remote, "add", "-f", path)
    git(remote, "commit", "-qm", "remote change")
    return git(remote, "rev-parse", "HEAD")


def runtime_snapshot(repo):
    names = [".env", "data/example.db", "logs/log", "reports/summary.md", "notes.txt"]
    return {name: ((repo / name).read_bytes(), (repo / name).stat().st_mode) for name in names}


def test_unchanged_does_not_backup_or_modify(installed, capsys):
    instance, _ = installed
    before = runtime_snapshot(instance.repo)
    head = git(instance.repo, "rev-parse", "HEAD")
    instance.run()
    assert "already current" in capsys.readouterr().out
    assert git(instance.repo, "rev-parse", "HEAD") == head
    assert runtime_snapshot(instance.repo) == before
    assert not list(instance.backups.glob("update-*"))


def test_source_update_staged_backed_up_and_runtime_preserved(installed, capsys):
    instance, remote = installed
    old = git(instance.repo, "rev-parse", "HEAD")
    target = publish(remote)
    before = runtime_snapshot(instance.repo)
    instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == target
    assert (instance.repo / "main.py").read_text() == "VALUE = 2\n"
    assert runtime_snapshot(instance.repo) == before
    backup, = instance.backups.glob("update-*")
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / "complete").exists()
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["old"] == old
    assert manifest["target"] == target
    assert manifest["env_sha256"] == hashlib.sha256(before[".env"][0]).hexdigest()
    with tarfile.open(backup / "source.tar") as source:
        assert ".env" not in source.getnames()
        assert "data/example.db" not in source.getnames()
        assert source.extractfile("main.py").read() == b"VALUE = 1\n"
        assert "pyproject.toml" in source.getnames()
    assert not list(instance.backups.glob(".stage-*"))
    output = capsys.readouterr().out
    assert "updated successfully" in output
    assert "preserve-me" not in output
    assert manifest["env_sha256"] not in output


@pytest.mark.parametrize("state", ["dirty", "staged", "branch", "detached", "ahead", "diverged"])
def test_unsafe_git_states_are_rejected(installed, state):
    instance, remote = installed
    if state in {"dirty", "staged", "ahead", "diverged"}:
        (instance.repo / "main.py").write_text("LOCAL = True\n")
        if state != "dirty":
            git(instance.repo, "add", "main.py")
        if state in {"ahead", "diverged"}:
            git(instance.repo, "commit", "-qm", "local only")
    elif state == "branch":
        git(instance.repo, "switch", "-c", "not-main")
    else:
        git(instance.repo, "switch", "--detach")
    if state != "ahead":
        publish(remote, "README.md", "remote docs\n")
    before = git(instance.repo, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == before
    assert not list(instance.backups.glob("update-*"))


@pytest.mark.parametrize("path", [
    "pyproject.toml", "uv.lock", ".python-version", "requirements.txt",
    "deploy/systemd/matrix-funds.timer", "scripts/run_funds.sh",
    "scripts/update_matrix.py", ".env.example", ".gitignore", "config/us_funds.json",
])
def test_dependencies_units_and_config_refused_before_live_changes(installed, path):
    instance, remote = installed
    publish(remote)
    publish(remote, path, "changed dependency or deployment configuration\n")
    before = git(instance.repo, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError, match="manual deployment required") as error:
        instance.run()
    assert path in str(error.value)
    assert git(instance.repo, "rev-parse", "HEAD") == before
    assert (instance.repo / "main.py").read_text() == "VALUE = 1\n"
    assert not list(instance.backups.glob("update-*"))


@pytest.mark.parametrize("ignored", [False, True])
def test_untracked_and_ignored_collisions_preserved(installed, ignored):
    instance, remote = installed
    (instance.repo / "matrix_etf").mkdir()
    collision = instance.repo / "matrix_etf/new.py"
    collision.write_text("LOCAL_DATA = True\n")
    if ignored:
        (instance.repo / ".git/info/exclude").write_text("matrix_etf/new.py\n")
    publish(remote, "matrix_etf/new.py")
    old = git(instance.repo, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError, match="untracked/ignored"):
        instance.run()
    assert collision.read_text() == "LOCAL_DATA = True\n"
    assert git(instance.repo, "rev-parse", "HEAD") == old


def test_invalid_python_preserves_live_checkout(installed):
    instance, remote = installed
    publish(remote, content="def invalid(:\n")
    old = git(instance.repo, "rev-parse", "HEAD")
    before = runtime_snapshot(instance.repo)
    with pytest.raises(updater.UpdateError, match="syntax validation"):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert runtime_snapshot(instance.repo) == before
    assert not list(instance.backups.glob("update-*"))
    assert not list(instance.backups.glob(".stage-*"))


def test_source_additions_and_deletions_are_applied(installed):
    instance, remote = installed
    publish(remote, "matrix_etf/retired.py", "VALUE = 0\n")
    instance.run()
    publish(remote, "matrix_etf/new.py", "VALUE = 3\n")
    git(remote, "rm", "matrix_etf/retired.py")
    git(remote, "commit", "-qm", "remove old source")
    instance.run()
    assert not (instance.repo / "matrix_etf/retired.py").exists()
    assert (instance.repo / "matrix_etf/new.py").read_text() == "VALUE = 3\n"


def test_business_entrypoint_deletion_requires_manual_deployment(installed):
    instance, remote = installed
    git(remote, "rm", "main.py")
    git(remote, "commit", "-qm", "remove entrypoint")
    with pytest.raises(updater.UpdateError, match="business entrypoints removed: main.py"):
        instance.run()
    assert (instance.repo / "main.py").exists()


def test_backup_must_contain_every_old_tracked_file(installed):
    instance, remote = installed
    # Attributes can affect git archive even though the tracked source is clean.
    (instance.repo / ".git/info/attributes").write_text("README.md export-ignore\n")
    git(remote, "rm", "README.md")
    git(remote, "commit", "-qm", "remove source")
    old = git(instance.repo, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError, match="archive omitted tracked files"):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert (instance.repo / "README.md").exists()
    assert not list(instance.backups.glob("update-*"))


def test_service_becoming_busy_after_staging_skips_safely(installed, monkeypatch, capsys):
    instance, remote = installed
    publish(remote)
    old = git(instance.repo, "rev-parse", "HEAD")
    calls = []

    def state_changed():
        calls.append(True)
        if len(calls) == 2:
            raise updater.Busy("matrix-funds.service is activating")

    monkeypatch.setattr(instance, "idle_services", state_changed)
    instance.run()
    assert "SKIP" in capsys.readouterr().out
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert not list(instance.backups.glob("update-*"))
    assert not list(instance.backups.glob(".stage-*"))


def test_late_ignored_collision_is_not_overwritten_by_git(installed, monkeypatch):
    instance, remote = installed
    publish(remote, "matrix_etf/new.py")
    original = instance.git
    old = git(instance.repo, "rev-parse", "HEAD")
    (instance.repo / ".git/info/exclude").write_text("matrix_etf/new.py\n")
    collision = instance.repo / "matrix_etf/new.py"

    def concurrent_file(*args, **kwargs):
        if args[0] == "merge":
            collision.parent.mkdir(exist_ok=True)
            collision.write_text("DO_NOT_REPLACE = True\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(instance, "git", concurrent_file)
    with pytest.raises(updater.UpdateError):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert collision.read_text() == "DO_NOT_REPLACE = True\n"


def test_env_change_during_apply_never_reports_success(installed, monkeypatch, capsys):
    instance, remote = installed
    publish(remote)
    original = instance.git

    def concurrent_env_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "merge":
            (instance.repo / ".env").write_text("DUMMY_ADMIN_EDIT=keep\n")
        return result

    monkeypatch.setattr(instance, "git", concurrent_env_edit)
    with pytest.raises(updater.UpdateError, match="verification failed"):
        instance.run()
    assert (instance.repo / ".env").read_text() == "DUMMY_ADMIN_EDIT=keep\n"
    assert "updated successfully" not in capsys.readouterr().out
    with pytest.raises(updater.UpdateError, match="incomplete previous update"):
        instance.run()


def test_symlink_target_is_rejected_before_live_changes(installed):
    instance, remote = installed
    (remote / "matrix_etf").mkdir()
    (remote / "matrix_etf/new.py").symlink_to("../main.py")
    git(remote, "add", "matrix_etf/new.py")
    git(remote, "commit", "-qm", "symlink")
    old = git(instance.repo, "rev-parse", "HEAD")
    with pytest.raises(updater.UpdateError, match="unsupported tracked path/type"):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old


def test_fetch_failure_is_error_not_silent_success(installed, monkeypatch, capsys):
    instance, _ = installed
    original = instance.git
    old = git(instance.repo, "rev-parse", "HEAD")

    def unreachable(*args, **kwargs):
        if args[0] == "fetch":
            raise updater.UpdateError("transport timed out")
        return original(*args, **kwargs)

    monkeypatch.setattr(instance, "git", unreachable)
    with pytest.raises(updater.UpdateError, match="GitHub fetch failed/unreachable"):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert "success" not in capsys.readouterr().out


@pytest.mark.parametrize("path", list(updater.JOB_LOCKS) + ["updater.lock"])
def test_busy_lock_skips_without_fetching(installed, monkeypatch, path, capsys):
    instance, _ = installed
    instance.installation()
    lock = instance.backups / path if path == "updater.lock" else instance.repo / path
    original = instance.git

    def no_fetch(*args, **kwargs):
        assert args[0] != "fetch"
        return original(*args, **kwargs)

    monkeypatch.setattr(instance, "git", no_fetch)
    with instance.lock(lock):
        instance.run()
    output = capsys.readouterr().out
    assert "SKIP" in output and path in output


def test_all_job_locks_held_before_service_checks(installed, monkeypatch):
    instance, remote = installed
    publish(remote)
    calls = []

    def check():
        for name in updater.JOB_LOCKS:
            with (instance.repo / name).open("rb") as stream:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        calls.append(True)

    monkeypatch.setattr(instance, "idle_services", check)
    instance.run()
    assert len(calls) == 2
    for name in updater.JOB_LOCKS:
        with instance.lock(instance.repo / name):
            pass


@pytest.mark.parametrize("state", sorted(updater.BUSY_STATES))
def test_active_transitional_services_are_busy(installed, monkeypatch, state):
    instance, _ = installed
    monkeypatch.setattr(updater, "command", lambda *a, **k: "ActiveState=" + state + "\n")
    with pytest.raises(updater.Busy, match=state):
        updater.Updater.idle_services(instance)


def test_unknown_service_state_fails_closed(installed, monkeypatch):
    instance, _ = installed
    monkeypatch.setattr(updater, "command", lambda *a, **k: "\n")
    with pytest.raises(updater.UpdateError, match="cannot establish idle"):
        updater.Updater.idle_services(instance)


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_service_state_compatible_with_older_systemd(installed, monkeypatch, state):
    instance, _ = installed
    calls = []

    def show(args, **kwargs):
        calls.append(args)
        assert "--value" not in args
        return f"ActiveState={state}\n"

    monkeypatch.setattr(updater, "command", show)
    updater.Updater.idle_services(instance)
    assert len(calls) == len(updater.SERVICES)


def test_wrong_origin_refused_before_fetch(installed):
    instance, _ = installed
    git(instance.repo, "remote", "set-url", "origin", "https://example.invalid/Matrix.git")
    with pytest.raises(updater.UpdateError, match="unexpected origin"):
        instance.run()


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_index_changes_refused(installed, flag):
    instance, _ = installed
    git(instance.repo, "update-index", flag, "main.py")
    (instance.repo / "main.py").write_text("HIDDEN = 1\n")
    with pytest.raises(updater.UpdateError, match="index has"):
        instance.run()


def test_merge_failure_keeps_backup_and_blocks_future_success(installed, monkeypatch, capsys):
    instance, remote = installed
    publish(remote)
    original = instance.git
    before = runtime_snapshot(instance.repo)
    old = git(instance.repo, "rev-parse", "HEAD")

    def fail_merge(*args, **kwargs):
        if args[0] == "merge":
            raise updater.UpdateError("simulated merge failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(instance, "git", fail_merge)
    with pytest.raises(updater.UpdateError, match="simulated merge failure"):
        instance.run()
    assert git(instance.repo, "rev-parse", "HEAD") == old
    assert runtime_snapshot(instance.repo) == before
    backup, = instance.backups.glob("update-*")
    assert (backup / "source.tar").exists() and not (backup / "complete").exists()
    monkeypatch.setattr(instance, "git", original)
    with pytest.raises(updater.UpdateError, match="incomplete previous update"):
        instance.run()
    assert "updated successfully" not in capsys.readouterr().out


def test_git_is_demoted_and_fetch_is_bounded(installed, monkeypatch):
    instance, _ = installed
    monkeypatch.setattr(os, "geteuid", lambda: instance.owner.pw_uid + 1)
    calls = []
    monkeypatch.setattr(updater, "command", lambda args, **kw: calls.append((args, kw)) or "")
    updater.Updater.git(instance, "fetch", "origin", timeout=90)
    args, kwargs = calls[0]
    assert args[:4] == ["runuser", "-u", instance.owner.pw_name, "--"]
    assert "core.hooksPath=/dev/null" in args
    assert kwargs["timeout"] == 90
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "FEISHU_WEBHOOK_URL" not in kwargs["env"]


def test_command_timeout_terminates_process_group():
    with pytest.raises(updater.UpdateError, match="timed out"):
        updater.command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)


def test_backup_retention_only_removes_completed_snapshots(installed):
    instance, remote = installed
    for number in range(5):
        publish(remote, content=f"VALUE = {number + 2}\n")
        instance.run()
    assert len(list(instance.backups.glob("update-*"))) == updater.KEEP_BACKUPS


def test_unit_is_opt_in_private_bounded_and_does_not_start_jobs():
    directory = Path(__file__).resolve().parents[1] / "deploy/systemd"
    service = (directory / "matrix-update.service").read_text()
    timer = (directory / "matrix-update.timer").read_text()
    assert "/usr/local/libexec/matrix-update.py" in service
    assert "EnvironmentFile" not in service
    assert "UMask=0077" in service
    assert "MemoryMax=256M" in service
    assert "TimeoutStartSec=10min" in service
    assert "OnUnitInactiveSec=30min" in timer
    assert "WantedBy=timers.target" in timer
    assert "OnCalendar=*-*-* 09:30:00 Asia/Shanghai" in (
        directory / "matrix-funds.timer"
    ).read_text()
    assert "OnCalendar=Sun 10:00:00 Asia/Shanghai" in (
        directory / "matrix-fund-catalog.timer"
    ).read_text()
