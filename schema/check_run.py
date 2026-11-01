#!/usr/bin/env python3
"""Check one import run folder against format v1, before the app sees it.

Standard library only. Runs on Python 3.10 or newer, on macOS, Windows or Linux.
It reads import-v1.schema.json, redaction.json and categories.json from its own
folder (data/_schema/ for the chat, schema/ in the repo).

Usage:
    python check_run.py <run-folder>                  # after manifest.json is written
    python check_run.py <run-folder> --pre            # before manifest.json exists
    python check_run.py <run-folder> --private-rules <private_rules.json>
    python check_run.py <run-folder> --json           # machine-readable output

It also rejects a section, field or value newer than the run's schema_version
(x-section-since, x-field-since and x-value-since in the schema file).

It prints one line per problem: a rule id and a location. Warnings (for
example a utility bill total off by a cent, a stale rewards balance, a
trip_key no run defines, or an active bill with no date) print the same way
with "(warning)" after them and do not fail the run. It never prints a value
from the files. Exit code 0 means the run passed, 1 means it failed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DAYS_AHEAD = 1
HISTORY_START = "2026-01-01"
TXN_DAYS_BEFORE = 60
POST_DAYS_AFTER = 5

RUN_ID_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:-([2-9]|[1-9][0-9]+))?")
ALLOWED_PATH_RE = re.compile(r"transactions/[a-z0-9-]+\.csv")
CSV_NAME_RE = re.compile(r"transactions/(?P<account>.+)-(?P<end>[0-9]{4}-[0-9]{2}-[0-9]{2})\.csv")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+")

STATE_SECTIONS = (
    "meta",
    "cashflow_plan",
    "cashflow_events",
    "cashflow_phases",
    "budgets",
    "payoff_sequence",
    "todos",
    "review",
    "recurring",
    "installment_plans",
    "flags",
    "pending",
    "rewards",
    "goals",
)
IDENTITY = {
    "people": ("key",),
    "accounts": ("key",),
    "new_accounts": ("key",),
    "statements": ("account_key", "period_end"),
    "balances": ("account_key", "as_of", "source"),
    "holdings": ("account_key", "period_end", "key"),
    "income.paychecks": ("person", "pay_date", "kind", "seq"),
    "income.income_streams": ("key",),
    "contributions": ("person", "account_key", "kind", "source", "date"),
    "cashflow_actual": ("month",),
    "cashflow_plan": ("month",),
    "cashflow_events": ("key",),
    "cashflow_phases": ("key",),
    "budgets": ("month", "key"),
    "payoff_sequence": ("key",),
    "todos": ("key",),
    "recurring": ("key",),
    "installment_plans": ("key",),
    "flags": ("key",),
    "pending": ("key",),
    "utility_bills": ("utility_key", "period_end"),
    "rewards.programs": ("key",),
    "rewards.benefits": ("key",),
    "rewards.milestones": ("key",),
    "rewards.routing": ("key",),
    "rewards.redemptions": ("key",),
    "trips": ("key",),
    "goals": ("key",),
}
# Units each utility service may use (format 1.2).
_ANY_UNIT = {"kwh", "therms", "gallons", "ccf", "none"}
UTILITY_UNITS = {
    "electric": {"kwh"},
    "gas": {"therms"},
    "water": {"gallons", "ccf"},
    "sewer": {"gallons", "ccf"},
    "stormwater": _ANY_UNIT,
    "other": _ANY_UNIT,
}
UTILITY_EARLIEST = "2000-01-01"
UTILITY_ROUNDING_CENTS = 2
# Format 1.3 rewards and trips.
TRIP_EARLIEST = "2000-01-01"
REWARD_STALE_DAYS = 90
BENEFIT_FIELDS = {
    "credit": {"cadence", "amount", "cycles"},
    "earning_cap": {"cap", "earn_before", "earn_after", "category_keys", "reported_spent", "as_of"},
    "status_perk": {"program_key", "status_tier"},
    "free_night": {"program_key"},
    "other": set(),
}
BENEFIT_REQUIRED = {
    "credit": ("cadence", "amount"),
    "earning_cap": ("cap", "earn_before", "earn_after", "category_keys"),
    "status_perk": ("program_key", "status_tier"),
    "free_night": ("program_key",),
    "other": (),
}
CASH_LIKE_TYPES = {"checking", "savings", "credit_card", "store_card", "loan", "mortgage", "heloc"}
INVESTMENT_TYPES = {"brokerage", "retirement", "529", "hsa", "custodial", "nqdc"}
# Format 1.4: account types a college goal may hold (goal-fields), the dates that
# anchor a recurring item's due date (warning recurring-no-date), and the personal
# subscription's name, which never carries a tag (recurring-tag-private).
COLLEGE_ACCOUNT_TYPES = {"529", "custodial"}
DEBT_ORIGINAL_TYPES = {"loan", "mortgage"}  # 1.4 accounts.original (debt-terms)
RECURRING_DATE_FIELDS = ("next_date", "start_date", "renewal_date")
PERSONAL_SUBSCRIPTION = "Personal subscription"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def to_cents(value: str) -> int:
    neg = value.startswith("-")
    whole, _, frac = value.lstrip("-").partition(".")
    n = int(whole) * 100 + int((frac + "00")[:2])
    return -n if neg else n


def version_tuple(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def is_personal_subscription(name: str) -> bool:
    """The name is the personal subscription's, ignoring case and extra spaces."""
    return " ".join(name.split()).casefold() == PERSONAL_SUBSCRIPTION.casefold()


def since_nodes(s: dict, path: str) -> list[tuple[dict, str]]:
    """The objects an x-field-since or x-value-since path ends in, with their locations.

    Lists on the way are walked item by item: 'trips.items.due_on' gives
    (item, 'snapshot.json:trips[0].items[1]') for each trip item. An object without
    the next field (a tombstone) is skipped. The app's models.since_nodes walks the same way.
    """
    nodes = [(s, "snapshot.json:")]
    for part in path.split(".")[:-1]:
        found = []
        for obj, loc in nodes:
            value = obj.get(part)
            here = loc + part if loc.endswith(":") else f"{loc}.{part}"
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        found.append((item, f"{here}[{i}]"))
            elif isinstance(value, dict):
                found.append((value, here))
        nodes = found
    return nodes


def parse_run_id(text: str) -> tuple[date, int] | None:
    m = RUN_ID_RE.fullmatch(text)
    if not m:
        return None
    try:
        day = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return day, int(m.group(2) or 1)


def real_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def real_timestamp(v: str) -> bool:
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def cusip_valid(v: str) -> bool:
    if not re.fullmatch(r"[0-9A-Z]{8}[0-9]", v):
        return False
    total = 0
    for i, ch in enumerate(v[:8]):
        n = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10
        if i % 2 == 1:
            n *= 2
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(v[8])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def to_micro(value: str) -> int:
    whole, _, frac = value.partition(".")
    return int(whole) * 1_000_000 + int((frac + "0" * 6)[:6])


def together(*values: object) -> bool:
    """True when the values are all set or all left out."""
    return len({v is None for v in values}) == 1


