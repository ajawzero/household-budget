#!/usr/bin/env bash
# Local dev: the API on 127.0.0.1:8000 (auto reload) and the Vite dev server on
# 127.0.0.1:5173, with sample data only. Ctrl-C stops both.
#   scripts/dev.sh
# Everything lives in var/ (git-ignored). Nothing here touches the server or the export folder.
set -euo pipefail
# Never re-lock: use backend/uv.lock exactly as committed. A machine-level uv
# config (for example a package proxy) must not rewrite the lock file.
export UV_FROZEN=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"

mkdir -p "$ROOT/var/import" "$ROOT/var/backups"
chmod 700 "$ROOT/var"

export BUDGET_DEV=1
export BUDGET_DB_PATH="$ROOT/var/budget.db"
export BUDGET_IMPORT_DIR="$ROOT/var/import"
export BUDGET_BACKUP_DIR="$ROOT/var/backups"
export BUDGET_PRIVATE_RULES="$ROOT/samples/private_rules.example.json"

# Copy the sample runs into the import folder so the poll loop imports them.
# Copy to a .partial name first, then rename, the same way real runs arrive.
for run in "$ROOT"/samples/data/*/; do
  name="$(basename "$run")"
  if [ ! -e "$BUDGET_IMPORT_DIR/$name" ]; then
    rm -rf "$BUDGET_IMPORT_DIR/$name.partial"
    cp -R "$run" "$BUDGET_IMPORT_DIR/$name.partial"
    mv "$BUDGET_IMPORT_DIR/$name.partial" "$BUDGET_IMPORT_DIR/$name"
    echo "Copied sample run $name to var/import."
  fi
done

cd "$ROOT/backend"
uv run python -m app.cli migrate

count_users='
from app.db.connect import read_connection
with read_connection() as conn:
    print(conn.execute("SELECT count(*) FROM users").fetchone()[0])
'
users="$(uv run python -c "$count_users")"
if [ "$users" = "0" ]; then
  echo
  echo "No users yet. In another terminal, create the first one:"
  echo "  cd $ROOT/backend"
  echo "  uv run python -m app.cli create-user <username> --display-name <name> --admin"
  echo "It asks for the password. Leave out --admin for a member."
  echo
fi

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Installing frontend packages (npm ci)."
  (cd "$ROOT/frontend" && npm ci)
fi

# Stop both servers (and uvicorn's reload worker) on Ctrl-C or when either exits.
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "Stopping."
  kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log &
api=$!
(cd "$ROOT/frontend" && exec npm run dev) &
web=$!

echo "API: http://127.0.0.1:8000   App: http://127.0.0.1:5173   Ctrl-C to stop."

while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do
  sleep 1
done
echo "A server stopped."
