"""deploy/research-api.sh: start, stop, status, logs, backup and install-web.

Most tests run the real script against a stand-in for the Python that starts the service (a small
HTTP server that records its command line), so they are fast and need no network. The last ones
start the real service in fake mode through the script: they check the flags that keep a forged
``X-Forwarded-For`` away from the sign-in rate limit and the whole path from install-web to a
signed-in browser-like client.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from research_api.bootstrap import load_settings

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "research-api.sh"
EXAMPLE = REPO / "deploy" / "config.example.toml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="the script needs bash and curl",
)

FAKE_PYTHON = """#!/usr/bin/env bash
# Stands in for the virtualenv's python: the service and the research-api command are faked,
# everything else (python -c, python -) runs on the real interpreter.
case "${{1:-}} ${{2:-}}" in
  "-m uvicorn") exec {python} {fake_dir}/fake_uvicorn.py "$@" ;;
  "-m research_api.entrypoints.cli")
    echo "cli $*" >> {fake_dir}/calls.log
    [ -z "${{FAKE_DB_FAILS:-}}" ] || {{ echo "cannot upgrade" >&2; exit 1; }}
    echo "done: $3 $4"; exit 0 ;;
  *) exec {python} "$@" ;;
esac
"""

FAKE_UVICORN = """
import json, os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

argv = sys.argv[1:]
host, port = argv[argv.index("--host") + 1], int(argv[argv.index("--port") + 1])
record = {"argv": argv, "env": {k: v for k, v in os.environ.items() if k.startswith("RESEARCH_API")}}
with open(os.path.join(os.path.dirname(__file__), "uvicorn.json"), "w") as handle:
    json.dump(record, handle)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true}' if self.path.endswith("/health") else b'{"mode": "fake", "ready": true}'
        status = 200 if self.path.endswith("/health") else 401 if self.path.endswith("/status") else 404
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

def stop(*_):
    with open(os.path.join(os.path.dirname(__file__), "terminated"), "w") as handle:
        handle.write("yes")
    os._exit(0)

signal.signal(signal.SIGTERM, signal.SIG_IGN if os.environ.get("FAKE_IGNORE_TERM") else stop)
HTTPServer((host, port), Handler).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Deploy:
    """One data folder, one fake interpreter and a way to run the script against them."""

    def __init__(self, base: Path, python: str | None = None) -> None:
        self.home = base / "research-api"
        self.fake_dir = base / "fake"
        self.fake_dir.mkdir()
        (self.fake_dir / "fake_uvicorn.py").write_text(FAKE_UVICORN, encoding="utf-8")
        self.python = python or str(self.fake_dir / "python")
        if python is None:
            fake = FAKE_PYTHON.format(python=shlex.quote(sys.executable), fake_dir=self.fake_dir)
            (self.fake_dir / "python").write_text(fake, encoding="utf-8")
            (self.fake_dir / "python").chmod(0o755)
        self.port = free_port()
        self.config = self.home / "config.toml"

    def env(self, **extra: str | None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESEARCH_API")}
        env.update(
            RESEARCH_API_HOME=str(self.home),
            RESEARCH_API_PYTHON=self.python,
            RESEARCH_API_ENV_FILE=str(self.fake_dir / "no-env-file"),
            RESEARCH_API_STOP_WAIT="10",
            RESEARCH_API_START_WAIT="30",
            FAKE_DIR=str(self.fake_dir),
        )
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run(self, *args: str, **extra: str | None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

    def private_home(self) -> None:
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.home.chmod(0o700)

    def write_config(self, extra: str = "") -> Path:
        self.private_home()
        self.config.write_text(
            textwrap.dedent(
                f"""\
                bind_host = "127.0.0.1"
                port = {self.port}
                public_origin = "http://127.0.0.1:{self.port}"
                db_path = "{self.home}/db/research-api.sqlite3"
                data_dir = "{self.home}/data"
                log_dir = "{self.home}/logs"
                """
            )
            + extra,
            encoding="utf-8",
        )
        self.config.chmod(0o600)
        return self.config

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def recorded(self) -> dict[str, object]:
        return json.loads((self.fake_dir / "uvicorn.json").read_text(encoding="utf-8"))

    def calls(self) -> list[str]:
        log = self.fake_dir / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def pid(self) -> int:
        return int((self.home / "run" / "research-api.pid").read_text(encoding="utf-8"))

    def force_stop(self) -> None:
        pid_file = self.home / "run" / "research-api.pid"
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, PermissionError, ValueError):
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)


@pytest.fixture
def deploy(tmp_path: Path) -> Iterator[Deploy]:
    d = Deploy(tmp_path)
    yield d
    d.force_stop()


@pytest.fixture
def real(tmp_path: Path) -> Iterator[Deploy]:
    """The script driving the real service (fake mode) through this environment's Python."""
    d = Deploy(tmp_path, python=sys.executable)
    yield d
    d.force_stop()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- start, status, stop -----------------------------------------------------------------------


def test_start_brings_the_database_up_then_runs_the_service_with_the_safe_flags(
    deploy: Deploy,
) -> None:
    deploy.write_config()
    done = deploy.run("start")
    assert done.returncode == 0, done.stderr
    assert "ready after" in done.stdout
    assert deploy.calls() == [
        f"cli -m research_api.entrypoints.cli db upgrade --config {deploy.config}"
    ]
    record = deploy.recorded()
    argv = record["argv"]
    assert isinstance(argv, list)
    assert argv[:3] == ["-m", "uvicorn", "research_api.entrypoints.http:create_app"]
    assert "--factory" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == str(deploy.port)
    assert argv[argv.index("--workers") + 1] == "1"
    assert "--no-proxy-headers" in argv  # O-122: a forged X-Forwarded-For must not pick the address
    assert "--no-access-log" in argv
    env = record["env"]
    assert isinstance(env, dict)
    assert env["RESEARCH_API_CONFIG"] == str(deploy.config)
    assert env["RESEARCH_API_WEB_DIR"] == str(deploy.home / "web" / "current")
    assert "RESEARCH_API_FAKE" not in env
    assert httpx.get(f"{deploy.origin}/api/v1/health").json() == {"ok": True}