def section_of(loc: str) -> str | None:
    """The snapshot section of a location: 'snapshot.json:trips[0].note' -> 'trips'."""
    if not loc.startswith("snapshot.json:"):
        return None
    return re.split(r"[.\[]", loc[len("snapshot.json:") :], maxsplit=1)[0]


# --------------------------------------------------------------------------
# Redaction (same algorithm as backend/app/importer/sanitize.py)
# --------------------------------------------------------------------------


class Redaction:
    def __init__(self, path: Path, private: dict | None = None) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.masks = [re.compile(m["pattern"]) for m in data["masks"]]
        self.rules = []
        for r in data["rules"]:
            flags = re.IGNORECASE if r.get("ignore_case") else 0
            self.rules.append((r, re.compile(r["pattern"], flags) if "pattern" in r else None))
        # A rule with sections runs only on text from those snapshot sections.
        sp = data["stock_plan"]
        self.stock_plan_id = sp["id"]
        self.stock_plan = re.compile(sp["pattern"], re.IGNORECASE if sp.get("ignore_case") else 0)
        self.private_ids = data["private"]["rule_ids"]
        self.min_squashed = int(data["private"]["min_squashed"])
        self.private = private or {}

    @staticmethod
    def _digits(s: str) -> str:
        return "".join(ch for ch in s if ch.isascii() and ch.isdigit())

    @staticmethod
    def _luhn(digits: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(digits)):
            n = int(ch)
            if i % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return total % 10 == 0

    @staticmethod
    def normalize(text: str) -> str:
        """NFKC, ASCII digits, '-' for dashes, ' ' for spaces, no format characters."""
        out = []
        for ch in unicodedata.normalize("NFKC", text):
            if ch.isascii():
                out.append(ch)
                continue
            digit = unicodedata.decimal(ch, None)
            cat = unicodedata.category(ch)
            if digit is not None:
                out.append(str(digit))
            elif cat == "Pd" or ch == "\u2212":
                out.append("-")
            elif ch.isspace() or cat == "Zs":
                out.append(" ")
            elif cat != "Cf":
                out.append(ch)
        return "".join(out)

    def _luhn_hit(self, spec: dict, text: str) -> bool:
        gap_rx, tight_rx = re.compile(spec["gap"]), re.compile(spec["tight_gap"])
        lo, hi, loose_min = spec["min_digits"], spec["max_digits"], spec["loose_min_group"]
        groups = [(m.start(), m.end(), m.group()) for m in re.finditer(r"[0-9]+", text)]
        for i in range(len(groups)):
            digits = groups[i][2]
            if lo <= len(digits) <= hi and self._luhn(digits):
                return True
            for j in range(i + 1, len(groups)):
                prev, cur = groups[j - 1], groups[j]
                gap = text[prev[1] : cur[0]]
                if not gap_rx.fullmatch(gap):
                    break
                if not tight_rx.fullmatch(gap) and min(len(prev[2]), len(cur[2])) < loose_min:
                    break
                digits += cur[2]
                if len(digits) > hi:
                    break
                if len(digits) >= lo and self._luhn(digits):
                    return True
        return False

    def detect(self, text: str, section: str | None = None) -> list[str]:
        text = self.normalize(text)
        masked = text
        for m in self.masks:
            masked = m.sub("_", masked)
        found = []
        for spec, rx in self.rules:
            if spec.get("sections") and section not in spec["sections"]:
                continue
            target = masked if spec.get("masked") else text
            kind = spec["kind"]
            if kind == "regex" and rx is not None:
                hit = rx.search(target) is not None
            elif kind == "digit_groups" and rx is not None:
                hit = any(
                    len(self._digits(m.group())) >= spec["min_digits"] for m in rx.finditer(target)
                )
            elif kind == "luhn":
                hit = self._luhn_hit(spec, target)
            else:
                hit = True
            if hit:
                found.append(spec["id"])
        return found

    @classmethod
    def _spaced(cls, s: str) -> str:
        return re.sub(r"[^0-9a-z]+", " ", cls.normalize(s).casefold()).strip()

    @classmethod
    def _squashed(cls, s: str) -> str:
        return re.sub(r"[^0-9a-z]+", "", cls.normalize(s).casefold())

    def private_hits(self, text: str) -> list[str]:
        found = []
        spaced, squashed = self._spaced(text), self._squashed(text)
        for name in ("personal_subscriptions", "employer_terms"):
            for term in self.private.get(name, []):
                t_sp, t_sq = self._spaced(term), self._squashed(term)
                long_enough = len(t_sq) >= self.min_squashed
                if (t_sp and t_sp in spaced) or (long_enough and t_sq in squashed):
                    found.append(self.private_ids[name])
                    break
        return found

    def scan(self, text: str, section: str | None = None) -> list[str]:
        return self.detect(text, section) + self.private_hits(text)


# --------------------------------------------------------------------------
# A small JSON Schema validator for the subset that gen_schema.py writes
# --------------------------------------------------------------------------


class SchemaWalker:
    """Validates an instance against import-v1.schema.json.

    Returns (errors, texts): errors are (rule, loc), texts are (loc, string)
    for every string the redaction scan must check ("x-scan" not false).
    """

    def __init__(self, root: dict) -> None:
        self.defs = root["$defs"]

    def run(self, inst: object, def_name: str, loc: str) -> tuple[list, list]:
        errors: list = []
        texts: list = []
        self._v(inst, {"$ref": f"#/$defs/{def_name}"}, loc, errors, texts)
        return errors, texts

    @staticmethod
    def _join(loc: str, name: str) -> str:
        return f"{loc}.{name}" if loc and not loc.endswith((":", ".")) else f"{loc}{name}"

    def _v(self, inst, node, loc, errors, texts) -> None:
        if "$ref" in node:
            node = self.defs[node["$ref"].rsplit("/", 1)[1]]
        branches = node.get("anyOf") or node.get("oneOf")
        if branches:
            best = None
            for b in branches:
                e: list = []
                t: list = []
                self._v(inst, b, loc, e, t)
                if not e:
                    best = (e, t)
                    break
                if best is None or len(e) < len(best[0]):
                    best = (e, t)
            errors += best[0]
            texts += best[1]
            return
        rule = node.get("x-rule", "schema")
        typ = node.get("type")
        if "const" in node and inst != node["const"]:
            errors.append(("schema", loc))
            return
        if "enum" in node and inst not in node["enum"]:
            errors.append(("schema", loc))
            return
        if typ == "object":
            if not isinstance(inst, dict):
                errors.append(("schema", loc))
                return
            props = node.get("properties", {})
            for name in node.get("required", []):
                if name not in inst:
                    errors.append(("schema", self._join(loc, name)))
            for name, value in inst.items():
                if name in props:
                    self._v(value, props[name], self._join(loc, name), errors, texts)
                elif node.get("additionalProperties") is False:
                    errors.append(("schema", self._join(loc, name)))
        elif typ == "array":
            if not isinstance(inst, list):
                errors.append(("schema", loc))
                return
            if "maxItems" in node and len(inst) > node["maxItems"]:
                errors.append(("schema", loc))
            if "minItems" in node and len(inst) < node["minItems"]:
                errors.append(("schema", loc))
            for i, item in enumerate(inst):
                self._v(item, node.get("items", {}), f"{loc}[{i}]", errors, texts)
        elif typ == "string":
            if not isinstance(inst, str):
                errors.append(("schema", loc))
                return
            ok = True
            if "pattern" in node and re.fullmatch(node["pattern"], inst) is None:
                ok = False
            if "maxLength" in node and len(inst) > node["maxLength"]:
                ok = False
            if "minLength" in node and len(inst) < node["minLength"]:
                ok = False
            if ok and inst:
                if rule == "date-format":
                    ok = real_date(inst)
                elif rule == "timestamp-format":
                    ok = real_timestamp(inst)
                elif rule == "cusip-format":
                    ok = cusip_valid(inst)
                elif rule == "run-id":
                    ok = real_date(inst[:10])
            if not ok:
                errors.append((rule, loc))
            elif node.get("x-sign") == "nonnegative" and inst.startswith("-"):
                errors.append(("money-sign", loc))
            elif node.get("x-scan", True):
                texts.append((loc, inst))
        elif typ == "integer":
            if not isinstance(inst, int) or isinstance(inst, bool):
                errors.append(("schema", loc))
                return
            if "minimum" in node and inst < node["minimum"]:
                errors.append(("schema", loc))
            if "maximum" in node and inst > node["maximum"]:
                errors.append(("schema", loc))
        elif typ == "boolean":
            if not isinstance(inst, bool):
                errors.append(("schema", loc))
        elif typ == "null":
            if inst is not None:
                errors.append(("schema", loc))


