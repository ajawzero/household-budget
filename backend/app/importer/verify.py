"""Step 4 of the import (PLAN 4.11): check the run folder and its files.

- The folder name is a run id and is not more than 1 day in the future.
- manifest.json exists, parses, and names this folder.
- Every listed path matches the allowed patterns (the Manifest model checks this).
- Files are opened relative to the run folder with O_NOFOLLOW at every level,
  so a symbolic link anywhere is rejected.
- Each file is at most 20 MB, and its size, sha256 and CSV row count match.
- Any file on disk that the manifest does not list fails the run.

Results are rule ids and locations. Never file contents.
"""

from __future__ import annotations

import csv
import errno
import hashlib
import io
import os
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.importer.models import Issue, Manifest, load_manifest
from app.importer.order import is_future, is_run_id
from app.importer.sanitize import PrivateRules, detect_sensitive, location_section

MAX_FILE_BYTES = 20 * 1024 * 1024
MANIFEST = "manifest.json"
SNAPSHOT = "snapshot.json"
FOLDER = "(folder)"

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)


class FileProblem(Exception):
    """A file could not be read safely. `rule` is the rule id."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


@dataclass
class VerifiedRun:
    run_id: str
    folder: Path
    manifest: Manifest
    manifest_sha256: str
    files: dict[str, bytes] = field(repr=False)  # listed path -> bytes


def safe_where(where: str, private: PrivateRules | None = None) -> str:
    """Hide a location that itself looks sensitive (a file name with digits, a private name)."""
    if detect_sensitive(where, location_section(where)) or (private and private.check(where)):
        head = where.split(":", 1)[0] if ":" in where else ""
        if head and not detect_sensitive(head) and not (private and private.check(head)):
            return f"{head}:(hidden name)"
        return "(hidden name)"
    return where


def open_root(folder: Path) -> int:
    """Open the run folder itself without following a symbolic link."""
    try:
        return os.open(folder, _DIR_FLAGS)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR) and folder.is_symlink():
            raise FileProblem("symlink") from None
        raise FileProblem("file-missing") from None


def read_relative(root_fd: int, rel: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    """Read `rel` under an open folder. No symbolic links at any level, no '..'."""
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise FileProblem("path-not-allowed")
    fds: list[int] = []
    try:
        dir_fd = root_fd
        for name in parts[:-1]:
            try:
                dir_fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
            except OSError as exc:
                raise FileProblem(_open_rule(exc, name, dir_fd)) from None
            fds.append(dir_fd)
        try:
            fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=dir_fd)
        except OSError as exc:
            raise FileProblem(_open_rule(exc, parts[-1], dir_fd)) from None
        fds.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FileProblem("path-not-allowed")
        if info.st_size > max_bytes:
            raise FileProblem("file-too-large")
        chunks = []
        total = 0
        while chunk := os.read(fd, 1 << 20):
            total += len(chunk)
            if total > max_bytes:
                raise FileProblem("file-too-large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        for fd in fds:
            os.close(fd)


def _open_rule(exc: OSError, name: str, dir_fd: int) -> str:
    if exc.errno == errno.ELOOP:
        return "symlink"
    try:
        if stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode):
            return "symlink"
    except OSError:
        pass
    return "file-missing"


def read_manifest_bytes(folder: Path) -> bytes | None:
    """manifest.json bytes, or None when it is missing or unsafe to read."""
    try:
        root = open_root(folder)
    except FileProblem:
        return None
    try:
        return read_relative(root, MANIFEST)
    except FileProblem:
        return None
    finally:
        os.close(root)


def manifest_sha256(folder: Path) -> str | None:
    data = read_manifest_bytes(folder)
    return hashlib.sha256(data).hexdigest() if data is not None else None


def list_files(folder: Path) -> tuple[list[str], list[str]]:
    """All files under the folder as relative POSIX paths, and any symbolic links found."""
    files: list[str] = []
    links: list[str] = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        base = Path(dirpath)
        for d in list(dirnames):
            if (base / d).is_symlink():
                links.append((base / d).relative_to(folder).as_posix())
                dirnames.remove(d)
        for name in filenames:
            rel = (base / name).relative_to(folder).as_posix()
            if (base / name).is_symlink():
                links.append(rel)
            files.append(rel)
    return sorted(files), sorted(links)


def csv_row_count(data: bytes) -> int:
    """Data rows in a CSV (header excluded), or -1 when it cannot be read."""
    try:
        return sum(1 for _ in csv.reader(io.StringIO(data.decode("utf-8"), newline=""))) - 1
    except UnicodeDecodeError, csv.Error:
        return -1


def verify_run(
    folder: Path, today: date, private: PrivateRules | None = None
) -> tuple[VerifiedRun | None, list[Issue]]:
    """Run step 4. Returns the verified files, or None and the issues found."""
    run_id = folder.name
    issues: list[Issue] = []

    def add(rule: str, where: str) -> None:
        issues.append(Issue(rule, safe_where(where, private)))

    def done() -> tuple[None, list[Issue]]:
        return None, sorted(set(issues))

    if not is_run_id(run_id):
        add("run-id", "(folder name)")
        return done()
    if is_future(run_id, today):
        add("run-id-future", "(folder name)")

    try:
        root = open_root(folder)
    except FileProblem as exc:
        add(exc.rule, FOLDER)
        return done()
    try:
        files, links = list_files(folder)
        for rel in links:
            add("symlink", rel)
        if MANIFEST not in files:
            if MANIFEST not in links:
                add("manifest-missing", MANIFEST)
            return done()
        try:
            manifest_bytes = read_relative(root, MANIFEST)
        except FileProblem as exc:
            add(exc.rule, MANIFEST)
            return done()
        manifest, problems = load_manifest(manifest_bytes)
        for p in problems:
            add(p.rule, p.where)
        if manifest is None:
            return done()
        if manifest.run_id != run_id:
            add("run-id-mismatch", f"{MANIFEST}:run_id")

        listed: dict[str, int] = {}
        for i, f in enumerate(manifest.files):
            if f.path in listed:
                add("key-duplicate", f"{MANIFEST}:files[{i}]")
            listed[f.path] = i
        if SNAPSHOT not in listed:
            add("file-missing", f"{MANIFEST}:files")
        for rel in files:
            if rel != MANIFEST and rel not in listed and rel not in links:
                add("file-unlisted", rel)

        contents: dict[str, bytes] = {}
        for f in manifest.files:
            if f.path in contents:
                continue
            try:
                data = read_relative(root, f.path)
            except FileProblem as exc:
                if exc.rule != "symlink" or f.path not in links:
                    add(exc.rule, f.path)
                continue
            if len(data) != f.bytes:
                add("size-mismatch", f.path)
            if hashlib.sha256(data).hexdigest() != f.sha256:
                add("hash-mismatch", f.path)
            if f.rows is not None and csv_row_count(data) != f.rows:
                add("rows-mismatch", f.path)
            contents[f.path] = data
    finally:
        os.close(root)

    if issues:
        return done()
    return (
        VerifiedRun(
            run_id=run_id,
            folder=folder,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            files=contents,
        ),
        [],
    )
