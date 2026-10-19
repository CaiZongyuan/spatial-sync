"""Validate shipped skill command examples without connecting to a laboratory."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "cli"))

from lw.skill_commands import check_skills


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument(
        "directory", type=Path, nargs="?", default=Path(__file__).resolve().parents[1] / "skills"
    )
    try:
        print(json.dumps(check_skills(arguments.parse_args().directory)))
        return 0
    except (ValueError, OSError) as error:
        print(f"Invalid platform skills: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