def test_the_folders_and_files_the_script_makes_are_private(deploy: Deploy) -> None:
    deploy.write_config()
    assert deploy.run("start").returncode == 0
    for name in ("run", "logs", "backups", "web", "incoming"):
        assert mode(deploy.home / name) == 0o700, name
    assert mode(deploy.home / "logs" / "process.log") == 0o600
    assert mode(deploy.home / "run" / "research-api.pid") == 0o600


def test_status_says_what_runs_and_stop_ends_only_that_service(deploy: Deploy) -> None:
    deploy.write_config()
    assert deploy.run("status").returncode == 1
    assert "not running" in deploy.run("status").stdout
    deploy.run("start")
    pid = deploy.pid()
    status = deploy.run("status")
    assert status.returncode == 0
    assert f"pid {pid}" in status.stdout
    assert "live mode" in status.stdout
    assert "web app: none installed" in status.stdout
    assert 'health: {"ok": true}' in status.stdout
    stopped = deploy.run("stop")
    assert stopped.returncode == 0
    assert "stopped" in stopped.stdout
    assert not alive(pid)
    assert (deploy.fake_dir / "terminated").exists()  # it got SIGTERM and could shut down cleanly
    assert not (deploy.home / "run" / "research-api.pid").exists()
    assert deploy.run("status").returncode == 1
    assert "not running" in deploy.run("stop").stdout


