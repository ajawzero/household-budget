#!/usr/bin/env bash
# Run every check before a commit or release. Works from any folder.
#   scripts/check.sh
# Runs all steps, then prints a PASS/FAIL summary. Exit 1 if any step failed.
# Written for the bash 3.2 that ships with macOS.
set -euo pipefail
# Never re-lock: use backend/uv.lock exactly as committed. A machine-level uv
# config (for example a package proxy) must not rewrite the lock file.
export UV_FROZEN=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
export PATH="/opt/homebrew/opt/node@24/bin:$PATH"

# No template: macOS and GNU mktemp both make a random folder under $TMPDIR.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

RESULTS=""
FAILED=0
WARN_EXIT=3  # a step function returns this to record WARN instead of PASS
STEP_NOTE=""

# step <name> <command...>: run one check and record PASS, WARN or FAIL.
step() {
  local name="$1" code=0
  shift
  STEP_NOTE=""
  printf '\n==> %s\n' "$name"
  "$@" || code=$?
  if [ "$code" -eq 0 ]; then
    RESULTS="${RESULTS}PASS  ${name}"$'\n'
  elif [ "$code" -eq "$WARN_EXIT" ]; then
    RESULTS="${RESULTS}WARN  ${name}${STEP_NOTE:+ ($STEP_NOTE)}"$'\n'
  else
    RESULTS="${RESULTS}FAIL  ${name}"$'\n'
    FAILED=1
  fi
}

warn() {
  RESULTS="${RESULTS}WARN  $1"$'\n'
}

in_backend() { (cd "$BACKEND" && "$@"); }
in_frontend() { (cd "$FRONTEND" && "$@"); }

samples_match() {
  in_backend uv run python ../scripts/make_samples.py --out "$TMP/samples/data" >/dev/null \
    || return 1
  local ok=0
  diff -rq "$TMP/samples/data" "$ROOT/samples/data" || ok=1
  cmp -s "$TMP/samples/private_rules.example.json" "$ROOT/samples/private_rules.example.json" \
    || { echo "samples/private_rules.example.json differs"; ok=1; }
  [ "$ok" -eq 0 ] && echo "samples/ matches make_samples.py output."
  return "$ok"
}

# A real export must never land in samples/: every manifest must come from make_samples.
samples_generator() {
  in_backend uv run python - "$ROOT/samples/data" <<'PY'
import json, sys
from pathlib import Path

bad = []
manifests = sorted(Path(sys.argv[1]).glob("*/manifest.json"))
for m in manifests:
    try:
        skill = json.loads(m.read_text(encoding="utf-8")).get("generator", {}).get("skill")
    except (OSError, ValueError, AttributeError):
        skill = None
    if skill != "make_samples":
        bad.append(m.parent.name)
for run in bad:
    print(f"samples/data/{run}: generator.skill is not make_samples")
if not manifests:
    print("no sample manifests found")
print(f"{len(manifests)} sample manifests checked.")
sys.exit(1 if bad or not manifests else 0)
PY
}

# samples/data must be tracked. Before the first commit nothing is staged yet,
# so that case is only a warning.
samples_tracked() {
  if [ -n "$(git -C "$ROOT" ls-files -- samples/data | head -n 1)" ]; then
    echo "samples/data is tracked."
    return 0
  fi
  if ! git -C "$ROOT" rev-parse --verify --quiet HEAD >/dev/null \
    && [ -z "$(git -C "$ROOT" ls-files | head -n 1)" ]; then
    echo "Nothing is committed or staged yet. samples/data must be in the first commit."
    STEP_NOTE="nothing committed or staged yet"
    return "$WARN_EXIT"
  fi
  echo "samples/data is not tracked by git."
  return 1
}

# Every package in uv.lock must come from public PyPI, never a machine's proxy.
lock_uses_pypi() {
  local other
  other="$(grep '^source = { registry' "$ROOT/backend/uv.lock" | grep -vc 'registry = "https://pypi.org/simple"' || true)"
  if [ "$other" != "0" ]; then
    echo "backend/uv.lock has $other package(s) from a registry other than https://pypi.org/simple."
    return 1
  fi
}

step "uv.lock uses PyPI only" lock_uses_pypi
step "ruff check" in_backend uv run ruff check . ../schema/check_run.py ../scripts
step "ruff format --check" in_backend uv run ruff format --check .
step "pytest" in_backend uv run pytest -q
step "scan_repo" in_backend uv run python ../scripts/scan_repo.py
step "samples match make_samples.py" samples_match
step "sample manifests from make_samples" samples_generator
step "samples/data tracked" samples_tracked
step "schema drift (gen_schema --check)" in_backend uv run python ../scripts/gen_schema.py --check

# schema.ts must match what the backend's OpenAPI generates today.
api_types_current() {
  "$ROOT/scripts/gen_api_types.sh" --out "$TMP/schema.ts" >/dev/null || return 1
  if diff -u "$FRONTEND/src/lib/api/schema.ts" "$TMP/schema.ts"; then
    echo "frontend/src/lib/api/schema.ts is up to date."
    return 0
  fi
  echo "schema.ts is out of date. Run scripts/gen_api_types.sh."
  return 1
}
step "api types up to date" api_types_current

if [ -d "$FRONTEND/node_modules" ]; then
  step "frontend typecheck" in_frontend npm run typecheck
  step "frontend tests" in_frontend npm test
  step "frontend build" in_frontend npm run build
else
  warn "frontend skipped (no frontend/node_modules; run npm ci in frontend)"
fi

printf '\n==== Summary ====\n%s' "$RESULTS"
if [ "$FAILED" -ne 0 ]; then
  echo "FAIL"
  exit 1
fi
echo "PASS"
