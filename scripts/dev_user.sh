#!/usr/bin/env bash
# Dev only: create an admin user without typing the password twice.
#   BUDGET_DEV=1 BUDGET_DB_PATH=/path/to/scratch.db scripts/dev_user.sh alex 'example-password'
# Drives `cli create-user` with expect, since it reads the password with
# getpass. Refuses to run unless BUDGET_DEV=1. Never use it on the server.
set -euo pipefail
# Never re-lock: use backend/uv.lock exactly as committed. A machine-level uv
# config (for example a package proxy) must not rewrite the lock file.
export UV_FROZEN=1

if [ "${BUDGET_DEV:-}" != "1" ]; then
  echo "Refusing: set BUDGET_DEV=1. This is for local dev databases only." >&2
  exit 1
fi
if [ $# -ne 2 ]; then
  echo "Usage: BUDGET_DEV=1 scripts/dev_user.sh <name> <password>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="$1"
display="$(printf '%s' "${name:0:1}" | tr '[:lower:]' '[:upper:]')${name:1}"

cd "$ROOT/backend"
# The password goes to expect through the environment, not its command line.
DEV_USER_NAME="$name" DEV_USER_DISPLAY="$display" DEV_USER_PASS="$2" /usr/bin/expect <<'EXPECT'
log_user 0
set timeout 60
spawn uv run python -m app.cli create-user $env(DEV_USER_NAME) --display-name $env(DEV_USER_DISPLAY) --admin
expect {
  "New password:" { send -- "$env(DEV_USER_PASS)\r" }
  timeout { puts stderr "No password prompt."; exit 1 }
  eof { puts stderr "create-user stopped early."; exit 1 }
}
expect {
  "Repeat it:" { send -- "$env(DEV_USER_PASS)\r" }
  timeout { puts stderr "No repeat prompt."; exit 1 }
}
expect eof
catch wait result
set code [lindex $result 3]
if {$code != 0} { puts stderr "create-user failed (exit $code)."; exit $code }
EXPECT
echo "Created dev user $name."
