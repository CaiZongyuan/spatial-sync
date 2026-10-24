"""Compare a Task obs_spec with independently exported trainer states (no physics startup)."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.library.common import document
from packages.policies.consistency import compare_observations
from packages.policies.observations import ObservationSpec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument(
        "samples", type=Path, help="JSON array: state/trainingObservation/previousAction/references"
    )
    args = parser.parse_args()
    try:
        spec = ObservationSpec.model_validate(document(args.spec))
        samples = json.loads(args.samples.read_bytes())
        result = compare_observations(spec, samples)
        result["specSha256"] = hashlib.sha256(args.spec.read_bytes()).hexdigest()
        result["samplesSha256"] = hashlib.sha256(args.samples.read_bytes()).hexdigest()
        print(json.dumps(result, allow_nan=False))
        return 0 if result["numericalPass"] else 1
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
