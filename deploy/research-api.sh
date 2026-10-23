#!/usr/bin/env bash
# Run the research API and the web app it serves on a shared machine, without sudo.
#
#   deploy/research-api.sh start [--fake] [--port N]   start in the background and wait until it answers
#   deploy/research-api.sh stop                         stop it (only the process this script started)
#   deploy/research-api.sh status                       is it running, since when, and what it answers
#   deploy/research-api.sh logs [-f] [-n N] [--process] the service log (--process: what the process printed)
#   deploy/research-api.sh backup                       copy the database (and data folder) to backups/
#   deploy/research-api.sh install-web [DIR] [--id ID]  install a built apps/web/dist and switch to it
#   deploy/research-api.sh users ARGS...                research-api users ARGS... with this server's settings
#   deploy/research-api.sh print-cmd [--fake] [--port N] show the command start would run
#
# Everything the service writes lives in one private folder outside the repository:
#
#   $RESEARCH_API_HOME/config.toml   settings (mode 0600), copied from deploy/config.example.toml
#   .../db  .../data  .../logs       database, data files, service log (made by the service, 0700)
#   .../run                          pid file and start time
#   .../backups                      database backups
#   .../web/<id>  .../web/current    installed web builds and the link to the one in use
#   .../incoming                     where a build is copied before install-web (public files only)
#
# start refuses to run when that folder, the settings or anything private in it can be read by
# other accounts, because the disk of a shared server is open to everyone. Settings of this
# machine (RESEARCH_API_HOME, RESEARCH_API_PYTHON, ...) come from the environment or from
# deploy/env.local, which is not in git; a variable set in the environment wins over the file.
# The data folder is $RESEARCH_API_HOME, else ~/research-api. Nothing in this script names a
# machine, a user or a network. The address and the port come from config.toml (bind_host, port),
# because the service refuses to answer on any other address.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
ENV_LOCAL=${RESEARCH_API_ENV_FILE:-$HERE/env.local}

# The text mode of a path, as ls -l prints it (the same on Linux and macOS): 10 characters. A path
# that is gone, because another start removed it, gives status 1. A path that is there but cannot be
# read gives the widest mode, so the checks below refuse it instead of passing it.
# shellcheck disable=SC2012
mode_text() {
  local m
  if m=$(ls -ld -- "$1" 2> /dev/null); then
    printf '%s\n' "${m:0:10}"
  elif [ -e "$1" ] || [ -L "$1" ]; then
    echo '?rwxrwxrwx'
  else
    return 1
  fi
}

# Group or other write permission (positions 6 and 9). A path that is gone has none.
others_can_write() { local m; m=$(mode_text "$1") || return 1; [ "${m:5:1}" != "-" ] || [ "${m:8:1}" != "-" ]; }

# env.local is shell code that this script runs, so it must be the user's own and closed to writes
# by others. The environment wins over it: a variable that is set before the script starts keeps
# its value, so one command can point at another folder (a trial restore) without editing the file.
if [ -f "$ENV_LOCAL" ]; then
  [ -O "$ENV_LOCAL" ] || { echo "research-api: $ENV_LOCAL belongs to another account." >&2; exit 1; }
  if others_can_write "$ENV_LOCAL"; then
    echo "research-api: $ENV_LOCAL can be changed by other accounts; run: chmod go-rwx '$ENV_LOCAL'" >&2
    exit 1
  fi
  keep=
  for name in RESEARCH_API_HOME RESEARCH_API_CONFIG RESEARCH_API_PYTHON RESEARCH_API_PORT \
      RESEARCH_API_BACKUP_KEEP RESEARCH_API_WEB_KEEP RESEARCH_API_STOP_WAIT RESEARCH_API_START_WAIT; do
    if [ -n "${!name:-}" ]; then keep+="$name=$(printf '%q' "${!name}"); export $name; "; fi
  done
  # shellcheck disable=SC1090
  . "$ENV_LOCAL"
  eval "$keep"
  unset keep name
fi

