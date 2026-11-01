"""Write schema/import-v1.schema.json from the Pydantic models.

Run from the repo or backend folder:
    cd backend && uv run python ../scripts/gen_schema.py [--check]

--check exits 1 if the committed file differs (used by tests and check.sh).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic.json_schema import models_json_schema  # noqa: E402

from app.importer.models import (  # noqa: E402
    CSV_HEADER,
    CURRENT_VERSION,
    FIELD_SINCE,
    REQUIRED_SECTIONS,
    SECTION_SINCE,
    VALUE_SINCE,
    CsvRow,
    Manifest,
    Snapshot,
)

OUT = ROOT / "schema" / "import-v1.schema.json"


def build_schema() -> str:
    """Return the schema file text (deterministic)."""
    _, top = models_json_schema(
        [(Manifest, "validation"), (Snapshot, "validation"), (CsvRow, "validation")],
        ref_template="#/$defs/{model}",
    )
    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "import-v1.schema.json",
        "title": "Budget app import format v1",
        "description": (
            "Generated from backend/app/importer/models.py by scripts/gen_schema.py. "
            "Do not edit. See schema/README.md."
        ),
        "x-schema-version": CURRENT_VERSION,
        "x-documents": {
            "manifest.json": {"$ref": "#/$defs/Manifest"},
            "snapshot.json": {"$ref": "#/$defs/Snapshot"},
            "transactions/*.csv row": {"$ref": "#/$defs/CsvRow"},
        },
        "x-csv-header": list(CSV_HEADER),
        "x-required-sections": {
            v: {m: list(s) for m, s in modes.items()} for v, modes in REQUIRED_SECTIONS.items()
        },
        "x-section-since": dict(SECTION_SINCE),
        "x-field-since": dict(FIELD_SINCE),
        "x-value-since": {path: dict(values) for path, values in VALUE_SINCE.items()},
        "$defs": top["$defs"],
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = build_schema()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("schema/import-v1.schema.json is out of date. Run scripts/gen_schema.py.")
            return 1
        print("schema/import-v1.schema.json is up to date.")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
