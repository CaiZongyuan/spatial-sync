"""Export the public validation document contract; --check verifies the committed artifact."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.library.reports import ValidationReport


def schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **ValidationReport.model_json_schema(by_alias=True),
    }


if __name__ == "__main__":
    path = ROOT / "packages/library/schemas/validation.schema.json"
    content = json.dumps(schema(), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if "--check" in sys.argv:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise SystemExit("Validation schema differs; run python tools/export_validation_schema.py")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