# The data folder: RESEARCH_API_HOME, else research-api in the home folder. HOME is set by cron
# too, unlike USER. A machine with its data disk elsewhere says so in env.local.
ROOT=${RESEARCH_API_HOME:-${HOME:?HOME is not set}/research-api}
CONFIG=${RESEARCH_API_CONFIG:-$ROOT/config.toml}
PY=${RESEARCH_API_PYTHON:-$REPO/.venv/bin/python}
[ -x "$PY" ] || PY=${RESEARCH_API_PYTHON:-python3}
BACKUP_KEEP=${RESEARCH_API_BACKUP_KEEP:-14}
WEB_KEEP=${RESEARCH_API_WEB_KEEP:-5}
STOP_WAIT=${RESEARCH_API_STOP_WAIT:-30}
START_WAIT=${RESEARCH_API_START_WAIT:-60}

RUN=$ROOT/run
PIDF=$RUN/research-api.pid
STARTF=$RUN/started
MODEF=$RUN/mode
ADDRF=$RUN/address
LOCKD=$RUN/start.lock
OUT=$ROOT/logs/process.log
WEB=$ROOT/web
APP=research_api.entrypoints.http:create_app
CLI=research_api.entrypoints.cli
API=/api/v1

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
die() { echo "research-api: $*" >&2; exit 1; }
now() { date -u +%Y%m%dT%H%M%SZ; }
forget_run() { rm -f "$PIDF" "$STARTF" "$MODEF" "$ADDRF"; }

# Is the pid file's process this service: alive, and started with the app and the port that start
# recorded in the address file? A recycled pid (another program, or another instance of this
# service on another port) fails the second test. The files go away when a stop or start removes
# them, so a missing one means "not running", not an error.
running() {
  local pid addr port cmd try
  pid=$(cat "$PIDF" 2> /dev/null) || return 1
  [[ $pid =~ ^[0-9]+$ ]] || return 1
  addr=$(cat "$ADDRF" 2> /dev/null) || return 1
  port=${addr##*:}
  [[ $port =~ ^[0-9]+$ ]] || return 1
  for try in 1 2 3; do
    kill -0 "$pid" 2> /dev/null || return 1
    cmd=$(ps -p "$pid" -o command= 2> /dev/null || true)
    [ -z "$cmd" ] || break   # ps shows nothing for a process that is in the middle of an exec
    sleep 0.1
  done
  [[ $cmd == *"$APP"* && $cmd == *" --port $port "* ]]
}

url_host() { case $1 in *:*) echo "[$1]" ;; *) echo "$1" ;; esac; }

# --- the private folder ----------------------------------------------------------------------

make_folders() {
  umask 077
  local d
  for d in "$ROOT" "$RUN" "$ROOT/logs" "$ROOT/backups" "$WEB" "$ROOT/incoming"; do
    # Two starts at once may both get here: the one that loses the mkdir finds the folder made.
    [ -d "$d" ] || mkdir -m 700 -- "$d" 2> /dev/null || [ -d "$d" ] || die "cannot make the folder $d."
  done
}

# Any group or other permission. A path that is gone (a lock another start just removed) has none.
loose() { local m; m=$(mode_text "$1") || return 1; [ "${m:4:6}" != "------" ]; }

check_private() {
  local bad=0 p
  while IFS= read -r p; do
    [ -e "$p" ] || continue
    if [ ! -O "$p" ]; then
      [ -e "$p" ] || continue   # removed since the check above, not owned by someone else
      echo "research-api: $p belongs to another account." >&2; bad=1
    elif loose "$p"; then
      echo "research-api: $p can be read by other accounts; run: chmod go-rwx '$p'" >&2; bad=1
    fi
  done < <({
    echo "$ROOT"; echo "$CONFIG"; echo "$ENV_LOCAL"
    # incoming and web hold the public build only; everything else is private
    # A start running at the same time makes and removes its lock while find walks the folder.
    find "$ROOT" -mindepth 1 \( -path "$ROOT/web" -o -path "$ROOT/incoming" \) -prune -o ! -type l -print 2> /dev/null
  } | sort -u)
  [ "$bad" = 0 ] || die "refusing to run with files open to other accounts."
}

# --- the settings file -----------------------------------------------------------------------

config_values() {   # host|port|log_dir|db_path|data_dir, with the service's defaults
  "$PY" - "$CONFIG" << 'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    config = tomllib.load(handle)
keys = (("bind_host", "127.0.0.1"), ("port", 8090), ("log_dir", ""), ("db_path", ""), ("data_dir", ""))
print("|".join(str(config.get(key, default)) for key, default in keys))
PY
}

