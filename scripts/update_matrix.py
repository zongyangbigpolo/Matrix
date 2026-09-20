#!/usr/bin/env python3
"""Opt-in, source-only updates. Install a private copy outside the checkout."""

import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ORIGIN = "https://github.com/zongyangbigpolo/Matrix.git"
JOB_LOCKS = (
    ".matrix_etf.lock", ".matrix_stock.lock", ".matrix_us.lock",
    ".matrix_analytics.lock", ".matrix_funds.lock",
)
SERVICES = (
    "matrix-etf", "matrix-stock", "matrix-us", "matrix-analytics",
    "matrix-backtest", "matrix-funds", "matrix-fund-catalog", "matrix-us-etf",
)
BUSY_STATES = {"active", "activating", "reloading", "deactivating"}
ENTRYPOINTS = {
    "main.py", "stock_main.py", "us_main.py", "analytics_main.py", "fund_main.py",
    "us_etf_main.py",
}
MAX_TREE_BYTES = 64 * 1024 * 1024
KEEP_BACKUPS = 3


class UpdateError(RuntimeError):
    pass


class Busy(UpdateError):
    pass


def log(message):
    print(f"matrix-update: {message}", flush=True)


def command(args, *, cwd=None, env=None, timeout=30, output=None):
    """Bound the entire command process group, including Git's transport children."""
    with subprocess.Popen(
        args, cwd=cwd, env=env, stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    ) as process:
        try:
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise UpdateError(f"{Path(args[0]).name} timed out after {timeout}s") from None
        if process.returncode:
            # Command output may contain URLs, configuration or source; don't journal it.
            raise UpdateError(f"{Path(args[0]).name} exited {process.returncode}")
        return (stdout or b"").decode("utf-8")


def source_only(path):
    parts = PurePosixPath(path).parts
    return (
        path in ENTRYPOINTS
        or path in {"README.md", "LICENSE"}
        or (parts[0] in {"matrix_etf", "tests"} and path.endswith(".py"))
        or (parts[0] == "docs" and path.endswith((".md", ".png", ".jpg", ".svg")))
    )


