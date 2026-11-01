"""Command line: `uv run python -m app.cli <command> ...`

Commands:
    migrate                          bring the database up to date
    import <path> [--dry-run] [--retry]
                                     a run folder or a folder of runs, in run order
    reapply [--dry-run]              rebuild imported tables from stored runs
    fingerprint                      print the imported-data fingerprint
    privacy-rescan [--dry-run]       clean the private lists' terms from stored data
    purge-run <run_id> [--yes]       remove a run that cannot be cleaned
    backup                           make tonight's backup now (VACUUM INTO + integrity check)
    prices                           fetch prices now (needs TIINGO_API_KEY)
    hpi                              fetch the FHFA house price index now
    create-user <username> --display-name <name> [--admin]
                                     add a user (password asked with getpass)
    set-password <username>          set a password, sign out that user's sessions
    openapi [--out FILE]             print the OpenAPI JSON (no server, no dev mode)
    ai-cache-check                   ask one question twice; the second must read the cache

Output holds run ids, counts, rule ids and locations. Never values from the files.

Extension point: add a command with the `command` decorator. It takes the
name, a help line, and an optional function that adds arguments to the
subparser. The handler gets the parsed args and returns an exit code.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

Handler = Callable[[argparse.Namespace], int]
Configure = Callable[[argparse.ArgumentParser], None]


@dataclass(frozen=True)
class Command:
    help: str
    handler: Handler
    configure: Configure | None = None


COMMANDS: dict[str, Command] = {}


def command(
    name: str, help: str, configure: Configure | None = None
) -> Callable[[Handler], Handler]:
    """Register a subcommand. Registering a name again replaces it."""

    def wrap(fn: Handler) -> Handler:
        COMMANDS[name] = Command(help, fn, configure)
        return fn

    return wrap


def _ensure_db() -> None:
    from app.db.migrate import migrate

    migrate()


# --------------------------------------------------------------------------
# Database and import
# --------------------------------------------------------------------------


@command("migrate", "Bring the database up to the newest migration and seed categories.")
def cmd_migrate(args: argparse.Namespace) -> int:
    from app.db.migrate import migrate

    print(f"Database at version {migrate()}.")
    return 0


def _import_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="a run folder, or a folder of run folders")
    p.add_argument("--dry-run", action="store_true", help="check and count, change nothing")
    p.add_argument("--retry", action="store_true", help="try runs that failed before again")


@command("import", "Import a run folder or a folder of runs, in run order.", _import_args)
def cmd_import(args: argparse.Namespace) -> int:
    from app.db.connect import read_connection
    from app.importer.runner import describe, import_path
    from app.services.fingerprint import fingerprint

    _ensure_db()
    path = Path(args.path)
    if not path.is_dir():
        print("Path is not a folder.", file=sys.stderr)
        return 2
    results = import_path(path, dry_run=args.dry_run, retry=args.retry)
    for r in results:
        for line in describe(r):
            print(line)
    total = sum(r.changes for r in results)
    failed = [r for r in results if not r.ok]
    print(f"{len(results)} runs, {total} changes" if total else f"{len(results)} runs, 0 changes")
    if failed:
        print(f"{len(failed)} not imported")
    if not args.dry_run:
        with read_connection() as conn:
            print(f"fingerprint {fingerprint(conn)}")
    return 1 if failed else 0


def _reapply_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dry-run", action="store_true", help="rebuild, count, then roll back")


@command("reapply", "Empty the imported tables and replay every stored run.", _reapply_args)
def cmd_reapply(args: argparse.Namespace) -> int:
    from app.db.connect import read_connection
    from app.importer.runner import reapply
    from app.services.fingerprint import fingerprint

    _ensure_db()
    counts = reapply(dry_run=args.dry_run)
    print(f"{counts['runs']} runs replayed, {counts['changes']} changes")
    with read_connection() as conn:
        print(f"fingerprint {fingerprint(conn)}")
    return 0


@command("fingerprint", "Print the fingerprint of the imported data.")
def cmd_fingerprint(args: argparse.Namespace) -> int:
    from app.db.connect import read_connection
    from app.services.fingerprint import fingerprint

    _ensure_db()
    with read_connection() as conn:
        print(fingerprint(conn))
    return 0


def _counts(label: str, counts: dict[str, int]) -> None:
    if counts:
        print(f"{label}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))


def _rescan_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dry-run", action="store_true", help="find and count, then roll back")


@command(
    "privacy-rescan",
    "Find the private lists' terms in stored data: mask text, list keys, delete matching "
    "Ask threads, match words and merchant rules.",
    _rescan_args,
)
def cmd_privacy_rescan(args: argparse.Namespace) -> int:
    from app.importer.rescan import RescanError
    from app.importer.runner import private_rules_for
    from app.importer.sanitize import PrivateRulesError
    from app.jobs.privacy import run_rescan

    settings = get_settings()
    _ensure_db()
    try:
        private = private_rules_for(settings)
    except PrivateRulesError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not private:
        print("No private rules are set (BUDGET_PRIVATE_RULES). Nothing to do.", file=sys.stderr)
        return 1
    try:
        report = run_rescan(settings, private, dry_run=args.dry_run)
    except RescanError as exc:
        print(f"Privacy rescan stopped ({exc.reason}). Nothing was changed.", file=sys.stderr)
        purge = exc.summary.get("purge")
        if purge:
            print(f"Purge these runs: {', '.join(purge)}", file=sys.stderr)
        return 1
    s = report.summary()
    print("Dry run, nothing changed." if args.dry_run else "Privacy rescan done.")
    if not report.found:
        print("No matches.")
    _counts("Masked", s["masked"])
    _counts("Listed keys (not masked)", s["listed"])
    _counts("Deleted", s["deleted"])
    if s["rekeyed"]:
        print(f"{s['rekeyed']} transaction keys moved with their edits.")
    if s["lost"]:
        print(f"{s['lost']} credit choices lost their row.")
    if s["runs"]:
        print(f"Run folders that still hold the text: {', '.join(s['runs'])}")
        print(f"Backups from {s['since']} on hold it too. See docs/SETUP.md, Privacy rescan.")
    if s["purge"]:
        print(f"Purge these runs: {', '.join(s['purge'])}")
    return 0


def _purge_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run_id")
    p.add_argument(
        "--yes", action="store_true", help="purge (without it: only say what it would do)"
    )


@command(
    "purge-run",
    "Mark a run purged, delete its stored sections and rebuild the imported tables.",
    _purge_args,
)
def cmd_purge_run(args: argparse.Namespace) -> int:
    from app.db.connect import read_connection
    from app.importer.rescan import RescanError
    from app.jobs.privacy import run_purge
    from app.services.fingerprint import fingerprint

    settings = get_settings()
    _ensure_db()
    try:
        out = run_purge(settings, args.run_id, dry_run=not args.yes)
    except RescanError as exc:
        print(f"Cannot purge that run ({exc.reason}).", file=sys.stderr)
        return 1
    what = f"{out['sections']} stored sections, {out['guards']} credit choice guards"
    if not args.yes:
        print(f"Would purge run {out['run']}: {what}. Nothing changed. Add --yes to purge.")
        return 0
    print(f"Purged run {out['run']}: {what} changed.")
    with read_connection() as conn:
        print(f"fingerprint {fingerprint(conn)}")
    print("Next: see docs/SETUP.md, Privacy rescan (folders and backups).")
    return 0


@command("backup", "Make the nightly backup now, check it and prune old copies.")
def cmd_backup(args: argparse.Namespace) -> int:
    from app.jobs.backup import JOB_NAME, BackupFailed, backup_now
    from app.jobs.scheduler import record_run, utc_now

    settings = get_settings()
    _ensure_db()
    started = utc_now()
    try:
        summary = backup_now(settings, started)
    except BackupFailed as exc:
        record_run(settings.db_path, JOB_NAME, started, utc_now(), "failed", exc.summary)
        print(
            f"Backup failed the integrity check. {exc.summary['file']} was deleted.",
            file=sys.stderr,
        )
        return 1
    record_run(settings.db_path, JOB_NAME, started, utc_now(), "ok", summary)
    print(
        f"Wrote {summary['file']} ({summary['bytes']} bytes), integrity ok. "
        f"{summary['deleted']} old copies deleted."
    )
    return 0


@command("prices", "Fetch prices now (Tiingo, then Yahoo) and record the run.")
def cmd_prices(args: argparse.Namespace) -> int:
    from app.jobs.scheduler import JobFailed, record_run, utc_now
    from app.market.job import JOB_NAME, price_job_parts

    settings = get_settings()
    _ensure_db()
    if settings.tiingo_api_key is None:
        print("TIINGO_API_KEY is not set. Nothing to do.", file=sys.stderr)
        return 1
    run, _ = price_job_parts(settings)
    started = utc_now()
    try:
        summary = run(started)
    except JobFailed as exc:
        record_run(settings.db_path, JOB_NAME, started, utc_now(), "failed", exc.summary)
        print(f"Every symbol failed: {exc.summary.get('errors', {})}", file=sys.stderr)
        return 1
    record_run(settings.db_path, JOB_NAME, started, utc_now(), "ok", summary)
    print(
        f"{summary['updated']} of {summary['symbols']} symbols updated, {summary['rows']} rows. "
        f"Errors: {summary['errors'] or 'none'}."
    )
    return 0


@command(
    "ai-cache-check",
    "Ask one fixed question twice with the Ask prompt. The second call must read the cache. "
    "Needs ANTHROPIC_API_KEY. Costs a few cents.",
)
def cmd_ai_cache_check(args: argparse.Namespace) -> int:
    import asyncio

    from app.ai.cache_check import cache_check
    from app.ai.client import AiError

    settings = get_settings()
    _ensure_db()
    try:
        result = asyncio.run(cache_check(settings))
    except AiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    for i, c in enumerate(result["calls"], 1):
        print(
            f"call {i}: cache write {c['cache_creation_input_tokens']}, "
            f"cache read {c['cache_read_input_tokens']}, input {c['input_tokens']}, "
            f"output {c['output_tokens']}"
        )
    print(f"model {result['model']}: " + ("cache OK" if result["ok"] else "NO cache read"))
    return 0 if result["ok"] else 1


@command("hpi", "Fetch the FHFA house price index now and record the run.")
def cmd_hpi(args: argparse.Namespace) -> int:
    from app.db.connect import connect
    from app.jobs.scheduler import JobFailed, record_run, utc_now
    from app.valuations.hpi import JOB_NAME, run_hpi

    settings = get_settings()
    _ensure_db()
    started = utc_now()
    conn = connect(settings.db_path)
    try:
        summary = run_hpi(conn, started)
    except JobFailed as exc:
        record_run(settings.db_path, JOB_NAME, started, utc_now(), "failed", exc.summary)
        print(f"Index fetch failed: {exc.summary.get('error')}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    record_run(settings.db_path, JOB_NAME, started, utc_now(), "ok", summary)
    newest = ", ".join(f"{k} {v}" for k, v in summary["series"].items())
    print(f"{summary['rows']} index values changed. Newest: {newest}.")
    return 0


# --------------------------------------------------------------------------
# Users. Passwords are read with getpass only, never from an argument.
# --------------------------------------------------------------------------


def read_new_password() -> str | None:
    """Ask twice with getpass. None (with a message) when they differ or are weak."""
    from app.auth.passwords import password_problem

    first = getpass.getpass("New password: ")
    problem = password_problem(first)
    if problem:
        print(problem, file=sys.stderr)
        return None
    if getpass.getpass("Repeat it: ") != first:
        print("The passwords do not match.", file=sys.stderr)
        return None
    return first


def _create_user_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("username")
    p.add_argument("--display-name", required=True, help="the name shown in the app")
    p.add_argument("--admin", action="store_true", help="admin role (default: member)")


@command(
    "create-user", "Create a user. The password is asked for, never passed.", _create_user_args
)
def cmd_create_user(args: argparse.Namespace) -> int:
    from app.auth.login import record_event
    from app.auth.users import UserError, create_user
    from app.db.connect import write_connection

    _ensure_db()
    password = read_new_password()
    if password is None:
        return 2
    role = "admin" if args.admin else "member"
    try:
        with write_connection() as conn:
            user = create_user(conn, args.username, args.display_name, password, role)
            record_event(conn, "user_created", user.id, {"username": user.username, "role": role})
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Created {user.username} ({role}).")
    return 0


def _set_password_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("username")


@command(
    "set-password", "Set a user's password and sign out all their sessions.", _set_password_args
)
def cmd_set_password(args: argparse.Namespace) -> int:
    from app.auth.login import record_event
    from app.auth.passwords import hash_password
    from app.auth.sessions import delete_user_sessions
    from app.auth.users import find_user, store_password_hash
    from app.db.connect import read_connection, write_connection

    _ensure_db()
    with read_connection() as conn:
        found = find_user(conn, args.username)
    if found is None:
        print("No active user with that username.", file=sys.stderr)
        return 2
    user = found[0]
    password = read_new_password()
    if password is None:
        return 2
    new_hash = hash_password(password)
    with write_connection() as conn:
        store_password_hash(conn, user.id, new_hash)
        ended = delete_user_sessions(conn, user.id)
        record_event(conn, "password_changed", user.id, {"username": user.username, "via": "cli"})
    print(f"Password set for {user.username}. {ended} sessions signed out.")
    return 0


# --------------------------------------------------------------------------
# API schema
# --------------------------------------------------------------------------


def _openapi_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", help="write to this file instead of printing")


@command("openapi", "Print the OpenAPI JSON. Starts no server, needs no dev mode.", _openapi_args)
def cmd_openapi(args: argparse.Namespace) -> int:
    import json

    from app.main import create_app

    schema = create_app(get_settings(), start_scheduler=False).openapi()
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}.")
    else:
        sys.stdout.write(text)
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Budget app commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, cmd in COMMANDS.items():
        p = sub.add_parser(name, help=cmd.help, description=cmd.help)
        if cmd.configure:
            cmd.configure(p)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    get_settings()  # fail early on bad settings
    return COMMANDS[args.command].handler(args)


if __name__ == "__main__":
    sys.exit(main())