load_config() {
  [ -f "$CONFIG" ] || die "no settings file at $CONFIG (copy deploy/config.example.toml there, chmod 600, edit it)."
  local values
  values=$(config_values) || die "cannot read $CONFIG as TOML."
  IFS='|' read -r HOST PORT LOG_DIR DB_PATH DATA_DIR <<< "$values"
}

# --- the process -----------------------------------------------------------------------------

build_cmd() {   # fills CMD; needs HOST and PORT
  CMD=("$PY" -m uvicorn "$APP" --factory --host "$HOST" --port "$PORT" --workers 1
       --no-proxy-headers --no-access-log --no-use-colors --timeout-graceful-shutdown 10)
}

parse_start_args() {
  FAKE=0; PORT_OVERRIDE=${RESEARCH_API_PORT:-}
  while [ $# -gt 0 ]; do
    case $1 in
      --fake) FAKE=1 ;;
      --port) [ $# -ge 2 ] || usage; PORT_OVERRIDE=$2; shift ;;
      *) usage ;;
    esac
    shift
  done
  if [ "$FAKE" = 1 ]; then
    HOST=127.0.0.1; PORT=${PORT_OVERRIDE:-8090}
  else
    load_config
    [ -z "$PORT_OVERRIDE" ] || PORT=$PORT_OVERRIDE
  fi
  { [[ $PORT =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; } \
    || die "the port must be a number from 1 to 65535, not '$PORT'."
}

port_in_use() { (exec 3<> "/dev/tcp/$1/$2") 2> /dev/null; }

launch() {   # runs "$@" in its own session, so stop ends only this service's processes
  if command -v setsid > /dev/null 2>&1; then
    nohup setsid "$@" >> "$OUT" 2>&1 < /dev/null &
  elif command -v perl > /dev/null 2>&1; then   # macOS has no setsid command
    # shellcheck disable=SC2016
    nohup perl -e 'use POSIX (); POSIX::setsid(); exec { $ARGV[0] } @ARGV or die "exec: $!"' "$@" >> "$OUT" 2>&1 < /dev/null &
  else
    nohup "$@" >> "$OUT" 2>&1 < /dev/null &
  fi
  LAUNCHED=$!
}

cmd_print_cmd() {
  parse_start_args "$@"
  build_cmd
  if [ "$FAKE" = 1 ]; then printf 'RESEARCH_API_FAKE=1 '; else printf 'RESEARCH_API_CONFIG=%q ' "$CONFIG"; fi
  printf 'RESEARCH_API_WEB_DIR=%q' "$WEB/current"
  printf ' %q' "${CMD[@]}"
  echo
}

# One start or stop at a time: the @reboot line and a manual start must not both launch, and a stop
# must not run between a start's launch and its pid file. mkdir is atomic and exists everywhere
# (flock does not on macOS). The folder holds the pid of its owner and that process's command line,
# so a lock left by a process that was killed (or whose pid another program now has) is taken over.
# Other processes make, remove and take over the lock at any moment, so no step assumes what the
# previous one saw: a lock that is gone, or has another owner, only sends the loop round again.
lock_start() {
  umask 077
  local i owner me
  me=$(ps -p $$ -o command= 2> /dev/null || true)
  for ((i = 0; i < (START_WAIT + 30) * 2; i++)); do
    if mkdir -m 700 -- "$LOCKD" 2> /dev/null; then
      # cmd first: a lock with a pid is complete. If the writes fail because the lock was removed
      # right after the mkdir, the loop makes it again; a lock that is still there is a real error.
      if printf '%s\n' "$me" > "$LOCKD/cmd" 2> /dev/null && echo $$ > "$LOCKD/pid" 2> /dev/null; then
        trap unlock_start EXIT
        return 0
      fi
      if [ -d "$LOCKD" ]; then die "cannot write in $LOCKD."; fi
      continue
    fi
    owner=$(cat "$LOCKD/pid" 2> /dev/null || true)
    [ -d "$LOCKD" ] || continue   # gone since the mkdir failed: make it now
    if [[ $owner =~ ^[0-9]+$ ]] && ! lock_owner_alive "$owner"; then
      # Only a takeover that removed the lock, or found it already gone, loops at once; one that did
      # nothing (a guard left by a process that was killed, or an owner that changed) waits like any
      # other turn.
      take_over_lock "$owner" && continue
    fi
    sleep 0.5
  done
  die "another start or stop has held $LOCKD for a long time; if none is running, remove that folder (and $LOCKD.takeover if it exists)."
}

# Is this pid the process that made the lock? It must be alive and have the command line the lock
# recorded (any name the script was started under). A lock with no record, from an older script,
# is judged by the script's name.
lock_owner_alive() {   # lock_owner_alive PID
  local now want
  kill -0 "$1" 2> /dev/null || return 1
  now=$(ps -p "$1" -o command= 2> /dev/null || true)
  [ -n "$now" ] || return 1
  want=$(cat "$LOCKD/cmd" 2> /dev/null || true)
  if [ -n "$want" ]; then [ "$now" = "$want" ]; else [[ $now == *research-api.sh* ]]; fi
}

# Taking over a lock is a check followed by a removal. Two processes doing that at once could remove
# each other's new lock, so only one at a time may do it, and it looks again once it is the one: the
# lock must still hold the same owner, and that owner must still be dead.
take_over_lock() {   # take_over_lock STALE_OWNER_PID; status 0 when the lock is gone, 1 when it is not
  mkdir -m 700 -- "$LOCKD.takeover" 2> /dev/null || return 1   # another start or stop is on it
  local removed=1
  if [ ! -d "$LOCKD" ]; then
    removed=0   # another takeover was first
  elif [ "$(cat "$LOCKD/pid" 2> /dev/null || true)" = "$1" ] && ! lock_owner_alive "$1" \
      && rm -rf -- "$LOCKD" 2> /dev/null; then
    removed=0
  fi
  rmdir -- "$LOCKD.takeover" 2> /dev/null || true
  return "$removed"
}

# Only the lock of this process: if it was taken over, the lock now belongs to another one.
unlock_start() {
  if [ "$(cat "$LOCKD/pid" 2> /dev/null || true)" = "$$" ]; then rm -rf -- "$LOCKD"; fi
}

cmd_start() {
  parse_start_args "$@"
  command -v curl > /dev/null 2>&1 || die "curl is needed to wait for the service."
  make_folders
  check_private
  lock_start
  if running; then
    echo "already running (pid $(cat "$PIDF"), $(cat "$ADDRF"))"
    return 0
  fi
  forget_run
  if [ "$FAKE" != 1 ]; then
    echo "bringing the database up to date"
    "$PY" -m "$CLI" db upgrade --config "$CONFIG" || die "db upgrade failed."
    check_private   # the upgrade may have made the database
  fi
  if port_in_use "$HOST" "$PORT"; then die "something already listens on $HOST:$PORT."; fi
  [ ! -f "$OUT" ] || [ "$(wc -c < "$OUT")" -lt 10485760 ] || mv -f "$OUT" "$OUT.1"
  build_cmd
  if [ "$FAKE" = 1 ]; then
    export RESEARCH_API_FAKE=1; unset RESEARCH_API_CONFIG
  else
    export RESEARCH_API_CONFIG=$CONFIG; unset RESEARCH_API_FAKE
  fi
  export RESEARCH_API_WEB_DIR=$WEB/current   # may not exist yet: the service says so until install-web
  launch "${CMD[@]}"
  local pid=$LAUNCHED address; address="http://$(url_host "$HOST"):$PORT"
  # The pid file last: running() needs the address file as well.
  date +%s > "$STARTF"
  if [ "$FAKE" = 1 ]; then echo fake > "$MODEF"; else echo live > "$MODEF"; fi
  echo "$address" > "$ADDRF"
  echo "$pid" > "$PIDF"
  echo "starting ($(cat "$MODEF") mode) on $address, pid $pid, output in $OUT"
  local i
  for ((i = 0; i < START_WAIT * 2; i++)); do
    if curl --noproxy '*' -sf -m 2 "$address$API/health" > /dev/null 2>&1; then
      echo "ready after $((i / 2)) s: open $address/"
      [ -e "$WEB/current/index.html" ] || echo "note: no web app installed yet (deploy/research-api.sh install-web)"
      return 0
    fi
    if ! kill -0 "$pid" 2> /dev/null; then   # our own child, just launched: no pid can have been reused
      echo "the service exited; last lines of $OUT:" >&2
      tail -n 15 "$OUT" >&2 || true
      forget_run
      return 1
    fi
    sleep 0.5
  done
  echo "not ready after $START_WAIT s; see $OUT" >&2
  return 1
}

signal_service() {   # signal_service SIGNAL PID GROUP
  # In its own session the process group is the pid. Never signal a group this script shares.
  if [ "$3" = "$2" ]; then kill "-$1" -- "-$2" 2> /dev/null || true; else kill "-$1" "$2" 2> /dev/null || true; fi
}

cmd_stop() {
  [ -d "$RUN" ] || { echo "not running"; return 0; }
  lock_start   # a start that is launching has the lock: stop waits for its pid file
  if ! running; then
    forget_run
    echo "not running"
    return 0
  fi
  local pid group
  pid=$(cat "$PIDF" 2> /dev/null) || { forget_run; echo "not running"; return 0; }   # removed by a start
  group=$(ps -o pgid= -p "$pid" 2> /dev/null | tr -d ' ') || group=
  echo "stopping pid $pid"
  signal_service TERM "$pid" "$group"
  local i
  for ((i = 0; i < STOP_WAIT; i++)); do
    running || break
    sleep 1
  done
  if running; then   # checked again: the pid must still be this service before it is killed
    echo "still running after $STOP_WAIT s; killing it" >&2
    signal_service KILL "$pid" "$group"
  fi
  forget_run
  echo "stopped"
}

cmd_status() {
  if ! running; then echo "not running"; return 1; fi
  local pid started up address
  if ! { pid=$(cat "$PIDF" 2> /dev/null) && address=$(cat "$ADDRF" 2> /dev/null); }; then
    echo "not running"; return 1   # the files went away since running looked
  fi
  started=$(cat "$STARTF" 2> /dev/null || date +%s); up=$(($(date +%s) - started))
  echo "running (pid $pid, $(cat "$MODEF") mode, up ${up} s) at $address/"
  if [ -L "$WEB/current" ]; then echo "web app: $(basename "$(readlink "$WEB/current")")"; else echo "web app: none installed"; fi
  local body
  if body=$(curl --noproxy '*' -sf -m 3 "$address$API/health"); then
    echo "health: $body"
  else
    echo "the service does not answer on $address$API/health" >&2
    return 3
  fi
}

cmd_logs() {
  local follow=0 lines=50 file=
  while [ $# -gt 0 ]; do
    case $1 in
      -f) follow=1 ;;
      -n) [ $# -ge 2 ] || usage; lines=$2; shift ;;
      --process) file=$OUT ;;
      *) usage ;;
    esac
    shift
  done
  [[ $lines =~ ^[0-9]+$ ]] || die "-n takes a number."
  if [ -z "$file" ]; then
    LOG_DIR=
    [ ! -f "$CONFIG" ] || load_config
    file=${LOG_DIR:+$LOG_DIR/api.log}
    [ -n "$file" ] || file=$OUT
  fi
  [ -f "$file" ] || die "no log yet at $file."
  if [ "$follow" = 1 ]; then tail -n "$lines" -f "$file"; else tail -n "$lines" "$file"; fi
}

