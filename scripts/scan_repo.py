"""Scan the repo for account numbers, secrets and private names.

Usage (from the repo root or anywhere):
    uv run --project backend python scripts/scan_repo.py            # tracked + untracked files
    uv run --project backend python scripts/scan_repo.py --staged   # staged content (pre-commit)
    uv run --project backend python scripts/scan_repo.py PATH ...   # only these files

Prints only `file:line:rule-id`, never the matched text. Exit 1 on any hit.

Each line is checked three ways:
1. Secret patterns on the raw line (Anthropic keys, auth headers, long values
   assigned to key-like names).
2. `detect_sensitive` on the line after hex tokens of 32+ characters (hashes)
   are removed.
3. The private lists, when a real private rules file exists
   (BUDGET_PRIVATE_RULES, else var/private_rules.json).

Sample run folders (samples/data/<run>/) are checked with schema/check_run.py
instead of raw lines. Money, quantities and byte counts in them would trip the
digit rules, and check_run scans each field with the right exemptions. They still
get the raw secret patterns.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.importer.sanitize import (  # noqa: E402
    EMPTY_PRIVATE_RULES,
    PrivateRules,
    PrivateRulesError,
    detect_sensitive,
    load_private_rules,
)

SAMPLES_DATA = "samples/data/"
EXAMPLE_RULES = ROOT / "samples" / "private_rules.example.json"
LOCAL_RULES = ROOT / "var" / "private_rules.json"
# The only binary files allowed besides the design screenshots in reference/.
# scripts/make_icons.mjs renders them from frontend/public/icon.svg.
BINARY_OK = {"frontend/public/icon-512.png", "frontend/public/apple-touch-icon.png"}

# Hashes are removed before detect_sensitive: hex of 32+ characters, subresource
# integrity values (sha512-<base64>) in lock files, and hex colors that hold a
# letter (like #d9e5f6). An all-digit color is not removed. The digit alphabet
# "0123456789" is removed too.
HASHES = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Fa-f]{32,}(?![0-9A-Za-z])"
    r"|\bsha(?:256|384|512)-[A-Za-z0-9+/]{40,}={0,2}"
    r"|#(?=[0-9]*[A-Fa-f])(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})(?![0-9A-Za-z])"
    r"|0123456789"
)
DIGIT_RUN = re.compile(r"[0-9]+")

# Secret patterns run on the raw line and are never allowlisted.
KEY_NAME = (
    r"[A-Za-z0-9_.-]*"
    r"(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key|access[_-]?key)"
)
LONG_VALUE = (
    r"(?:[0-9A-Fa-f]{32,}"  # long hex
    r"|(?=[A-Za-z0-9+/_-]*[0-9])(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{24,}={0,2})"  # base64
    r"(?![A-Za-z0-9+/_=-])"
)
SECRETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("secret-anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    (
        "secret-auth-header",
        re.compile(
            r"authorization\s*[:=]\s*[\"']?\s*(?:token|bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",
            re.I,
        ),
    ),
    ("secret-assigned", re.compile(KEY_NAME + r"[\"']?\s*[:=]\s*[\"']?" + LONG_VALUE, re.I)),
)

# Known-safe files: path -> {rule id: cap}. Only the listed rules are allowed in
# that file. A cap of None allows the rule anywhere in the file. For digits-run an
# int cap allows digit runs up to that length only (cents literals), so a longer
# run, like an account number, still fails. Keep this short. Every entry has a reason.
DETECT_ALL = (
    "digits-run",
    "digits-split",
    "luhn",
    "phone",
    "split-account",
    "masked-number",
    "email",
    "street-address",
)
CENTS = 8  # up to $999,999.99 written in cents
ALLOW: dict[str, dict[str, int | None]] = {
    # The redaction rules themselves and their spec: examples of each bad form.
    "schema/redaction.json": {"masked-number": None},
    "schema/README.md": dict.fromkeys(DETECT_ALL),
    # Tests that build bad values on purpose (fake, generic test numbers).
    "backend/tests/test_sanitize.py": dict.fromkeys(DETECT_ALL),
    "backend/tests/bad_cases.py": dict.fromkeys(DETECT_ALL),
    "backend/tests/test_format.py": {"digits-run": None, "digits-split": None, "luhn": None},
    "backend/tests/test_check_run.py": {"digits-run": None, "luhn": None},
    "backend/tests/test_redaction_db.py": {"digits-split": None, "luhn": None},
    "backend/tests/test_import.py": {
        "digits-run": None,
        "digits-split": None,
        "split-account": None,
    },
    "backend/tests/test_keys.py": {"masked-number": None},
    "backend/tests/test_db.py": {"digits-run": CENTS},
    "backend/tests/test_transactions_p4.py": {"digits-run": CENTS},
    "backend/tests/test_cashflow_p4.py": {"digits-run": CENTS},
    "backend/tests/test_recurring_p4.py": {"digits-run": CENTS},
    "backend/tests/test_order.py": {"digits-run": CENTS},
    "backend/tests/test_balances.py": {"digits-run": CENTS},
    "frontend/src/lib/format.test.ts": {"digits-run": None},  # formats a 12-digit amount
    "backend/tests/test_dashboard.py": {"digits-run": CENTS, "digits-split": None},
    # Phase 5 sums of cents (sample paychecks, limits, loan balances) and FHFA place ids.
    "backend/tests/test_contributions.py": {
        "digits-run": CENTS,
        "digits-split": None,
        "luhn": None,
    },
    "backend/tests/test_valuations.py": {"digits-run": 9, "digits-split": None},
    # Cents literals, plus micro-unit quantities (9 digits) for micro_str.
    "backend/tests/test_networth.py": {"digits-run": 9, "digits-split": None},
    # Code: cents literals, rule messages and the masked-reference cleaner.
    "scripts/make_samples.py": {"digits-run": CENTS, "masked-number": None},
    "backend/app/importer/models.py": {
        "digits-run": CENTS,
        "digits-split": None,
        "split-account": None,
    },
    "backend/app/importer/keys.py": {"masked-number": None},
    "backend/app/services/balances.py": {"digits-run": CENTS},
    "backend/app/services/networth.py": {"digits-run": CENTS},  # micro_str docstring
    # IRS limits in cents (seed rows).
    "backend/app/db/migrations/0006_contributions_valuations.sql": {"digits-run": CENTS},
    "backend/app/valuations/hpi.py": {"digits-run": 5},  # FHFA place id (CBSA) for Austin
    # Design tokens: all-digit hex colors.
    "frontend/src/styles/tokens.css": {"digits-run": CENTS},
    "docs/DESIGN.md": {"digits-run": CENTS},
    "backend/app/main.py": {"digits-run": CENTS},  # cache max-age in seconds
    # Plan: all-digit colors and the masked-number examples.
    "docs/PLAN.md": {"digits-run": CENTS, "masked-number": None},
    # Package sizes and upload times.
    "backend/uv.lock": {"digits-run": None},
    "frontend/package-lock.json": {"digits-run": None},  # versions and hashes
}


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def list_files(staged: bool) -> list[str]:
    if staged:
        out = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    else:
        out = git("ls-files", "-co", "--exclude-standard", "-z")
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def read_file(path: str, staged: bool) -> bytes | None:
    if staged:
        return git("show", f":{path}")
    p = ROOT / path
    if p.is_symlink() or not p.is_file():
        return None
    return p.read_bytes()


def private_rules() -> tuple[PrivateRules, Path | None]:
    """A real private rules file, if one is set up. The example file never counts."""
    env = os.environ.get("BUDGET_PRIVATE_RULES")
    path = Path(env) if env else LOCAL_RULES
    if not path.is_absolute():
        path = (ROOT / path) if not env else Path.cwd() / path
    if not path.is_file():
        return EMPTY_PRIVATE_RULES, None
    if path.resolve() == EXAMPLE_RULES.resolve():
        return EMPTY_PRIVATE_RULES, None
    try:
        return load_private_rules(path), path.resolve()
    except PrivateRulesError:
        print("scan_repo: private rules file cannot be read", file=sys.stderr)
        sys.exit(2)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def scan_line(
    line: str, private: PrivateRules, secrets_only: bool, allowed: dict[str, int | None]
) -> list[str]:
    found = [rule for rule, rx in SECRETS if rx.search(line)]
    if not secrets_only:
        stripped = HASHES.sub(" ", line)
        for rule in detect_sensitive(stripped):
            if rule in allowed:
                cap = allowed[rule]
                if cap is None:
                    continue
                if rule == "digits-run" and max(map(len, DIGIT_RUN.findall(stripped))) <= cap:
                    continue
            found.append(rule)
        if private:
            found += private.check(line)  # never allowlisted
    return list(dict.fromkeys(found))


def scan_bytes(
    path: str, data: bytes, private: PrivateRules, secrets_only: bool
) -> list[tuple[str, int, str]]:
    if b"\0" in data[:8192]:
        # Binary files cannot be scanned by line. Only the screenshots and icons may be binary.
        ok = path.startswith("reference/") or path in BINARY_OK
        return [] if ok else [(path, 0, "binary-file")]
    text = data.decode("utf-8", errors="replace")
    allowed = ALLOW.get(path, {})
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for rule in scan_line(line, private, secrets_only, allowed):
            hits.append((path, n, rule))
    return hits


def load_check_run():
    spec = importlib.util.spec_from_file_location("check_run", ROOT / "schema" / "check_run.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check_run


def scan_sample_runs(
    files: dict[str, bytes], private_path: Path | None
) -> list[tuple[str, int | str, str]]:
    """Copy the sample run files to a temp folder and run check_run on each run."""
    if not files:
        return []
    check_run = load_check_run()
    hits: list[tuple[str, int | str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "data"
        runs = set()
        for path, data in files.items():
            rel = path[len(SAMPLES_DATA) :]
            runs.add(rel.split("/", 1)[0])
            dest = base / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for run in sorted(runs):
            folder = base / run
            if not folder.is_dir():
                hits.append((f"{SAMPLES_DATA}{run}", 0, "stray-file"))
                continue
            issues = check_run(folder, private_rules=str(private_path) if private_path else None)
            for rule, where in issues:
                file, _, loc = where.partition(":")
                hits.append((f"{SAMPLES_DATA}{run}/{file}", loc or 0, rule))
    return hits


def list_index(prefix: str) -> list[str]:
    out = git("ls-files", "-z", "--", prefix)
    return [p for p in out.decode("utf-8").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan repo files for sensitive text.")
    ap.add_argument("--staged", action="store_true", help="scan staged content (pre-commit)")
    ap.add_argument("paths", nargs="*", help="only these repo-relative paths")
    args = ap.parse_args(argv)

    private, private_path = private_rules()
    if args.paths:
        paths = sorted({Path(p).resolve().relative_to(ROOT).as_posix() for p in args.paths})
    else:
        paths = list_files(args.staged)

    sample_files: dict[str, bytes] = {}
    hits: list[tuple[str, int | str, str]] = []
    for path in paths:
        if private_path and (ROOT / path).resolve() == private_path:
            continue  # the private list itself
        data = read_file(path, args.staged)
        if data is None:
            continue
        in_samples = path.startswith(SAMPLES_DATA)
        if in_samples:
            sample_files[path] = data
        hits += scan_bytes(path, data, private, secrets_only=in_samples)

    if sample_files:
        # A changed run is checked together with the other runs it may build on.
        if args.staged:
            for path in list_index(SAMPLES_DATA):
                if path not in sample_files:
                    sample_files[path] = git("show", f":{path}")
        hits += scan_sample_runs(sample_files, private_path)

    for path, line, rule in hits:
        print(f"{path}:{line}:{rule}")
    if hits:
        files = len({h[0] for h in hits})
        print(f"scan_repo: {len(hits)} hit(s) in {files} file(s)", file=sys.stderr)
        return 1
    print(f"scan_repo: OK ({len(paths)} files)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