class Updater:
    def __init__(self, repo=Path("/opt/Matrix"), backups=Path("/opt/matrix-backups"),
                 owner="admin"):
        self.repo = Path(repo)
        self.backups = Path(backups)
        self.owner = pwd.getpwnam(owner)
        self.env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": self.owner.pw_dir,
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
        }

    def git(self, *args, timeout=30, output=None):
        prefix = [] if os.geteuid() == self.owner.pw_uid else [
            "runuser", "-u", self.owner.pw_name, "--",
        ]
        return command(
            prefix + [
                "git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "core.attributesFile=/dev/null", "-c", "merge.autoStash=false",
                "-c", "credential.helper=", "-c", "core.askPass=",
                "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=30",
            ] + list(args),
            cwd=self.repo, env=self.env, timeout=timeout, output=output,
        )

    def installation(self):
        for path in (self.repo, self.repo / ".git"):
            if path.is_symlink() or not path.is_dir():
                raise UpdateError(f"{path} must be a real directory (not a worktree/symlink)")
            if path.stat().st_uid != self.owner.pw_uid:
                raise UpdateError(f"{path} must be owned by {self.owner.pw_name}")
        if Path(self.git("rev-parse", "--show-toplevel").strip()) != self.repo:
            raise UpdateError("unexpected Git work tree")
        if not os.access(self.repo / ".venv/bin/python", os.X_OK):
            raise UpdateError("existing .venv/bin/python required; provision dependencies manually")
        self.backups.mkdir(mode=0o700, parents=False, exist_ok=True)
        info = self.backups.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise UpdateError("backup directory must be private (0700), owned by updater user")

    @contextlib.contextmanager
    def lock(self, path):
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise UpdateError(f"not a regular lock: {path.name}")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy(f"busy lock {path.name}; skipping until next timer") from None
            yield
        finally:
            os.close(descriptor)

    def clean_main(self):
        try:
            branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
        except UpdateError as error:
            raise UpdateError("checkout must be on main; detached HEAD refused") from error
        if branch != "main":
            raise UpdateError("checkout must be on main; detached/non-main checkout refused")
        flags = self.git("ls-files", "-v", "-z").split("\0")
        if any(item and not item.startswith("H ") for item in flags):
            raise UpdateError("index has skip-worktree/assume-unchanged/unmerged entries")
        if self.git("status", "--porcelain=v1", "--untracked-files=no").strip():
            raise UpdateError("dirty tracked checkout; refusing to update")
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                       "rebase-apply", "index.lock", "shallow"):
            if (self.repo / ".git" / marker).exists():
                raise UpdateError(f"unfinished Git operation or shallow history: {marker}")
        for args in (("remote", "get-url", "--all", "origin"),
                     ("remote", "get-url", "--push", "--all", "origin")):
            if self.git(*args).strip() != ORIGIN:
                raise UpdateError(f"unexpected origin; expected exactly {ORIGIN}")
        return self.git("rev-parse", "HEAD").strip()

    def idle_services(self):
        for service in SERVICES:
            output = command(
                ["systemctl", "show", f"{service}.service", "--property=ActiveState"],
                env=self.env, timeout=10,
            ).strip()
            if not output.startswith("ActiveState=") or "\n" in output:
                raise UpdateError(f"cannot establish idle state for {service}.service")
            state = output.partition("=")[2]
            if state in BUSY_STATES:
                raise Busy(f"{service}.service is {state}; skipping until next timer")
            if state not in {"inactive", "failed"}:
                raise UpdateError(f"cannot establish idle state for {service}.service: {state!r}")

    def env_digest(self):
        path = self.repo / ".env"
        if path.is_symlink():
            raise UpdateError(".env must not be a symlink")
        if not path.exists():
            return None
        if not path.is_file():
            raise UpdateError(".env must be a regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def tree(self, revision):
        files = {}
        size = 0
        for record in self.git("ls-tree", "-r", "-l", "-z", revision).split("\0"):
            if not record:
                continue
            metadata, name = record.split("\t", 1)
            mode, kind, oid, length = metadata.split()
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or ".." in parts
                    or parts[0] in {".env", ".git", ".venv", "data", "reports", "logs"}
                    or parts[-1] in {".gitattributes", ".gitmodules"}
                    or parts[0].startswith(".matrix_")
                    or kind != "blob" or mode not in {"100644", "100755"}):
                raise UpdateError(f"unsupported tracked path/type: {name}")
            length = int(length)
            if length > 8 * 1024 * 1024:
                raise UpdateError(f"tracked file exceeds 8 MiB limit: {name}")
            files[name] = (mode, oid, length)
            size += length
        if size > MAX_TREE_BYTES or len(files) > 10000:
            raise UpdateError("tracked tree exceeds safe staging size")
        return files

    def check_changes(self, old_files, new_files):
        removed_entrypoints = sorted(ENTRYPOINTS & (old_files.keys() - new_files.keys()))
        if removed_entrypoints:
            raise UpdateError(
                "manual deployment required; business entrypoints removed: "
                + ", ".join(removed_entrypoints)
            )
        changed = sorted(
            name for name in old_files.keys() | new_files.keys()
            if old_files.get(name) != new_files.get(name)
        )
        unsupported = [name for name in changed if not source_only(name)]
        if unsupported:
            raise UpdateError(
                "manual deployment required; unsupported automatic changes: "
                + ", ".join(unsupported)
                + ". Dependencies/lockfiles, Python version, configuration, runners, updater "
                  "and systemd units must be reviewed and installed together; checkout unchanged"
            )
        for name in changed:
            path = self.repo / name
            for parent in path.parents:
                if parent == self.repo:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise UpdateError(f"unsafe/colliding parent for {name}")
            if name not in old_files and os.path.lexists(path):
                raise UpdateError(f"untracked/ignored path would be overwritten: {name}")
        return changed

    def archive(self, revision, destination):
        with destination.open("xb") as stream:
            self.git("archive", "--format=tar", revision, timeout=60, output=stream)

    def stage(self, archive, directory, files):
        if directory is not None:
            directory.mkdir(mode=0o700)
        found = set()
        oid_length = len(self.git("rev-parse", "HEAD").strip())
        if oid_length not in {40, 64}:
            raise UpdateError("unsupported Git object format")
        algorithm = "sha1" if oid_length == 40 else "sha256"
        with tarfile.open(archive) as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                if not member.isfile() or member.name not in files or member.name in found:
                    raise UpdateError("unexpected file in Git archive")
                mode, oid, length = files[member.name]
                if member.size != length:
                    raise UpdateError(f"archive size mismatch: {member.name}")
                content = bundle.extractfile(member).read()
                actual = hashlib.new(algorithm, f"blob {length}\0".encode() + content).hexdigest()
                if actual != oid:
                    raise UpdateError(f"archive content mismatch: {member.name}")
                if directory is not None:
                    path = directory / member.name
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(int(mode, 8) & 0o777)
                found.add(member.name)
        if found != files.keys():
            raise UpdateError("Git archive omitted tracked files")

    def validate(self, stage):
        # Compile only: never import application code, run tests, load .env or notify Feishu.
        validator = (
            "import pathlib,sys\n"
            "for p in pathlib.Path(sys.argv[1]).rglob('*.py'):\n"
            " try: compile(p.read_bytes(), str(p), 'exec')\n"
            " except (SyntaxError, ValueError):\n"
            "  print('invalid Python: ' + str(p.relative_to(sys.argv[1])))\n"
            "  sys.exit(1)\n"
        )
        try:
            command(
                [str(self.repo / ".venv/bin/python"), "-I", "-S", "-c", validator, str(stage)],
                cwd=stage, env=self.env, timeout=60,
            )
        except UpdateError as error:
            raise UpdateError(f"staged Python syntax validation failed: {error}") from error

    def prune(self):
        completed = []
        for path in self.backups.glob("update-*"):
            if path.is_dir() and not path.is_symlink() and (path / "complete").is_file():
                completed.append(path)
        for path in sorted(completed, key=lambda p: (p / "complete").stat().st_mtime_ns)[:-KEEP_BACKUPS]:
            shutil.rmtree(path)

    def update_locked(self):
        old = self.clean_main()
        try:
            self.git(
                "fetch", "--no-tags", "--no-recurse-submodules",
                "origin", "+refs/heads/main:refs/remotes/origin/main", timeout=90,
            )
        except UpdateError as error:
            raise UpdateError(
                f"GitHub fetch failed/unreachable: {error}; checkout unchanged; next check in 30 min"
            ) from error
        target = self.git("rev-parse", "refs/remotes/origin/main").strip()
        if old == target:
            log(f"already current: {old[:12]}")
            return
        try:
            self.git("merge-base", "--is-ancestor", old, target)
        except UpdateError as error:
            raise UpdateError("local/ahead/divergent main refused; only fast-forward allowed") from error
        old_files, new_files = self.tree(old), self.tree(target)
        self.check_changes(old_files, new_files)
        needed = sum(item[2] for item in old_files.values()) + 2 * sum(
            item[2] for item in new_files.values()
        ) + 32 * 1024 * 1024
        if min(shutil.disk_usage(self.backups).free, shutil.disk_usage(self.repo).free) < needed:
            raise UpdateError("insufficient disk space for source backup, staging and checkout")
        stage_root = self.backups / (".stage-" + uuid.uuid4().hex)
        stage_root.mkdir(mode=0o700)
        backup = None
        try:
            target_archive = stage_root / "target.tar"
            self.archive(target, target_archive)
            self.stage(target_archive, stage_root / "source", new_files)
            self.validate(stage_root / "source")
            # Locks are held before checking ActiveState, including oneshot 'activating'.
            self.idle_services()
            if self.clean_main() != old:
                raise UpdateError("HEAD changed while staging; refusing to apply")
            self.check_changes(old_files, new_files)
            before_env = self.env_digest()
            old_archive = stage_root / "old.tar"
            self.archive(old, old_archive)
            self.stage(old_archive, None, old_files)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.backups / f"update-{stamp}-{old[:12]}-{uuid.uuid4().hex[:8]}"
            backup.mkdir(mode=0o700)
            old_archive.replace(backup / "source.tar")
            (backup / "manifest.json").write_text(json.dumps({
                "old": old, "target": target, "env_sha256": before_env,
                "created": stamp, "scope": "tracked source/config only; no .env or databases",
            }, indent=2) + "\n")
            log(f"validated {target[:12]}; source backup: {backup}")
            self.git("merge", "--ff-only", "--no-edit", "--no-overwrite-ignore", target, timeout=60)
            if self.clean_main() != target or self.env_digest() != before_env:
                raise UpdateError("post-update HEAD/cleanliness/.env verification failed")
            (backup / "complete").touch(mode=0o600)
            log(f"updated successfully: {old[:12]} -> {target[:12]}; no jobs started")
            try:
                self.prune()
            except OSError:
                log("WARNING: backup retention cleanup failed; remove older complete backups manually")
        except (UpdateError, OSError):
            if backup is not None:
                log(f"FAILED during apply/verification; preserve backup {backup}; inspect before retry")
            raise
        finally:
            shutil.rmtree(stage_root)

    def run(self):
        self.installation()
        try:
            with contextlib.ExitStack() as locks:
                locks.enter_context(self.lock(self.backups / "updater.lock"))
                incomplete = [
                    path for path in self.backups.glob("update-*")
                    if path.is_dir() and not (path / "complete").is_file()
                ]
                if incomplete:
                    raise UpdateError(
                        "incomplete previous update; inspect checkout and backup before retry: "
                        + ", ".join(str(path) for path in sorted(incomplete))
                    )
                for name in JOB_LOCKS:
                    locks.enter_context(self.lock(self.repo / name))
                self.idle_services()
                self.update_locked()
        except Busy as error:
            log(f"SKIP: {error}")


def main():
    os.umask(0o077)
    try:
        if os.geteuid() != 0:
            raise UpdateError("run the installed updater as root; Git runs as repository owner admin")
        Updater().run()
    except (UpdateError, OSError, KeyError, ValueError) as error:
        log(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