cmd_backup() {
  load_config
  { [ -n "$DB_PATH" ] && [ -f "$DB_PATH" ]; } || die "no database at '${DB_PATH:-db_path is not set}' to back up."
  make_folders
  check_private
  local stamp name copy n=1 status=0
  stamp=$(now); name=research-api-$stamp
  # A second copy in the same second gets -02, -03 ...: the names then sort in the order made.
  while [ -e "$ROOT/backups/$name.sqlite3" ]; do n=$((n + 1)); name=$(printf 'research-api-%s-%02d' "$stamp" "$n"); done
  copy=$ROOT/backups/$name.sqlite3
  # SQLite's own backup call: a consistent copy while the service runs, WAL included.
  "$PY" - "$DB_PATH" "$copy" << 'PY' || { rm -f "$copy"; die "the backup failed."; }
import sqlite3, sys
from pathlib import Path
# as_uri() escapes ?, # and % in the path, which a bare "file:" + path would not.
source = sqlite3.connect(Path(sys.argv[1]).absolute().as_uri() + "?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
source.backup(target)
verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
source.close()
target.close()
if verdict != "ok":
    sys.exit(f"the copy failed its integrity check: {verdict}")
PY
  echo "database: $copy"
  if [ -n "$DATA_DIR" ] && [ -d "$DATA_DIR" ] && [ -n "$(ls -A "$DATA_DIR")" ]; then
    local archive=$ROOT/backups/$name-data.tar.gz rc=0
    # tar exits 1 when a file changed while it was read (a growing run file): the archive is
    # still written, so that is a warning. Anything higher is a failure.
    tar -czf "$archive" -C "$DATA_DIR" . || rc=$?
    if [ "$rc" -eq 0 ]; then
      echo "data:     $archive"
    elif [ "$rc" -eq 1 ]; then
      echo "data:     $archive (a file changed while it was read)"
    else
      rm -f -- "$archive"
      echo "research-api: the archive of $DATA_DIR failed (tar status $rc); the database copy is kept." >&2
      status=1
    fi
  fi
  # Keep the newest $BACKUP_KEEP database copies; a data archive goes with its copy. The names are
  # sorted without the extension and in the C locale, so -02 comes after the plain name.
  local old
  while IFS= read -r old; do
    rm -f -- "$old.sqlite3" "$old-data.tar.gz"
  done < <(find "$ROOT/backups" -maxdepth 1 -name 'research-api-*.sqlite3' | sed 's/\.sqlite3$//' \
             | LC_ALL=C sort -r | tail -n +"$((BACKUP_KEEP + 1))")
  return "$status"
}

# shellcheck disable=SC2012
prune_web() {   # prune_web KEEP_ID: leave the WEB_KEEP newest builds and the one in use
  local name count=0
  while IFS= read -r name; do
    { [ -d "$WEB/$name" ] && [ ! -L "$WEB/$name" ]; } || continue
    count=$((count + 1))
    if [ "$count" -gt "$WEB_KEEP" ] && [ "$name" != "$1" ]; then rm -rf -- "${WEB:?}/${name:?}"; fi
  done < <(ls -1t "$WEB")   # newest first; the names are ones this script made
}

cmd_install_web() {
  local src=$ROOT/incoming id=
  while [ $# -gt 0 ]; do
    case $1 in
      --id) [ $# -ge 2 ] || usage; id=$2; shift ;;
      -*) usage ;;
      *) src=$1 ;;
    esac
    shift
  done
  [ -f "$src/index.html" ] || die "$src has no index.html: copy the output of 'pnpm build' (apps/web/dist) there."
  if [ -n "$(find "$src" -type l -print -quit)" ]; then die "$src contains a symbolic link; a build has none."; fi
  if [ -z "$id" ]; then
    if [ -f "$src/version.json" ]; then
      id=$(sed -n 's/.*"commit"[[:space:]]*:[[:space:]]*"\([0-9A-Za-z._-]\{1,64\}\)".*/\1/p' "$src/version.json" | head -n 1)
    fi
    [ -n "$id" ] || id=build-$(now)
  fi
  [[ $id =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]] || die "the id '$id' must be 1 to 64 letters, digits, dots, dashes or underscores."
  [ "$id" != current ] || die "the id 'current' is the name of the link to the build in use; give --id another name."
  make_folders
  [ ! -e "$WEB/$id" ] || die "build $id is already installed; give --id another name."
  local tmp=$WEB/.incoming-$id.$$
  # A copy that fails leaves nothing behind.
  if ! { cp -R -- "$src" "$tmp" && chmod -R go-rwx "$tmp" && mv -- "$tmp" "$WEB/$id"; }; then
    rm -rf -- "$tmp"
    die "could not copy $src to $WEB/$id."
  fi
  # Replace the link in one step, so a request never meets a missing "current".
  if ! "$PY" -c 'import os, sys; os.symlink(sys.argv[1], sys.argv[2]); os.replace(sys.argv[2], sys.argv[3])' \
    "$id" "$WEB/.current.$$" "$WEB/current"; then
    rm -f -- "$WEB/.current.$$"
    die "could not switch $WEB/current to $id."
  fi
  echo "installed $id; the service serves it now (no restart needed)"
  prune_web "$id"
}

cmd_users() {
  load_config
  make_folders
  check_private
  exec "$PY" -m "$CLI" users "$@" --config "$CONFIG"
}

CMD_NAME=${1:-}
[ $# -eq 0 ] || shift
case $CMD_NAME in
  start) cmd_start "$@" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  logs) cmd_logs "$@" ;;
  backup) cmd_backup ;;
  install-web) cmd_install_web "$@" ;;
  users) cmd_users "$@" ;;
  print-cmd) cmd_print_cmd "$@" ;;
  *) usage ;;
esac
