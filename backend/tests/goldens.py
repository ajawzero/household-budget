"""Golden digests of API bodies, and the fields added after a golden was taken.

A golden is the sha256 of the canonical JSON (sorted keys, no spaces). A later chunk
that adds fields never recaptures it: it lists each new field with the value it must
have on the old data, and `older` checks that value and takes the field out before
hashing (docs/plans/format-14.md section 8)."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

_MISSING = object()


def digest(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _holders(body: object, path: str, parts: list[str]) -> list[dict]:
    """Each object a path's parent parts lead to. Lists are walked item by item. A part no
    object at its level has fails, so a typo in a parent cannot pass unseen."""
    nodes = [body] if isinstance(body, dict) else []
    for part in parts:
        assert not nodes or any(part in n for n in nodes), f"{path}: not in the body"
        found: list[dict] = []
        for node in nodes:
            value = node.get(part)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    found.append(item)
        nodes = found
    return nodes


def older(body: object, fields: Mapping[str, object]) -> object:
    """A copy of `body` without `fields`: {dotted path: its value on old data}. A path
    walks lists item by item ("flags.updates", "cards.plans.note"). Each value taken out
    must equal its old value. A path whose objects exist but lack the field fails, so a
    typo or a rename cannot pass unseen. No objects (an empty list): nothing to check."""
    out = copy.deepcopy(body)
    for path, old in fields.items():
        *parents, name = path.split(".")
        holders = _holders(out, path, parents)
        assert not holders or all(name in h for h in holders), f"{path}: not in the body"
        for holder in holders:
            value = holder.pop(name, _MISSING)
            assert value == old, f"{path} is {value!r} on old data, not {old!r}"
    return out


# Fields E-b (the format 1.4 screens) adds, by URL path (query left out), with their value
# on a 1.3 run. The 1.3 goldens (test_state_v14) and the R1 goldens take them out. Each
# E-b step adds its own paths in the commit that adds the fields.
E14_FIELDS: dict[str, dict[str, object]] = {
    "/api/flags": {"flags.action": "act", "flags.updates": [], "counts.info": 0},
    "/api/recurring": {
        "items.keep": None,
        "items.tag": None,
        "sections.group_key": None,
        "sections.group_label": None,
    },
    "/api/plans": {
        "cards.plans.note": None,
        "cards.plans.paid_off_on": None,
        "cards.plans.paid_off_early": False,
        "cards.plans.refund_cents": 0,
    },
    "/api/cashflow/plan": {"events.status": None},
    "/api/dashboard": {
        "next_changes.status": None,
        "upcoming.trip_key": None,
        "upcoming.trip_name": None,
        "net_worth.behind": [],  # Investments feature, Oct 29: nothing is behind there
    },
    "/api/rewards": {"programs.last_redeemed_on": None, "redemptions.done_on": None},
    "/api/trips": {
        "active.items.due_on": None,
        "active.items.maybe": False,
        "completed.items.due_on": None,
        "completed.items.maybe": False,
        "older.items.due_on": None,
        "older.items.maybe": False,
        "open_items.items.due_on": None,
        "open_items.items.maybe": False,
        "open_items.due": [],
    },
    "/api/debt": {
        "ledger.original_cents": None,
        "ledger.paid_down_pct": None,
        "ledger.rate_changes": [],
    },
}