def test_a_second_start_changes_nothing(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.run("start")
    pid = deploy.pid()
    again = deploy.run("start")
    assert again.returncode == 0
    assert "already running" in again.stdout
    assert deploy.pid() == pid
    assert len(deploy.calls()) == 1  # no second database upgrade


def test_a_leftover_pid_file_does_not_block_a_start(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.run("start")
    pid = deploy.pid()
    os.killpg(pid, signal.SIGKILL)
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert deploy.run("status").returncode == 1  # the pid file is stale, and says so
    assert deploy.run("start").returncode == 0
    assert deploy.pid() != pid


def test_a_reused_pid_is_not_taken_for_the_service(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.private_home()
    (deploy.home / "run").mkdir(mode=0o700)
    (deploy.home / "run" / "research-api.pid").write_text(str(os.getpid()), encoding="utf-8")
    assert deploy.run("status").returncode == 1
    stopped = deploy.run("stop")
    assert "not running" in stopped.stdout
    assert alive(os.getpid())  # stop did not signal an unrelated process


def test_two_starts_at_once_leave_one_service_and_a_pid_file(deploy: Deploy) -> None:
    """The @reboot line and a manual start can meet: one launches, the other finds it running."""
    deploy.write_config()
    env = deploy.env()
    starts = [
        subprocess.Popen(
            ["bash", str(SCRIPT), "start"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outputs = [p.communicate(timeout=90) for p in starts]
    assert [p.returncode for p in starts] == [0, 0, 0], outputs
    said = " ".join(out for out, _ in outputs)
    assert said.count("ready after") == 1
    assert said.count("already running") == 2
    assert len(deploy.calls()) == 1  # one database upgrade
    pid = deploy.pid()
    assert alive(pid)
    assert not (deploy.home / "run" / "start.lock").exists()
    assert deploy.run("stop").returncode == 0
    assert not alive(pid)


def test_a_lock_left_by_a_dead_start_is_taken_over(deploy: Deploy) -> None:
    deploy.write_config()
    gone = subprocess.Popen(["true"])
    gone.wait()
    lock = deploy.home / "run" / "start.lock"
    lock.parent.mkdir(mode=0o700)
    lock.mkdir(mode=0o700)
    (lock / "pid").write_text(str(gone.pid), encoding="utf-8")
    (lock / "pid").chmod(0o600)
    done = deploy.run("start")
    assert done.returncode == 0, done.stderr
    assert not lock.exists()


def test_starts_racing_for_a_dead_lock_leave_one_service(deploy: Deploy) -> None:
    """Taking over a dead lock must not let a slower start delete the new lock of a faster one."""
    deploy.write_config()
    gone = subprocess.Popen(["true"])
    gone.wait()
    lock = deploy.home / "run" / "start.lock"
    lock.parent.mkdir(mode=0o700)
    lock.mkdir(mode=0o700)
    (lock / "pid").write_text(str(gone.pid), encoding="utf-8")
    (lock / "pid").chmod(0o600)
    env = deploy.env()
    starts = [
        subprocess.Popen(
            ["bash", str(SCRIPT), "start"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    outputs = [p.communicate(timeout=90) for p in starts]
    assert [p.returncode for p in starts] == [0] * 6, outputs
    said = " ".join(out for out, _ in outputs)
    assert said.count("ready after") == 1
    assert said.count("already running") == 5
    assert len(deploy.calls()) == 1
    assert alive(deploy.pid())
    assert not lock.exists()
    assert not (deploy.home / "run" / "start.lock.takeover").exists()
    assert deploy.run("stop").returncode == 0


REAL_LS = shutil.which("ls") or "/bin/ls"

# An ls that, the first time it is asked about the path in $VANISH (a path under the data folder),
# removes the lock first and then runs the real ls, which finds nothing. That is another start
# taking the lock over exactly between two steps of this one.
LS_THAT_LOSES_THE_LOCK = """#!/usr/bin/env bash
for arg in "$@"; do
  case $arg in
    *"$VANISH") if mkdir "$FAKE_DIR/ls-once" 2> /dev/null; then rm -rf "$LOCK"; fi ;;
  esac
done
exec {real_ls} "$@"
"""


@pytest.mark.parametrize("asked_about", ["start.lock", "start.lock/pid"])
def test_a_lock_that_vanishes_after_the_permission_check_starts_is_not_an_error(
    deploy: Deploy, asked_about: str
) -> None:
    """Another start removes the lock between find listing it and ls reading its mode."""
    deploy.write_config()
    lock = dead_lock(deploy)
    shim = deploy.fake_dir / "shim"
    shim.mkdir()
    (shim / "ls").write_text(
        LS_THAT_LOSES_THE_LOCK.format(real_ls=shlex.quote(REAL_LS)), encoding="utf-8"
    )
    (shim / "ls").chmod(0o755)
    done = deploy.run(
        "start",
        PATH=f"{shim}{os.pathsep}{os.environ['PATH']}",
        VANISH=f"/run/{asked_about}",
        LOCK=str(lock),
    )
    assert (deploy.fake_dir / "ls-once").exists(), "the lock was never removed"
    assert done.returncode == 0, done.stderr
    assert "No such file" not in done.stderr
    assert "ready after" in done.stdout
    assert not lock.exists()
    assert deploy.run("stop").returncode == 0


@pytest.mark.parametrize("mode_bits", [0o750, 0o770, 0o705])
def test_a_lock_open_to_other_accounts_is_still_refused(deploy: Deploy, mode_bits: int) -> None:
    deploy.write_config()
    lock = dead_lock(deploy)
    lock.chmod(mode_bits)
    done = deploy.run("start")
    assert done.returncode == 1
    assert "can be read by other accounts" in done.stderr
    assert str(lock) in done.stderr
    lock.chmod(0o700)


def sleeper(*words: str) -> subprocess.Popen[bytes]:
    """A live process whose command line carries the given words after the interpreter's own."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *words])


def command_of(pid: int) -> str:
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    )
    return out.stdout.strip()


def record_command(lock: Path, command: str) -> None:
    """The command line a start writes in its lock, to tell its process from a reused pid."""
    (lock / "cmd").write_text(command + "\n", encoding="utf-8")
    (lock / "cmd").chmod(0o600)


def run_in_background(deploy: Deploy, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["bash", str(SCRIPT), *args],
        env=deploy.env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_a_start_run_under_another_name_keeps_its_lock_while_it_lives(deploy: Deploy) -> None:
    """A link to the script (an @reboot line) has a command line without 'research-api.sh'."""
    deploy.write_config()
    holder = sleeper("renamed-link", "start")
    try:
        lock = dead_lock(deploy, owner=holder.pid)
        record_command(lock, command_of(holder.pid))
        start = run_in_background(deploy, "start")
        try:
            time.sleep(3)
            assert start.poll() is None, "the start took over the lock of a live start"
            assert lock.exists()
        finally:
            holder.kill()
            holder.wait()
        out, err = start.communicate(timeout=60)
    finally:
        if start.poll() is None:
            start.kill()
    assert start.returncode == 0, err
    assert "ready after" in out
    assert deploy.run("stop").returncode == 0


def test_a_lock_whose_pid_now_belongs_to_another_program_is_taken_over(deploy: Deploy) -> None:
    deploy.write_config()
    stranger = sleeper("unrelated")
    try:
        lock = dead_lock(deploy, owner=stranger.pid)
        record_command(lock, "bash /somewhere/research-api.sh start")
        done = deploy.run("start")
        assert done.returncode == 0, done.stderr
        assert not lock.exists()
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()
    assert deploy.run("stop").returncode == 0


def test_stop_waits_for_a_start_that_holds_the_lock(deploy: Deploy) -> None:
    """Between its launch and its pid file a start has nothing for stop to find."""
    deploy.write_config()
    holder = sleeper("research-api.sh", "start")
    try:
        lock = dead_lock(deploy, owner=holder.pid)
        record_command(lock, command_of(holder.pid))
        stop = run_in_background(deploy, "stop")
        try:
            time.sleep(2)
            assert stop.poll() is None, "stop ran while a start held the lock"
        finally:
            holder.kill()
            holder.wait()
        out, err = stop.communicate(timeout=60)
    finally:
        if stop.poll() is None:
            stop.kill()
    assert stop.returncode == 0, err
    assert "not running" in out
    assert not (deploy.home / "run" / "start.lock").exists()


def test_stop_without_a_data_folder_says_not_running(deploy: Deploy) -> None:
    done = deploy.run("stop")
    assert done.returncode == 0
    assert "not running" in done.stdout
    assert not deploy.home.exists()


def test_a_pid_file_naming_another_instance_of_the_service_is_not_this_service(
    deploy: Deploy,
) -> None:
    """Same program, other port (say a trial copy): stop and status must leave it alone."""
    deploy.write_config()
    other = sleeper(
        "research_api.entrypoints.http:create_app", "--factory", "--port", "9", "--workers", "1"
    )
    try:
        run = deploy.home / "run"
        run.mkdir(mode=0o700)
        (run / "research-api.pid").write_text(str(other.pid), encoding="utf-8")
        (run / "address").write_text("http://127.0.0.1:8090", encoding="utf-8")
        assert deploy.run("status").returncode == 1
        stopped = deploy.run("stop")
        assert stopped.returncode == 0
        assert "not running" in stopped.stdout
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_a_failed_start_releases_the_lock(deploy: Deploy) -> None:
    deploy.write_config()
    done = deploy.run("start", FAKE_DB_FAILS="1")
    assert done.returncode == 1
    assert not (deploy.home / "run" / "start.lock").exists()


def dead_lock(deploy: Deploy, owner: int | None = None) -> Path:
    """A start.lock folder whose owner pid is that of a finished process, or the one given."""
    if owner is None:
        gone = subprocess.Popen(["true"])
        gone.wait()
        owner = gone.pid
    lock = deploy.home / "run" / "start.lock"
    lock.parent.mkdir(mode=0o700, exist_ok=True)
    lock.mkdir(mode=0o700)
    (lock / "pid").write_text(str(owner), encoding="utf-8")
    (lock / "pid").chmod(0o600)
    return lock


def test_a_takeover_guard_left_behind_is_waited_for_not_spun_on(deploy: Deploy) -> None:
    """A start killed inside the takeover leaves the guard; later starts must not burn their tries."""
    deploy.write_config()
    lock = dead_lock(deploy)
    guard = deploy.home / "run" / "start.lock.takeover"
    guard.mkdir(mode=0o700)
    start = subprocess.Popen(
        ["bash", str(SCRIPT), "start"],
        env=deploy.env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(4)
        assert start.poll() is None, "the start gave up instead of waiting for the guard"
        guard.rmdir()
        out, err = start.communicate(timeout=60)
    finally:
        if start.poll() is None:
            start.kill()
    assert start.returncode == 0, err
    assert "ready after" in out
    assert not lock.exists()
    assert deploy.run("stop").returncode == 0


def test_a_lock_is_not_held_by_an_unrelated_process_that_mentions_research_api(
    deploy: Deploy,
) -> None:
    """A reused pid of, say, a tail of the log must not keep the lock for minutes."""
    deploy.write_config()
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", "research-api/logs/api.log"]
    )
    try:
        lock = dead_lock(deploy, owner=bystander.pid)
        done = deploy.run("start")
        assert done.returncode == 0, done.stderr
        assert not lock.exists()
        assert bystander.poll() is None  # the unrelated process was left alone
    finally:
        bystander.kill()
        bystander.wait()
    assert deploy.run("stop").returncode == 0


def test_stop_kills_a_service_that_ignores_the_request_to_end(deploy: Deploy) -> None:
    deploy.write_config()
    assert deploy.run("start", FAKE_IGNORE_TERM="1").returncode == 0
    pid = deploy.pid()
    done = deploy.run("stop", RESEARCH_API_STOP_WAIT="2")
    assert done.returncode == 0
    assert "killing it" in done.stderr
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.1)
    assert not alive(pid)
    assert not (deploy.home / "run" / "research-api.pid").exists()


def test_a_proxy_in_the_environment_does_not_hide_a_service_on_this_machine(
    deploy: Deploy,
) -> None:
    deploy.write_config()
    dead_proxy = "http://127.0.0.1:1"
    proxied = {
        "http_proxy": dead_proxy,
        "HTTP_PROXY": dead_proxy,
        "all_proxy": dead_proxy,
        "ALL_PROXY": dead_proxy,
        "no_proxy": None,
        "NO_PROXY": None,
    }
    started = deploy.run("start", **proxied)
    assert started.returncode == 0, started.stderr
    assert "ready after" in started.stdout
    status = deploy.run("status", **proxied)
    assert status.returncode == 0, status.stderr
    assert 'health: {"ok": true}' in status.stdout
    assert deploy.run("stop", **proxied).returncode == 0


def test_the_service_runs_in_its_own_process_group(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.run("start")
    pid = deploy.pid()
    assert os.getpgid(pid) == pid != os.getpgrp()


def test_start_refuses_when_the_database_upgrade_fails(deploy: Deploy) -> None:
    deploy.write_config()
    done = deploy.run("start", FAKE_DB_FAILS="1")
    assert done.returncode == 1
    assert "db upgrade failed" in done.stderr
    assert not (deploy.home / "run" / "research-api.pid").exists()


def test_start_refuses_a_port_that_is_taken(deploy: Deploy) -> None:
    deploy.write_config()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", deploy.port))
        taken.listen()
        done = deploy.run("start")
    assert done.returncode == 1
    assert f"already listens on 127.0.0.1:{deploy.port}" in done.stderr
    assert not (deploy.home / "run" / "research-api.pid").exists()


def test_start_reports_a_service_that_dies_at_once(deploy: Deploy) -> None:
    deploy.write_config()
    (deploy.fake_dir / "fake_uvicorn.py").write_text(
        "import sys\nprint('boom: cannot bind', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8"
    )
    done = deploy.run("start")
    assert done.returncode == 1
    assert "boom: cannot bind" in done.stderr
    assert not (deploy.home / "run" / "research-api.pid").exists()


def test_the_port_can_be_given_on_the_command_line(deploy: Deploy) -> None:
    deploy.write_config()
    other = free_port()
    assert deploy.run("start", "--port", str(other)).returncode == 0
    argv = deploy.recorded()["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--port") + 1] == str(other)
    bad = deploy.run("start", "--port", "70000")
    assert bad.returncode == 1 or "already running" in bad.stdout


def test_a_bad_port_is_refused(deploy: Deploy) -> None:
    deploy.write_config()
    for port in ("0", "70000", "http", "-1"):
        done = deploy.run("start", "--port", port)
        assert done.returncode == 1, port
        assert "port must be a number" in done.stderr


# --- the privacy checks ------------------------------------------------------------------------


def test_start_refuses_a_data_folder_others_can_read(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.home.chmod(0o755)
    done = deploy.run("start")
    assert done.returncode == 1
    assert f"{deploy.home} can be read by other accounts" in done.stderr
    assert f"chmod go-rwx '{deploy.home}'" in done.stderr
    assert not (deploy.home / "run" / "research-api.pid").exists()


def test_start_refuses_settings_others_can_read(deploy: Deploy) -> None:
    deploy.write_config()
    for bits in (0o640, 0o644, 0o660, 0o604):
        deploy.config.chmod(bits)
        done = deploy.run("start")
        assert done.returncode == 1, oct(bits)
        assert f"{deploy.config} can be read by other accounts" in done.stderr
    deploy.config.chmod(0o600)
    assert deploy.run("start").returncode == 0


def test_start_refuses_a_database_or_log_others_can_read(deploy: Deploy) -> None:
    deploy.write_config()
    (deploy.home / "db").mkdir(mode=0o700)
    database = deploy.home / "db" / "research-api.sqlite3"
    database.write_text("", encoding="utf-8")
    database.chmod(0o644)
    done = deploy.run("start")
    assert done.returncode == 1
    assert f"{database} can be read by other accounts" in done.stderr
    database.chmod(0o600)
    (deploy.home / "backups").mkdir(mode=0o700, exist_ok=True)
    backup = deploy.home / "backups" / "research-api-20260101T000000Z.sqlite3"
    backup.write_text("", encoding="utf-8")
    backup.chmod(0o640)
    assert deploy.run("start").returncode == 1
    backup.chmod(0o600)
    assert deploy.run("start").returncode == 0


def test_start_needs_a_settings_file(deploy: Deploy) -> None:
    deploy.private_home()
    done = deploy.run("start")
    assert done.returncode == 1
    assert "no settings file" in done.stderr
    assert "config.example.toml" in done.stderr


def test_the_public_build_folders_may_be_open(deploy: Deploy) -> None:
    deploy.write_config()
    (deploy.home / "incoming").mkdir(mode=0o755)
    (deploy.home / "incoming" / "index.html").write_text("x", encoding="utf-8")
    assert deploy.run("start").returncode == 0


# --- fake mode -------------------------------------------------------------------------------


def test_fake_mode_needs_no_settings_and_listens_on_loopback(deploy: Deploy) -> None:
    done = deploy.run("start", "--fake", "--port", str(deploy.port))
    assert done.returncode == 0, done.stderr
    assert deploy.calls() == []  # no database to upgrade
    record = deploy.recorded()
    argv, env = record["argv"], record["env"]
    assert isinstance(argv, list) and isinstance(env, dict)
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert env["RESEARCH_API_FAKE"] == "1"
    assert "RESEARCH_API_CONFIG" not in env
    assert "fake mode" in deploy.run("status").stdout


def test_print_cmd_shows_what_start_runs(deploy: Deploy) -> None:
    deploy.write_config()
    live = deploy.run("print-cmd")
    assert live.returncode == 0
    assert f"RESEARCH_API_CONFIG={deploy.config}" in live.stdout
    assert "--no-proxy-headers" in live.stdout
    fake = deploy.run("print-cmd", "--fake", "--port", "9100")
    assert fake.stdout.startswith("RESEARCH_API_FAKE=1 ")
    assert "--port 9100" in fake.stdout
    assert not (deploy.home / "run" / "research-api.pid").exists()


# --- logs ------------------------------------------------------------------------------------


def test_logs_show_the_service_log_or_the_process_output(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.run("start")
    (deploy.home / "logs" / "api.log").write_text(
        "".join(f"line {i}\n" for i in range(100)), encoding="utf-8"
    )
    last = deploy.run("logs", "-n", "3")
    assert last.stdout.splitlines() == ["line 97", "line 98", "line 99"]
    assert "line 49" not in deploy.run("logs").stdout
    (deploy.home / "logs" / "process.log").write_text("traceback here\n", encoding="utf-8")
    assert "traceback here" in deploy.run("logs", "--process").stdout
    assert deploy.run("logs", "-n", "x").returncode == 1


def test_logs_without_a_log_say_so(deploy: Deploy) -> None:
    deploy.private_home()
    done = deploy.run("logs")
    assert done.returncode == 1
    assert "no log yet" in done.stderr


# --- backup ----------------------------------------------------------------------------------


def make_database(path: Path, rows: int = 3) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE users (name TEXT)")
    db.executemany("INSERT INTO users VALUES (?)", [(f"user{i}",) for i in range(rows)])
    db.commit()
    db.close()
    path.chmod(0o600)


def test_backup_copies_the_database_while_it_is_in_use(deploy: Deploy) -> None:
    deploy.write_config()
    database = deploy.home / "db" / "research-api.sqlite3"
    make_database(database)
    holder = sqlite3.connect(database)  # an open connection, as the running service has
    holder.execute("INSERT INTO users VALUES ('late')")  # in the WAL, not yet in the main file
    holder.commit()
    done = deploy.run("backup")
    holder.close()
    assert done.returncode == 0, done.stderr
    copies = sorted((deploy.home / "backups").glob("research-api-*.sqlite3"))
    assert len(copies) == 1
    assert mode(copies[0]) == 0o600
    assert mode(deploy.home / "backups") == 0o700
    copy = sqlite3.connect(copies[0])
    assert copy.execute("SELECT count(*) FROM users").fetchone()[0] == 4
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copy.close()
    assert "data:" not in done.stdout  # the data folder is empty


def test_backup_also_archives_the_data_folder_when_it_holds_files(deploy: Deploy) -> None:
    deploy.write_config()
    make_database(deploy.home / "db" / "research-api.sqlite3")
    (deploy.home / "data").mkdir(mode=0o700)
    (deploy.home / "data" / "set.json").write_text('{"made": "up"}', encoding="utf-8")
    (deploy.home / "data" / "set.json").chmod(0o600)
    done = deploy.run("backup")
    assert done.returncode == 0, done.stderr
    [archive] = (deploy.home / "backups").glob("*-data.tar.gz")
    assert mode(archive) == 0o600
    listing = subprocess.run(
        ["tar", "-tzf", str(archive)], capture_output=True, text=True, check=True
    )
    assert "set.json" in listing.stdout


def test_backups_in_the_same_second_get_different_names_and_old_ones_are_pruned(
    deploy: Deploy,
) -> None:
    deploy.write_config()
    make_database(deploy.home / "db" / "research-api.sqlite3")
    backups = deploy.home / "backups"
    backups.mkdir(mode=0o700)
    for day in (1, 2, 3):
        old = backups / f"research-api-2026010{day}T000000Z.sqlite3"
        old.write_text("old", encoding="utf-8")
        old.chmod(0o600)
        (backups / f"research-api-2026010{day}T000000Z-data.tar.gz").write_bytes(b"old")
        (backups / f"research-api-2026010{day}T000000Z-data.tar.gz").chmod(0o600)
    for _ in range(2):
        assert deploy.run("backup", RESEARCH_API_BACKUP_KEEP="3").returncode == 0
    names = sorted(p.name for p in backups.glob("*.sqlite3"))
    assert len(names) == 3
    assert names[0].startswith("research-api-2026") and "T000000Z" not in "".join(names[1:])
    assert not (backups / "research-api-20260101T000000Z.sqlite3").exists()
    assert not (backups / "research-api-20260101T000000Z-data.tar.gz").exists()
    assert not (backups / "research-api-20260102T000000Z.sqlite3").exists()


def test_backup_works_for_a_database_path_with_url_characters(deploy: Deploy) -> None:
    odd = deploy.home / "db#1?x%20y"
    config = deploy.write_config()
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            f"{deploy.home}/db/research-api.sqlite3", f"{odd}/research-api.sqlite3"
        ),
        encoding="utf-8",
    )
    make_database(odd / "research-api.sqlite3")
    done = deploy.run("backup")
    assert done.returncode == 0, done.stderr
    [copy] = (deploy.home / "backups").glob("*.sqlite3")
    db = sqlite3.connect(copy)
    assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 3
    db.close()


@pytest.fixture
def fake_tar(tmp_path: Path) -> Path:
    """A tar that writes its archive and then exits with FAKE_TAR_RC."""
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    tool = folder / "tar"
    tool.write_text('#!/bin/sh\necho archive > "$2"\nexit "${FAKE_TAR_RC:-0}"\n', encoding="utf-8")
    tool.chmod(0o755)
    return folder


def with_data(deploy: Deploy) -> None:
    deploy.write_config()
    make_database(deploy.home / "db" / "research-api.sqlite3")
    (deploy.home / "data").mkdir(mode=0o700)
    (deploy.home / "data" / "growing.log").write_text("x", encoding="utf-8")
    (deploy.home / "data" / "growing.log").chmod(0o600)


def test_backup_accepts_a_file_that_changed_while_tar_read_it_and_still_prunes(
    deploy: Deploy, fake_tar: Path
) -> None:
    with_data(deploy)
    backups = deploy.home / "backups"
    backups.mkdir(mode=0o700)
    for day in (1, 2):
        (backups / f"research-api-2026010{day}T000000Z.sqlite3").write_text("old")
        (backups / f"research-api-2026010{day}T000000Z.sqlite3").chmod(0o600)
    done = deploy.run(
        "backup",
        PATH=f"{fake_tar}:{os.environ['PATH']}",
        FAKE_TAR_RC="1",
        RESEARCH_API_BACKUP_KEEP="1",
    )
    assert done.returncode == 0, done.stderr
    assert "changed while it was read" in done.stdout
    assert len(list(backups.glob("*-data.tar.gz"))) == 1
    assert [p.name for p in backups.glob("*.sqlite3")] != []
    assert not (backups / "research-api-20260101T000000Z.sqlite3").exists()  # pruned


def test_backup_reports_a_failed_archive_and_keeps_the_database_copy(
    deploy: Deploy, fake_tar: Path
) -> None:
    with_data(deploy)
    done = deploy.run("backup", PATH=f"{fake_tar}:{os.environ['PATH']}", FAKE_TAR_RC="2")
    assert done.returncode == 1
    assert "archive" in done.stderr and "failed" in done.stderr
    assert len(list((deploy.home / "backups").glob("*.sqlite3"))) == 1
    assert list((deploy.home / "backups").glob("*-data.tar.gz")) == []


def test_pruning_orders_copies_made_in_the_same_second_by_their_number(deploy: Deploy) -> None:
    deploy.write_config()
    make_database(deploy.home / "db" / "research-api.sqlite3")
    backups = deploy.home / "backups"
    backups.mkdir(mode=0o700)
    stem = "research-api-20260101T000000Z"
    for suffix in ("", "-02", "-10"):
        (backups / f"{stem}{suffix}.sqlite3").write_text("old")
        (backups / f"{stem}{suffix}.sqlite3").chmod(0o600)
    done = deploy.run("backup", RESEARCH_API_BACKUP_KEEP="2", LC_ALL="en_US.UTF-8")
    assert done.returncode == 0, done.stderr
    kept = sorted(p.name for p in backups.glob("*.sqlite3"))
    assert f"{stem}-10.sqlite3" in kept  # the newest of the three survives
    assert f"{stem}.sqlite3" not in kept and f"{stem}-02.sqlite3" not in kept
    assert len(kept) == 2


def test_backup_without_a_database_says_so(deploy: Deploy) -> None:
    deploy.write_config()
    done = deploy.run("backup")
    assert done.returncode == 1
    assert "no database at" in done.stderr
    assert (
        not list((deploy.home / "backups").glob("*"))
        if (deploy.home / "backups").exists()
        else True
    )


def test_backup_of_something_that_is_not_a_database_leaves_nothing(deploy: Deploy) -> None:
    deploy.write_config()
    (deploy.home / "db").mkdir(mode=0o700)
    bad = deploy.home / "db" / "research-api.sqlite3"
    bad.write_text("not a database at all", encoding="utf-8")
    bad.chmod(0o600)
    done = deploy.run("backup")
    assert done.returncode == 1
    assert "backup failed" in done.stderr
    assert list((deploy.home / "backups").glob("*.sqlite3")) == []


# --- install-web -----------------------------------------------------------------------------


def make_dist(folder: Path, marker: str = "one", commit: str | None = "abc1234") -> Path:
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(f"<!doctype html><title>{marker}</title>", encoding="utf-8")
    (folder / "assets" / "app-1a2b3c.js").write_text("// js", encoding="utf-8")
    if commit:
        (folder / "version.json").write_text(json.dumps({"commit": commit}), encoding="utf-8")
    folder.chmod(0o755)
    return folder


def test_install_web_copies_privately_and_switches_the_link(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    dist = make_dist(tmp_path / "dist")
    (dist / "assets" / "app-1a2b3c.js").chmod(0o644)
    done = deploy.run("install-web", str(dist))
    assert done.returncode == 0, done.stderr
    assert "installed abc1234" in done.stdout
    installed = deploy.home / "web" / "abc1234"
    assert (installed / "index.html").read_text(encoding="utf-8").endswith("one</title>")
    assert mode(installed) == 0o700
    assert mode(installed / "assets" / "app-1a2b3c.js") == 0o600
    link = deploy.home / "web" / "current"
    assert link.is_symlink() and os.readlink(link) == "abc1234"
    # A newer build takes over; the old one stays for a quick way back.
    second = make_dist(tmp_path / "dist2", "two", commit="def5678")
    assert deploy.run("install-web", str(second)).returncode == 0
    assert os.readlink(link) == "def5678"
    assert (deploy.home / "web" / "abc1234" / "index.html").exists()
    assert "two" in (link / "index.html").read_text(encoding="utf-8")


def test_install_web_reads_the_incoming_folder_by_default_and_names_builds_without_a_commit(
    deploy: Deploy,
) -> None:
    deploy.private_home()
    (deploy.home / "incoming").mkdir(mode=0o755)
    make_dist(deploy.home / "incoming" / "x", commit=None)
    for item in (deploy.home / "incoming" / "x").iterdir():
        shutil.move(item, deploy.home / "incoming" / item.name)
    done = deploy.run("install-web")
    assert done.returncode == 0, done.stderr
    [name] = [p.name for p in (deploy.home / "web").iterdir() if p.name != "current"]
    assert name.startswith("build-2")  # build-<UTC time>
    assert os.readlink(deploy.home / "web" / "current") == name


def test_install_web_refuses_what_is_not_a_build(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    empty = tmp_path / "empty"
    empty.mkdir()
    done = deploy.run("install-web", str(empty))
    assert done.returncode == 1 and "has no index.html" in done.stderr
    linked = make_dist(tmp_path / "linked")
    (linked / "leak").symlink_to("/etc/hosts")
    done = deploy.run("install-web", str(linked))
    assert done.returncode == 1 and "symbolic link" in done.stderr
    good = make_dist(tmp_path / "good")
    for bad_id in ("../up", "a b", ".hidden", "x" * 65, "a/b"):
        done = deploy.run("install-web", str(good), "--id", bad_id)
        assert done.returncode == 1, bad_id
    assert not (deploy.home / "web" / "current").exists()


def test_install_web_does_not_overwrite_a_build(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    dist = make_dist(tmp_path / "dist")
    assert deploy.run("install-web", str(dist), "--id", "v1").returncode == 0
    again = deploy.run("install-web", str(dist), "--id", "v1")
    assert again.returncode == 1 and "already installed" in again.stderr


def test_install_web_does_not_take_the_name_of_the_link(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    dist = make_dist(tmp_path / "dist")
    done = deploy.run("install-web", str(dist), "--id", "current")
    assert done.returncode == 1 and "'current'" in done.stderr
    assert not (deploy.home / "web" / "current").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_a_failed_copy_leaves_nothing_behind(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    dist = make_dist(tmp_path / "dist")
    unreadable = dist / "assets" / "locked.js"
    unreadable.write_text("// x", encoding="utf-8")
    unreadable.chmod(0)
    try:
        done = deploy.run("install-web", str(dist))
    finally:
        unreadable.chmod(0o600)
    assert done.returncode == 1
    assert "could not copy" in done.stderr
    assert list((deploy.home / "web").iterdir()) == []


def test_install_web_keeps_only_the_newest_builds(deploy: Deploy, tmp_path: Path) -> None:
    deploy.private_home()
    for n in range(1, 5):
        dist = make_dist(tmp_path / f"d{n}", str(n), commit=f"c{n}")
        assert deploy.run("install-web", str(dist), RESEARCH_API_WEB_KEEP="2").returncode == 0
        time.sleep(0.05)
    kept = sorted(p.name for p in (deploy.home / "web").iterdir() if p.name != "current")
    assert kept == ["c3", "c4"]
    assert os.readlink(deploy.home / "web" / "current") == "c4"


# --- users -----------------------------------------------------------------------------------


def test_users_runs_the_research_api_command_with_this_servers_settings(deploy: Deploy) -> None:
    deploy.write_config()
    done = deploy.run("users", "add", "marta", "--name", "Marta Prueba")
    assert done.returncode == 0, done.stderr
    assert deploy.calls() == [
        f"cli -m research_api.entrypoints.cli users add marta --name Marta Prueba --config {deploy.config}"
    ]


def test_users_refuses_open_settings_too(deploy: Deploy) -> None:
    deploy.write_config()
    deploy.config.chmod(0o644)
    assert deploy.run("users", "list").returncode == 1
    assert deploy.calls() == []


def test_an_unknown_command_prints_the_usage(deploy: Deploy) -> None:
    for args in ((), ("explode",)):
        done = deploy.run(*args)
        assert done.returncode == 2
        assert "deploy/research-api.sh start" in done.stdout + done.stderr


# --- where the settings come from ------------------------------------------------------------


def write_env_file(deploy: Deploy, text: str, file_mode: int = 0o600) -> Path:
    path = deploy.fake_dir / "env.local"
    path.write_text(text, encoding="utf-8")
    path.chmod(file_mode)
    return path


def test_the_environment_wins_over_env_local(deploy: Deploy, tmp_path: Path) -> None:
    """A trial on a scratch folder must never reach the real one, whatever env.local says."""
    real_home, scratch = tmp_path / "real-home", tmp_path / "scratch-home"
    env_file = write_env_file(deploy, f"RESEARCH_API_HOME={real_home}\nRESEARCH_API_STOP_WAIT=77\n")
    scratch_run = deploy.run(
        "print-cmd", "--fake", RESEARCH_API_ENV_FILE=str(env_file), RESEARCH_API_HOME=str(scratch)
    )
    assert scratch_run.returncode == 0, scratch_run.stderr
    assert f"RESEARCH_API_WEB_DIR={scratch}/web/current" in scratch_run.stdout
    assert str(real_home) not in scratch_run.stdout
    # Without the variable in the environment the file supplies it.
    from_file = deploy.run(
        "print-cmd", "--fake", RESEARCH_API_ENV_FILE=str(env_file), RESEARCH_API_HOME=None
    )
    assert f"RESEARCH_API_WEB_DIR={real_home}/web/current" in from_file.stdout


def test_an_empty_variable_does_not_beat_env_local(deploy: Deploy, tmp_path: Path) -> None:
    real_home = tmp_path / "real-home"
    env_file = write_env_file(deploy, f"RESEARCH_API_HOME={real_home}\n")
    done = deploy.run(
        "print-cmd", "--fake", RESEARCH_API_ENV_FILE=str(env_file), RESEARCH_API_HOME=""
    )
    assert done.returncode == 0, done.stderr
    assert f"RESEARCH_API_WEB_DIR={real_home}/web/current" in done.stdout


def test_a_scratch_start_and_stop_leave_the_real_service_alone(
    deploy: Deploy, tmp_path: Path
) -> None:
    deploy.write_config()
    env_file = write_env_file(deploy, f"RESEARCH_API_HOME={deploy.home}\n")
    assert deploy.run("start", RESEARCH_API_ENV_FILE=str(env_file)).returncode == 0
    pid = deploy.pid()
    scratch = tmp_path / "scratch"
    other = deploy.run("stop", RESEARCH_API_ENV_FILE=str(env_file), RESEARCH_API_HOME=str(scratch))
    assert "not running" in other.stdout
    assert alive(pid)
    assert deploy.run("stop", RESEARCH_API_ENV_FILE=str(env_file)).returncode == 0
    assert not alive(pid)


def test_the_data_folder_defaults_to_research_api_in_the_home_folder(
    deploy: Deploy, tmp_path: Path
) -> None:
    done = deploy.run(
        "print-cmd", "--fake", RESEARCH_API_HOME=None, HOME=str(tmp_path / "me"), USER=None
    )
    assert done.returncode == 0, done.stderr
    assert f"RESEARCH_API_WEB_DIR={tmp_path}/me/research-api/web/current" in done.stdout


def test_an_env_file_others_can_change_is_not_run(deploy: Deploy, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    env_file = write_env_file(deploy, f"touch {marker}\n", file_mode=0o662)
    done = deploy.run("print-cmd", "--fake", RESEARCH_API_ENV_FILE=str(env_file))
    assert done.returncode == 1
    assert "can be changed by other accounts" in done.stderr
    assert not marker.exists()
    env_file.chmod(0o626)
    assert deploy.run("print-cmd", "--fake", RESEARCH_API_ENV_FILE=str(env_file)).returncode == 1


def test_start_refuses_an_env_file_others_can_read(deploy: Deploy) -> None:
    deploy.write_config()
    env_file = write_env_file(deploy, "RESEARCH_API_STOP_WAIT=5\n", file_mode=0o644)
    done = deploy.run("start", RESEARCH_API_ENV_FILE=str(env_file))
    assert done.returncode == 1
    assert "can be read by other accounts" in done.stderr
    assert deploy.calls() == []


def test_nothing_published_names_a_machine() -> None:
    texts = [SCRIPT, EXAMPLE, REPO / "docs" / "run-guide.md"]
    for path in texts:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("/mnt/", "ts.net"):
            assert forbidden not in text, (path.name, forbidden)


# --- the example settings --------------------------------------------------------------------


def test_the_example_settings_load_with_placeholders_only() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    settings = load_settings(EXAMPLE)
    assert settings.port == 8090
    assert settings.origin == "http://100.64.0.10:8090"
    assert str(settings.db_path).startswith("/path/to/")
    for forbidden in ("ts.net", "/mnt/", "@"):
        assert forbidden not in text


# --- the real service through the script -----------------------------------------------------


def wrong_login(origin: str, **headers: str) -> None:
    done = httpx.post(
        f"{origin}/api/v1/auth/login",
        json={"username": "ana", "password": "not-the-password-1"},
        headers={"Origin": origin, **headers},
    )
    assert done.status_code == 401


def failure_addresses(log: Path) -> list[str]:
    found = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("{") and '"login.failure"' in line:
            found.append(json.loads(line)["address"])
    return found


def test_a_forged_forwarded_address_does_not_change_the_address_the_limiter_sees(
    real: Deploy,
) -> None:
    """O-122: the script starts uvicorn with --no-proxy-headers; without it the header wins."""
    done = real.run("start", "--fake", "--port", str(real.port))
    assert done.returncode == 0, done.stderr + done.stdout
    wrong_login(real.origin, **{"X-Forwarded-For": "203.0.113.9"})
    assert real.run("stop").returncode == 0
    assert failure_addresses(real.home / "logs" / "process.log") == ["127.0.0.1"]

    # The control: the command the script prints, minus the flag, lets the forged header through.
    # If uvicorn ever stops trusting it by default, this test says so instead of passing blindly.
    printed = real.run("print-cmd", "--fake", "--port", str(real.port)).stdout
    words = shlex.split(printed)
    env = dict(os.environ, **dict(w.split("=", 1) for w in words if w.startswith("RESEARCH_API_")))
    argv = [w for w in words if not w.startswith("RESEARCH_API_") and w != "--no-proxy-headers"]
    log = real.home / "logs" / "control.log"
    with log.open("w") as out:
        control = subprocess.Popen(argv, env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    if httpx.get(f"{real.origin}/api/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.2)
            wrong_login(real.origin, **{"X-Forwarded-For": "203.0.113.9"})
        finally:
            control.terminate()
            control.wait(timeout=20)
    assert failure_addresses(log) == ["203.0.113.9"]


def test_from_install_web_to_a_signed_in_client(real: Deploy, tmp_path: Path) -> None:
    dist = make_dist(tmp_path / "dist", "first")
    assert real.run("start", "--fake", "--port", str(real.port)).returncode == 0
    with httpx.Client(base_url=real.origin, headers={"Origin": real.origin}) as client:
        assert client.get("/").status_code == 503  # nothing installed yet, and it says so
        assert client.get("/api/v1/health").json() == {"ok": True}
        assert real.run("install-web", str(dist)).returncode == 0
        page = client.get("/")
        assert page.status_code == 200 and "<title>first</title>" in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert "first" in client.get("/login").text
        assert client.get("/assets/app-1a2b3c.js").headers["cache-control"].startswith("public")
        assert client.get("/assets/gone.js").status_code == 404
        assert client.get("/api/v1/nothing").headers["content-type"] == "application/problem+json"
        login = client.post(
            "/api/v1/auth/login", json={"username": "ana", "password": "test-password-ana-01"}
        )
        assert login.status_code == 204
        assert client.get("/api/v1/auth/me").json()["username"] == "ana"
        # A new build is served at once, with no restart and the same session.
        second = make_dist(tmp_path / "dist2", "second", commit="f00d")
        assert real.run("install-web", str(second)).returncode == 0
        assert "<title>second</title>" in client.get("/").text
        assert client.get("/api/v1/auth/me").status_code == 200
    status = real.run("status")
    assert status.returncode == 0 and "web app: f00d" in status.stdout
    assert real.run("stop").returncode == 0
    with pytest.raises(httpx.TransportError):
        httpx.get(f"{real.origin}/api/v1/health")