# --------------------------------------------------------------------------
# The checker
# --------------------------------------------------------------------------


class Checker:
    def __init__(
        self, folder: Path, *, pre: bool, schema_dir: Path, private: dict | None, today: date
    ) -> None:
        self.folder = folder
        self.pre = pre
        self.today = today
        self.schema = json.loads((schema_dir / "import-v1.schema.json").read_text(encoding="utf-8"))
        self.walker = SchemaWalker(self.schema)
        self.red = Redaction(schema_dir / "redaction.json", private)
        cats = json.loads((schema_dir / "categories.json").read_text(encoding="utf-8"))
        self.category_kind = {c["key"]: c["kind"] for c in cats["categories"]}
        self.issues: set[tuple[str, str]] = set()
        self.warnings: set[tuple[str, str]] = set()
        self.known_accounts: set[str] = set()
        self.people: set[str] = set()
        self.by_payments: set[str] = set()

    # ---- reporting ----

    def add(self, rule: str, where: str) -> None:
        # never print a location that itself holds sensitive text
        if self.red.detect(where, section_of(where)):
            where = where.split(":", 1)[0] + ":(hidden name)" if ":" in where else "(hidden name)"
        self.issues.add((rule, where))

    def warn(self, rule: str, where: str) -> None:
        self.warnings.add((rule, where))

    def scan_texts(self, texts: list) -> None:
        for loc, text in texts:
            for rule in self.red.scan(text, section_of(loc)):
                self.add(rule, loc)

    # ---- files ----

    def read_bytes_checked(self, rel: str) -> bytes | None:
        path = self.folder / rel
        if path.is_symlink():
            self.add("symlink", rel)
            return None
        if not path.is_file():
            return None
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(self.folder) + os.sep):
            self.add("symlink", rel)
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            self.add("file-too-large", rel)
            return None
        return path.read_bytes()

    def text_of(self, data: bytes, rel: str) -> str | None:
        if data.startswith(b"\xef\xbb\xbf"):
            self.add("encoding", rel)
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.add("encoding", rel)
            return None
        if "\r" in text or not text.endswith("\n"):
            self.add("line-endings", rel)
        return text

    def load_json(self, rel: str) -> dict | None:
        data = self.read_bytes_checked(rel)
        if data is None:
            return None
        text = self.text_of(data, rel)
        if text is None:
            return None
        try:
            obj = json.loads(text)
        except ValueError:
            self.add("json", rel)
            return None
        if not isinstance(obj, dict):
            self.add("json", rel)
            return None
        return obj

    def check_version(self, obj: dict, rel: str) -> bool:
        v = obj.get("schema_version")
        known = self.schema["x-required-sections"]
        if not isinstance(v, str) or not VERSION_RE.fullmatch(v):
            self.add("version-format", f"{rel}:schema_version")
            return False
        majors = {k.split(".")[0] for k in known}
        if v.split(".")[0].lstrip("0") not in {m.lstrip("0") for m in majors}:
            self.add("version-major", f"{rel}:schema_version")
            return False
        if v not in known:
            self.add("version-minor", f"{rel}:schema_version")
            return False
        return True

    def list_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.folder, followlinks=False):
            base = Path(dirpath)
            for d in list(dirnames):
                if (base / d).is_symlink():
                    self.add("symlink", (base / d).relative_to(self.folder).as_posix())
                    dirnames.remove(d)
            for name in filenames:
                out.append((base / name).relative_to(self.folder).as_posix())
        return sorted(out)

    # ---- run ----

    def run(self) -> list[tuple[str, str]]:
        run_id = self.folder.name
        parsed = parse_run_id(run_id)
        if parsed is None:
            self.add("run-id", "(folder name)")
        elif parsed[0] > self.today + timedelta(days=MAX_DAYS_AHEAD):
            self.add("run-id-future", "(folder name)")

        files = self.list_files()
        for rel in files:
            if (self.folder / rel).is_symlink():
                self.add("symlink", rel)

        manifest = None if self.pre else self.check_manifest(files, run_id)
        if self.pre:
            for rel in files:
                if rel in ("snapshot.json", "manifest.json"):
                    continue
                if not ALLOWED_PATH_RE.fullmatch(rel):
                    self.add("path-not-allowed", rel)

        snap = None
        if "snapshot.json" not in files:
            self.add("file-missing", "snapshot.json")
        else:
            snap = self.check_snapshot(run_id)
        if manifest is not None and snap is not None:
            if manifest.get("mode") != snap.get("mode"):
                self.add("mode-mismatch", "snapshot.json:mode")
            if manifest.get("schema_version") != snap.get("schema_version"):
                self.add("schema", "snapshot.json:schema_version")

        csvs = {}
        for rel in files:
            if ALLOWED_PATH_RE.fullmatch(rel) and not (self.folder / rel).is_symlink():
                rows = self.check_csv(rel)
                if rows is not None:
                    csvs[rel] = rows
        if snap is not None:
            self.semantic(snap, csvs, run_id)
        return sorted(self.issues)

    def check_manifest(self, files: list[str], run_id: str) -> dict | None:
        rel = "manifest.json"
        if rel not in files:
            self.add("manifest-missing", rel)
            return None
        obj = self.load_json(rel)
        if obj is None or not self.check_version(obj, rel):
            return None
        errors, texts = self.walker.run(obj, "Manifest", f"{rel}:")
        for rule, loc in errors:
            self.add(rule, loc)
        self.scan_texts(texts)
        if errors:
            return None
        if obj["run_id"] != run_id:
            self.add("run-id-mismatch", f"{rel}:run_id")
        listed = {}
        for i, f in enumerate(obj["files"]):
            where = f"{rel}:files[{i}]"
            if f["path"] in listed:
                self.add("key-duplicate", where)
            listed[f["path"]] = f
            if f["path"].endswith(".csv") != ("rows" in f and f["rows"] is not None):
                self.add("manifest-rows", where)
        if "snapshot.json" not in listed:
            self.add("file-missing", f"{rel}:files")
        for rel_file in files:
            if rel_file != rel and rel_file not in listed:
                self.add("file-unlisted", rel_file)
        for path, f in listed.items():
            target = self.folder / path
            if target.is_symlink():
                self.add("symlink", path)
                continue
            if not target.is_file():
                self.add("file-missing", path)
                continue
            data = self.read_bytes_checked(path)
            if data is None:
                continue
            if len(data) != f["bytes"]:
                self.add("size-mismatch", path)
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                self.add("hash-mismatch", path)
            if f.get("rows") is not None:
                try:
                    reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
                    n = sum(1 for _ in reader) - 1
                except (UnicodeDecodeError, csv.Error):
                    n = -1
                if n != f["rows"]:
                    self.add("rows-mismatch", path)
        return obj

    def check_snapshot(self, run_id: str) -> dict | None:
        rel = "snapshot.json"
        obj = self.load_json(rel)
        if obj is None or not self.check_version(obj, rel):
            return None
        errors, texts = self.walker.run(obj, "Snapshot", f"{rel}:")
        for rule, loc in errors:
            self.add(rule, loc)
        self.scan_texts(texts)
        if errors:
            return None
        if obj["run_id"] != run_id:
            self.add("run-id-mismatch", f"{rel}:run_id")
        return obj

    def check_csv(self, rel: str) -> list[dict] | None:
        data = self.read_bytes_checked(rel)
        if data is None:
            return None
        text = self.text_of(data, rel)
        if text is None:
            return None
        header_spec = self.schema["x-csv-header"]
        try:
            all_rows = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error:
            self.add("csv-format", rel)
            return None
        if not all_rows or all_rows[0] != header_spec:
            self.add("csv-header", rel)
            return None
        rows = []
        ok = True
        for n, cells in enumerate(all_rows[1:], start=1):
            if len(cells) != len(header_spec):
                self.add("csv-format", f"{rel}:row {n}")
                ok = False
                continue
            rec = dict(zip(header_spec, cells, strict=True))
            errors, texts = self.walker.run(rec, "CsvRow", f"{rel}:row {n}.")
            for rule, loc in errors:
                self.add(rule, loc)
            self.scan_texts(texts)
            if errors:
                ok = False
            rows.append(rec)
        if not ok:
            return None
        if not rows:
            self.add("csv-empty", rel)
            return None
        keys = [self.csv_sort_key(r, header_spec) for r in rows]
        for i in range(1, len(keys)):
            if keys[i] < keys[i - 1]:
                self.add("csv-sort", f"{rel}:row {i + 1}")
                break
        return rows

    @staticmethod
    def csv_sort_key(r: dict, header: list[str]) -> tuple:
        rest = tuple(r[f] for f in header if f not in ("txn_date", "amount", "description"))
        return (r["txn_date"], to_cents(r["amount"]), r["description"]) + rest

    # ---- earlier runs (registry) ----

    def earlier_registry(self, run_id: str) -> tuple[dict, set, dict, set]:
        """Account types, person keys, tracked_by values and trip keys (sent, not
        as tombstones) from earlier run folders."""
        accounts: dict = {}
        people: set = set()
        tracked: dict = {}
        trips: set = set()
        me = parse_run_id(run_id)
        if me is None:
            return accounts, people, tracked, trips
        parent = self.folder.parent
        runs = []
        for d in parent.iterdir():
            p = parse_run_id(d.name)
            if p is not None and p < me and (d / "snapshot.json").is_file():
                runs.append((p, d))
        for _, d in sorted(runs):
            try:
                obj = json.loads((d / "snapshot.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            for a in obj.get("accounts") or []:
                if isinstance(a, dict) and isinstance(a.get("key"), str):
                    accounts[a["key"]] = a.get("type")
                    if "tracked_by" in a:
                        tracked[a["key"]] = a["tracked_by"] or "statements"
            for a in obj.get("new_accounts") or []:
                if isinstance(a, dict) and isinstance(a.get("key"), str):
                    accounts.setdefault(a["key"], None)
            for p in obj.get("people") or []:
                if isinstance(p, dict) and isinstance(p.get("key"), str):
                    people.add(p["key"])
            for t in obj.get("trips") or []:
                if isinstance(t, dict) and isinstance(t.get("key"), str) and not t.get("deleted"):
                    trips.add(t["key"])
        return accounts, people, tracked, trips

    # ---- checks across sections and files ----

    def semantic(self, s: dict, csvs: dict, run_id: str) -> None:
        S = "snapshot.json:"
        mode = s["mode"]
        version = s["schema_version"]

        # sections per mode
        for name in self.schema["x-required-sections"][version][mode]:
            if s.get(name) is None:
                self.add("section-missing", f"{S}{name}")
        if mode in ("backfill", "correction"):  # state sections are monthly only
            for name in STATE_SECTIONS:
                if s.get(name) is not None:
                    self.add("section-not-allowed", f"{S}{name}")
            if (s.get("income") or {}).get("income_streams") is not None:
                self.add("section-not-allowed", f"{S}income.income_streams")
        old = version_tuple(version)
        for name, since in (self.schema.get("x-section-since") or {}).items():
            if name in s and old < version_tuple(since):  # present counts, even as null
                self.add("section-version", f"{S}{name}")
        # fields and values newer than the run (present counts, even as null)
        for path, since in (self.schema.get("x-field-since") or {}).items():
            if old < version_tuple(since):
                name = path.rsplit(".", 1)[1]
                for obj, loc in since_nodes(s, path):
                    if name in obj:
                        self.add("section-version", f"{loc}.{name}")
        for path, values in (self.schema.get("x-value-since") or {}).items():
            name = path.rsplit(".", 1)[1]
            for obj, loc in since_nodes(s, path):
                value = obj.get(name)
                since = values.get(value) if isinstance(value, str) else None
                if since is not None and old < version_tuple(since):
                    self.add("section-version", f"{loc}.{name}")

        # identity: no duplicates, sorted ('<section>.<list>' is a list inside a section)
        for name, fields in IDENTITY.items():
            if "." in name:
                section, inner = name.split(".", 1)
                items = (s.get(section) or {}).get(inner)
            else:
                items = s.get(name)
            if not items:
                continue
            ids = [tuple(it.get(f) for f in fields) for it in items]
            for i in range(1, len(ids)):
                if ids[i] in ids[:i]:
                    self.add("key-duplicate", f"{S}{name}[{i}]")
                    break
            for i in range(1, len(ids)):
                if ids[i] < ids[i - 1]:
                    self.add("sort-order", f"{S}{name}[{i}]")
                    break
        groups = []
        if s.get("net_worth"):
            groups.append(("net_worth.parts", s["net_worth"]["parts"]))
        for name in ("cashflow_actual", "cashflow_plan"):
            for i, item in enumerate(s.get(name) or []):
                groups.append((f"{name}[{i}].by_group", item["by_group"]))
        for loc, items in groups:
            keys = [g["key"] for g in items]
            if len(set(keys)) != len(keys):
                self.add("key-duplicate", f"{S}{loc}")
            elif keys != sorted(keys):
                self.add("sort-order", f"{S}{loc}")

        # paycheck seq
        pay_groups: dict = {}
        for p in (s.get("income") or {}).get("paychecks") or []:
            if p.get("deleted") is not True:
                pay_groups.setdefault((p["person"], p["pay_date"], p["kind"]), []).append(
                    (p["seq"], to_cents(p["gross"]))
                )
        for members in pay_groups.values():
            members.sort()
            if [m[0] for m in members] != list(range(1, len(members) + 1)) or [
                m[1] for m in members
            ] != sorted(m[1] for m in members):
                self.add("paycheck-seq", f"{S}income.paychecks")
                break

        # model rules
        for i, a in enumerate(s.get("accounts") or []):
            if a["include_in_net_worth"] and a.get("nw_group") is None:
                self.add("nw-group", f"{S}accounts[{i}]")
            if a.get("tracked_by") == "payments" and a["include_in_net_worth"]:
                self.add("tracked-net-worth", f"{S}accounts[{i}]")
            # 1.4 debt-terms, on the item as sent. [] is fine on any account.
            original = a.get("original")
            if original is not None and (
                a["type"] not in DEBT_ORIGINAL_TYPES or to_cents(original) == 0
            ):
                self.add("debt-terms", f"{S}accounts[{i}]")
            changes = a.get("rate_changes") or []
            if changes and (a.get("apr") is None or a.get("rate_type") != "variable"):
                self.add("debt-terms", f"{S}accounts[{i}]")
            self.order(f"{S}accounts[{i}].rate_changes", [(c["effective_on"],) for c in changes])
        for i, st in enumerate(s.get("statements") or []):
            if st["period_start"] > st["period_end"]:
                self.add("date-order", f"{S}statements[{i}]")
            if st["reconciled"] is False and st.get("reconcile_note") is None:
                self.add("reconcile-note", f"{S}statements[{i}]")
        self.state_rules(s)
        self.utility_rules(s)
        self.rewards_trips_rules(s, run_id)
        self.goal_rules(s)

        # registry
        earlier_accounts, earlier_people, tracked, earlier_trips = self.earlier_registry(run_id)
        new_keys = {a["key"] for a in s.get("new_accounts") or []}
        this_accounts = {a["key"]: a["type"] for a in s.get("accounts") or []}
        known_accounts = set(earlier_accounts) | new_keys
        account_type = dict(earlier_accounts)
        account_type.update(this_accounts)
        for i, a in enumerate(s.get("accounts") or []):
            if a["key"] not in known_accounts:
                self.add("account-unknown", f"{S}accounts[{i}].key")
        for i, a in enumerate(s.get("new_accounts") or []):
            if a["key"] not in this_accounts:
                self.add("new-account-details", f"{S}new_accounts[{i}]")
        people = earlier_people | {p["key"] for p in s.get("people") or []}
        self.known_accounts, self.people = known_accounts, people
        for a in s.get("accounts") or []:
            if "tracked_by" in a:
                tracked[a["key"]] = a["tracked_by"] or "statements"
        self.by_payments = {k for k, v in tracked.items() if v == "payments"}

        def need_account(key: str | None, where: str) -> None:
            if key and key not in known_accounts:
                self.add("account-unknown", where)

        def need_person(key: str | None, where: str) -> None:
            if key and key not in people:
                self.add("person-unknown", where)

        for i, a in enumerate(s.get("accounts") or []):
            need_account(a.get("secured_by"), f"{S}accounts[{i}].secured_by")
            need_person(a["owner"], f"{S}accounts[{i}].owner")
        for name in ("statements", "balances", "holdings", "contributions"):
            for i, it in enumerate(s.get(name) or []):
                need_account(it["account_key"], f"{S}{name}[{i}].account_key")
        for i, c in enumerate(s.get("contributions") or []):
            need_person(c["person"], f"{S}contributions[{i}].person")
        for i, p in enumerate((s.get("income") or {}).get("paychecks") or []):
            need_person(p["person"], f"{S}income.paychecks[{i}].person")
        for i, p in enumerate((s.get("income") or {}).get("income_streams") or []):
            need_person(p["person"], f"{S}income.income_streams[{i}].person")
        for i, r in enumerate(s.get("recurring") or []):
            where = f"{S}recurring[{i}]"
            if r["category_key"] not in self.category_kind:
                self.add("category-unknown", f"{where}.category_key")
            need_account(r.get("account_key"), f"{where}.account_key")
            need_account(r.get("preferred_account_key"), f"{where}.preferred_account_key")
            need_person(r.get("owner"), f"{where}.owner")
        for i, p in enumerate(s.get("installment_plans") or []):
            need_account(p["account_key"], f"{S}installment_plans[{i}].account_key")
        for i, u in enumerate(s.get("utility_bills") or []):
            need_account(u.get("paid_from"), f"{S}utility_bills[{i}].paid_from")
        for i, f in enumerate(s.get("flags") or []):
            where = f"{S}flags[{i}].related_key"
            kind, key = f.get("related_kind"), f.get("related_key")
            if kind == "account":
                need_account(key, where)
            elif kind == "person":
                need_person(key, where)
            elif kind == "category" and key and key not in self.category_kind:
                self.add("category-unknown", where)
        for i, b in enumerate(s.get("budgets") or []):
            if b.get("category_key") and b["category_key"] not in self.category_kind:
                self.add("category-unknown", f"{S}budgets[{i}].category_key")
            scope = b.get("scope") or {}
            need_person(scope.get("owner"), f"{S}budgets[{i}].scope.owner")
            for j, card in enumerate(scope.get("cards") or []):
                need_account(card, f"{S}budgets[{i}].scope.cards[{j}]")
        for i, g in enumerate(s.get("goals") or []):
            for j, key in enumerate(g["accounts"]):
                need_account(key, f"{S}goals[{i}].accounts[{j}]")
            need_person(g.get("person"), f"{S}goals[{i}].person")

        # format 1.3: rewards and trips point at known accounts, people and categories
        rewards = s.get("rewards") or {}
        for i, p in enumerate(rewards.get("programs") or []):
            where = f"{S}rewards.programs[{i}]"
            need_person(p.get("owner"), f"{where}.owner")
            for j, card in enumerate(p.get("earned_on") or []):
                need_account(card, f"{where}.earned_on[{j}]")
            need_account(p.get("status_via"), f"{where}.status_via")
        for i, b in enumerate(rewards.get("benefits") or []):
            where = f"{S}rewards.benefits[{i}]"
            need_account(b["account_key"], f"{where}.account_key")
            for j, key in enumerate(b.get("category_keys") or []):
                if key not in self.category_kind:
                    self.add("category-unknown", f"{where}.category_keys[{j}]")
        for name in ("milestones", "routing"):
            for i, item in enumerate(rewards.get(name) or []):
                need_account(item["account_key"], f"{S}rewards.{name}[{i}].account_key")
        trips = [(i, t) for i, t in enumerate(s.get("trips") or []) if t.get("deleted") is not True]
        for i, t in trips:
            for j, person in enumerate(t.get("people") or []):
                need_person(person, f"{S}trips[{i}].people[{j}]")
            for j, item in enumerate(t["items"]):
                need_account(item.get("account_key"), f"{S}trips[{i}].items[{j}].account_key")
                need_person(item.get("person"), f"{S}trips[{i}].items[{j}].person")

        # warning: a trip_key that no run so far defines (backfill and old rows may)
        known_trips = earlier_trips | {t["key"] for _, t in trips}
        refs = []
        for i, p in enumerate(s.get("installment_plans") or []):
            for j, c in enumerate(p.get("components") or []):
                refs.append(
                    (c.get("trip_key"), f"{S}installment_plans[{i}].components[{j}].trip_key")
                )
        for i, f in enumerate(s.get("flags") or []):
            if f.get("related_kind") == "trip":
                refs.append((f.get("related_key"), f"{S}flags[{i}].related_key"))
        for i, x in enumerate(rewards.get("redemptions") or []):
            refs.append((x.get("trip_key"), f"{S}rewards.redemptions[{i}].trip_key"))
        for rel, rows in csvs.items():
            for n, r in enumerate(rows, start=1):
                refs.append((r["trip_key"], f"{rel}:row {n}.trip_key"))
        for key, where in refs:
            if key and key not in known_trips:
                self.warn("trip-unknown", where)

        # statements and their CSV files
        statements = {
            (st["account_key"], st["period_end"]): (i, st)
            for i, st in enumerate(s.get("statements") or [])
        }
        csv_by_stmt = {}
        for rel, rows in csvs.items():
            m = CSV_NAME_RE.fullmatch(rel)
            ident = (m.group("account"), m.group("end")) if m else None
            if ident not in statements:
                self.add("csv-orphan", rel)
            else:
                csv_by_stmt[ident] = (rel, rows)
        stmt_balances = {
            (b["account_key"], b["as_of"])
            for b in s.get("balances") or []
            if b.get("deleted") is not True and b["source"] == "statement"
        }
        for ident, (i, st) in statements.items():
            where = f"{S}statements[{i}]"
            if st["account_key"] not in self.by_payments and ident not in stmt_balances:
                self.add("statement-balance-missing", where)
            if st.get("has_holdings") is True and s.get("holdings") is None:
                self.add("holdings-missing", where)
            rel, rows = csv_by_stmt.get(ident, (None, None))
            if rows is None and st["txn_count"] > 0:
                if (self.folder / f"transactions/{ident[0]}-{ident[1]}.csv").exists():
                    continue  # the CSV itself failed and is already reported
                self.add("csv-missing", where)
                continue
            rows = rows or []
            if len(rows) != st["txn_count"]:
                self.add("txn-count", where)
            amounts = [to_cents(r["amount"]) for r in rows]
            total = sum(amounts)
            if st.get("txn_sum") is not None and rows and to_cents(st["txn_sum"]) != total:
                self.add("txn-sum", where)
            self.reconcile(st, account_type.get(st["account_key"]), rows, amounts, where)
            for n, r in enumerate(rows, start=1):
                self.row_checks(st, r, f"{rel}:row {n}")

        # balances and holdings that point at statements
        for i, b in enumerate(s.get("balances") or []):
            if b.get("deleted") is True or b["source"] != "statement":
                continue
            if b["account_key"] in self.by_payments:
                self.add("tracked-balance", f"{S}balances[{i}]")
                continue
            hit = statements.get((b["account_key"], b["as_of"]))
            if hit is None:
                self.add("balance-statement", f"{S}balances[{i}]")
                continue
            st = hit[1]
            expected = st.get("balance") if st.get("balance") is not None else st["closing"]
            if to_cents(b["balance"]) != to_cents(expected):
                self.add("balance-closing", f"{S}balances[{i}].balance")
        for i, h in enumerate(s.get("holdings") or []):
            hit = statements.get((h["account_key"], h["period_end"]))
            if hit is None or hit[1].get("has_holdings") is not True:
                self.add("holding-statement", f"{S}holdings[{i}]")
            if self.red.stock_plan.search(h["name"]):
                self.add(self.red.stock_plan_id, f"{S}holdings[{i}].name")

    def state_rules(self, s: dict) -> None:
        """Item rules for the 1.1 state sections and their 1.4 fields (the same as the
        app's models and snapshot checks), and the warning recurring-no-date."""
        S = "snapshot.json:"
        updated = (s.get("meta") or {}).get("updated")
        for i, r in enumerate(s.get("recurring") or []):
            where = f"{S}recurring[{i}]"
            if (r["cadence"] == "every_n_weeks") != (r.get("interval_n") is not None):
                self.add("recurring-interval", where)
            months = r.get("active_months")
            if months is not None and months != sorted(set(months)):
                self.add("active-months", where)
            if r.get("start_date") and r.get("end_date") and r["start_date"] > r["end_date"]:
                self.add("date-order", where)
            if r.get("keep") is not None and r["status"] not in ("active", "paused"):
                self.add("recurring-keep", where)
            if r.get("tag") is not None and is_personal_subscription(r["name"]):
                self.add("recurring-tag-private", f"{where}.tag")
            if r["status"] == "active" and not any(r.get(k) for k in RECURRING_DATE_FIELDS):
                self.warn("recurring-no-date", where)
        for i, p in enumerate(s.get("installment_plans") or []):
            where = f"{S}installment_plans[{i}]"
            paid_off_on = p.get("paid_off_on")
            if (
                (p["kind"] == "promo_0pct" and p.get("promo_expiry") is None)
                or (p["kind"] == "deferred_interest" and p.get("pay_before") is None)
                or (
                    paid_off_on is not None
                    and (p["status"] == "active" or paid_off_on < p["created_on"])
                )
            ):
                self.add("plan-dates", where)
            total, left = p.get("payments_total"), p.get("payments_left")
            if total is not None and left is not None and left > total:
                self.add("plan-payments", where)
            parts = p.get("components")
            if parts and sum(to_cents(c["amount"]) for c in parts) != to_cents(p["original"]):
                self.add("plan-components", where)
        for i, f in enumerate(s.get("flags") or []):
            where = f"{S}flags[{i}]"
            if (f.get("related_kind") is None) != (f.get("related_key") is None):
                self.add("flag-related", where)
            if f["status"] == "resolved" and f.get("resolved_on") is None:
                self.add("flag-resolved", where)
            if f.get("resolved_on") and f["created_on"] > f["resolved_on"]:
                self.add("date-order", where)
            updates = f.get("updates") or []
            self.order(f"{where}.updates", [(u["on"], u["text"]) for u in updates])
            for j, u in enumerate(updates):
                if u["on"] < f["created_on"] or (updated and u["on"] > updated):
                    self.add("date-order", f"{where}.updates[{j}].on")
        for i, p in enumerate(s.get("pending") or []):
            if p.get("updated_on") and p["added_on"] > p["updated_on"]:
                self.add("date-order", f"{S}pending[{i}]")

    def goal_rules(self, s: dict) -> None:
        """Goals rules (format 1.4), the same as the app's Goal model and snapshot checks:
        a college goal's person is a child and its accounts are 529 or custodial, both
        from this run's people and accounts."""
        S = "snapshot.json:"
        roles = {p["key"]: p["role"] for p in s.get("people") or []}
        types = {a["key"]: a["type"] for a in s.get("accounts") or []}
        for i, g in enumerate(s.get("goals") or []):
            where = f"{S}goals[{i}]"
            college = g["kind"] == "college"
            if (
                college
                and (
                    g.get("person") is None
                    or g.get("start_year") is None
                    or g.get("target_date") is not None
                )
            ) or (not college and g.get("start_year") is not None):
                self.add("goal-fields", where)
            elif college and (
                roles.get(g["person"]) != "child"
                or any(types.get(k) not in COLLEGE_ACCOUNT_TYPES for k in g["accounts"])
            ):
                self.add("goal-fields", where)
            self.order(f"{where}.accounts", [(k,) for k in g["accounts"]])

    def utility_rules(self, s: dict) -> None:
        """Utility bill rules (format 1.2), the same as the app's models and warnings."""
        S = "snapshot.json:"
        last_end: dict = {}
        for i, b in enumerate(s.get("utility_bills") or []):
            if b.get("deleted") is True:
                continue
            where = f"{S}utility_bills[{i}]"
            for j, ln in enumerate(b["lines"]):
                at = f"{where}.lines[{j}]"
                if ln["unit"] not in UTILITY_UNITS[ln["service"]]:
                    self.add("utility-unit", at)
                fixed = ln.get("fixed_charges")
                if fixed is not None and to_cents(fixed) > to_cents(ln["charges"]):
                    self.add("utility-fixed", at)
            for j, c in enumerate(b.get("credits") or []):
                if to_cents(c["amount"]) >= 0:
                    self.add("utility-credit-sign", f"{where}.credits[{j}]")
            dates = (b["bill_date"], b["period_start"], b["period_end"], b.get("due_date"))
            if any(d is not None and d < UTILITY_EARLIEST for d in dates):
                self.add("utility-date", where)
            if b["period_start"] >= b["period_end"]:
                self.add("utility-period", where)
            charges = sum(to_cents(ln["charges"]) for ln in b["lines"])
            credits = sum(to_cents(c["amount"]) for c in b.get("credits") or [])
            off = abs(charges + credits - to_cents(b["total"]))
            if off > UTILITY_ROUNDING_CENTS:
                self.add("utility-total", where)
            elif off:
                self.warn("utility-total-rounding", where)
            if b["period_start"] < b["period_end"]:
                span = date.fromisoformat(b["period_end"]) - date.fromisoformat(b["period_start"])
                if span.days != b["days"]:
                    self.warn("utility-days", where)
            before = last_end.get(b["utility_key"])
            if before is not None and b["period_start"] < before:
                self.warn("utility-overlap", where)
            last_end[b["utility_key"]] = b["period_end"]
            lists = [("lines", [(ln["service"],) for ln in b["lines"]])]
            if b.get("credits"):
                lists.append(
                    ("credits", [(c["label"], to_cents(c["amount"])) for c in b["credits"]])
                )
            for name, ids in lists:
                if len(set(ids)) != len(ids):
                    self.add("key-duplicate", f"{where}.{name}")
                elif ids != sorted(ids):
                    self.add("sort-order", f"{where}.{name}")

    def rewards_trips_rules(self, s: dict, run_id: str) -> None:
        """Rewards and trips rules (format 1.3), the same as the app's models and warnings."""
        S = "snapshot.json:"
        rewards = s.get("rewards")
        if rewards is not None:
            self.rewards_rules(rewards, run_id)
        for i, t in enumerate(s.get("trips") or []):
            if t.get("deleted") is not True:
                self.trip_rules(t, f"{S}trips[{i}]")

    def order(self, where: str, ids: list) -> None:
        if len(set(ids)) != len(ids):
            self.add("key-duplicate", where)
        elif ids != sorted(ids):
            self.add("sort-order", where)

    def rewards_rules(self, r: dict, run_id: str) -> None:
        S = "snapshot.json:rewards."
        parsed = parse_run_id(run_id)
        stale = (parsed[0] - timedelta(days=REWARD_STALE_DAYS)).isoformat() if parsed else ""
        programs = {p["key"] for p in r["programs"]}
        for i, p in enumerate(r["programs"]):
            where = f"{S}programs[{i}]"
            if not together(p.get("balance"), p.get("as_of"), p.get("source")):
                self.add("reward-as-of", where)
            low, high = p.get("cpp_low"), p.get("cpp_high")
            if not together(low, high):
                self.add("reward-value-range", where)
            elif low is not None and (p["kind"] == "cash_back" or to_micro(low) > to_micro(high)):
                self.add("reward-value-range", where)
            allocations = p.get("allocations")
            if allocations is not None and p["kind"] != "timeshare":
                self.add("reward-allocation", where)
            for j, a in enumerate(allocations or []):
                spent = to_micro(a["used"]) + to_micro(a.get("pulled_forward") or "0")
                if spent > to_micro(a["allocated"]):
                    self.add("reward-allocation", f"{where}.allocations[{j}]")
            self.order(f"{where}.earned_on", [(k,) for k in p.get("earned_on") or []])
            self.order(f"{where}.allocations", [(a["year"],) for a in allocations or []])
        for i, b in enumerate(r["benefits"]):
            where = f"{S}benefits[{i}]"
            own = BENEFIT_FIELDS[b["kind"]]
            other = set().union(*BENEFIT_FIELDS.values()) - own
            if any(b.get(f) is not None for f in other) or any(
                b.get(f) is None for f in BENEFIT_REQUIRED[b["kind"]]
            ):
                self.add("reward-benefit-fields", where)
            if not together(b.get("reported_spent"), b.get("as_of")):
                self.add("reward-as-of", where)
            for j, c in enumerate(b.get("cycles") or []):
                if c["start"] > c["end"]:
                    self.add("date-order", f"{where}.cycles[{j}]")
            self.order(f"{where}.cycles", [(c["start"],) for c in b.get("cycles") or []])
            self.order(f"{where}.category_keys", [(k,) for k in b.get("category_keys") or []])
            if b.get("program_key") is not None and b["program_key"] not in programs:
                self.add("reward-program-unknown", f"{where}.program_key")
        for i, ms in enumerate(r["milestones"]):
            if not together(ms.get("reported_spent"), ms.get("as_of")):
                self.add("reward-as-of", f"{S}milestones[{i}]")
        for i, x in enumerate(r["redemptions"]):
            if x["program_key"] not in programs:
                self.add("reward-program-unknown", f"{S}redemptions[{i}].program_key")
            if x.get("done_on") is not None and x["status"] != "done":
                self.add("reward-done-on", f"{S}redemptions[{i}]")
        for name in ("programs", "benefits", "milestones"):
            for i, item in enumerate(r[name]):
                if item.get("as_of") and item["as_of"] < stale:
                    self.warn("reward-stale", f"{S}{name}[{i}].as_of")

    def trip_rules(self, t: dict, where: str) -> None:
        start, end, month = t.get("start_date"), t.get("end_date"), t.get("month")
        ranged = start is not None and end is not None
        if (
            not together(start, end)
            or ranged == (month is not None)
            or any(d < TRIP_EARLIEST for d in (start, end) if d is not None)
            or (month is not None and month < TRIP_EARLIEST[:7])
        ):
            self.add("trip-dates", where)
        if ranged and start > end:
            self.add("date-order", where)
        low, high = t.get("estimate_low"), t.get("estimate_high")
        if not together(low, high) or (low is not None and to_cents(low) > to_cents(high)):
            self.add("trip-estimate", where)
        for j, it in enumerate(t["items"]):
            at = f"{where}.items[{j}]"
            amount, low, high = it.get("amount"), it.get("estimate_low"), it.get("estimate_high")
            status = it["status"]
            if status in ("posted", "booked", "excluded"):
                ok = amount is not None
            elif status == "estimate":
                ok = amount is None and low is not None and high is not None
            else:
                ok = amount is None and low is None and high is None
            if not ok:
                self.add("trip-item-amount", at)
            if not together(low, high) or (low is not None and to_cents(low) > to_cents(high)):
                self.add("trip-estimate", at)
            tender = it["tender"]
            if (
                tender == "points" and (it.get("points") is None or it.get("program_key") is None)
            ) or (tender == "points_credit" and amount is None):
                self.add("trip-tender", at)
            if it.get("due_on") is not None and status != "booked":
                self.add("trip-due", at)
            if it.get("maybe") is not None and status not in ("estimate", "tbd"):
                self.add("trip-maybe", at)
        items = {it["key"] for it in t["items"]}
        for j, o in enumerate(t.get("ious") or []):
            at = f"{where}.ious[{j}]"
            if (o["status"] == "settled") != (o.get("settled_on") is not None):
                self.add("trip-iou-settled", at)
            if o.get("item_key") is not None and o["item_key"] not in items:
                self.add("trip-item-unknown", f"{at}.item_key")
        self.order(f"{where}.people", [(k,) for k in t.get("people") or []])
        self.order(f"{where}.items", [(it["key"],) for it in t["items"]])
        self.order(f"{where}.ious", [(o["key"],) for o in t.get("ious") or []])

    def reconcile(
        self, st: dict, acct_type: str | None, rows: list, amounts: list[int], where: str
    ) -> None:
        if st["reconciled"] is False:
            return
        if st.get("opening") is None:
            if rows:
                self.add("statement-opening", where)
            return
        opening, closing = to_cents(st["opening"]), to_cents(st["closing"])

        def c(name: str) -> int | None:
            return to_cents(st[name]) if st.get(name) is not None else None

        if acct_type in CASH_LIKE_TYPES:
            if rows:
                if opening + sum(amounts) != closing:
                    self.add("statement-unbalanced", where)
                if c("credits") is not None and c("credits") != sum(a for a in amounts if a > 0):
                    self.add("statement-credits", where)
                if c("debits") is not None and c("debits") != sum(a for a in amounts if a < 0):
                    self.add("statement-debits", where)
            elif opening + (c("credits") or 0) + (c("debits") or 0) != closing:
                self.add("statement-unbalanced", where)
        elif acct_type in INVESTMENT_TYPES:
            flows, market = c("net_flows"), c("market_change")
            if flows is None or market is None or opening + flows + market != closing:
                self.add("statement-unbalanced", where)

    def row_checks(self, st: dict, r: dict, where: str) -> None:
        start, end = st["period_start"], st["period_end"]
        if not r["plan_key"]:
            if not (add_days(start, -TXN_DAYS_BEFORE) <= r["txn_date"] <= end):
                self.add("date-window", f"{where}.txn_date")
        if r["post_date"] and not (start <= r["post_date"] <= add_days(end, POST_DAYS_AFTER)):
            self.add("date-window", f"{where}.post_date")
        kind = self.category_kind.get(r["category_key"])
        if kind is None:
            self.add("category-unknown", f"{where}.category_key")
        elif r["txn_type"] == "refund" and kind == "income":
            self.add("refund-income", f"{where}.category_key")
        counterparty = r["counterparty_account_key"]
        if counterparty and counterparty not in self.known_accounts:
            self.add("account-unknown", f"{where}.counterparty_account_key")
        if r["cardholder"] and r["cardholder"] not in self.people:
            self.add("person-unknown", f"{where}.cardholder")
        if counterparty in self.by_payments and r["is_transfer"] != "true":
            if r["txn_type"] != "payment" or kind != "spending":
                self.add("tracked-payment", f"{where}.is_transfer")


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def load_private(path: str | None) -> dict | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("private rules file must be a JSON object")
    return {k: list(data.get(k) or []) for k in ("personal_subscriptions", "employer_terms")}


def check_run_full(
    folder: str | os.PathLike,
    *,
    pre: bool = False,
    private_rules: str | None = None,
    today: date | None = None,
    schema_dir: str | os.PathLike | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Check one run folder. Returns (problems, warnings), each sorted (rule_id, location)
    pairs. No problems means OK. Warnings never fail a run."""
    checker = Checker(
        Path(folder),
        pre=pre,
        schema_dir=Path(schema_dir) if schema_dir else HERE,
        private=load_private(private_rules),
        today=today or date.today(),
    )
    issues = checker.run()
    return issues, sorted(checker.warnings)


def check_run(
    folder: str | os.PathLike,
    *,
    pre: bool = False,
    private_rules: str | None = None,
    today: date | None = None,
    schema_dir: str | os.PathLike | None = None,
) -> list[tuple[str, str]]:
    """Check one run folder. Returns sorted (rule_id, location) pairs. Empty means OK."""
    return check_run_full(
        folder, pre=pre, private_rules=private_rules, today=today, schema_dir=schema_dir
    )[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check an import run folder (format v1).")
    ap.add_argument("folder")
    ap.add_argument("--pre", action="store_true", help="before manifest.json is written")
    ap.add_argument("--private-rules", help="path to a private_rules.json file")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--schema-dir", help="folder with the schema files (default: this folder)")
    args = ap.parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir():
        print("FAILED: run folder not found")
        return 1
    try:
        issues, warnings = check_run_full(
            folder,
            pre=args.pre,
            private_rules=args.private_rules,
            today=date.fromisoformat(args.today) if args.today else None,
            schema_dir=args.schema_dir,
        )
    except (OSError, ValueError, KeyError):
        print("FAILED: the checker could not read its schema or private rules files")
        return 1
    if args.json:
        out = [{"rule": r, "where": w} for r, w in issues]
        out += [{"level": "warning", "rule": r, "where": w} for r, w in warnings]
        print(json.dumps(out, indent=2))
    else:
        for rule, where in issues:
            print(f"{rule}  {where}")
        for rule, where in warnings:
            print(f"{rule}  {where}  (warning)")
        print(f"FAILED: {len(issues)} problem(s)" if issues else "OK")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
