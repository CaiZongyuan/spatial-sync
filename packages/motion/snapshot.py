"""Verification shared by the Task library, the Node and Policy packaging."""

import hashlib
import json

from packages.library.archive import package_files

REFERENCE_FILE = "reference-motion.json"
MAX_REFERENCE_BYTES = 8 * 1024 * 1024


def reference_snapshot(task, directory):
    pin = task.get("referenceMotionRef")
    if not pin:
        return None
    with (directory / REFERENCE_FILE).open("rb") as stream:
        raw = stream.read(MAX_REFERENCE_BYTES + 1)
    if len(raw) > MAX_REFERENCE_BYTES or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise ValueError("Reference Motion snapshot differs from the declared digest")
    doc = json.loads(raw)
    if doc["schema"] != "lw.reference-motion/1" or doc["motion"] != task["referenceMotion"]:
        raise ValueError("Task reference frames differ from the verified motion")
    return raw


def declarative_files(task, directory):
    expected = {"task.yaml", task["observation"]}
    if task.get("referenceMotionRef"):
        expected.add(REFERENCE_FILE)
    if set(package_files(directory)) != expected:
        raise ValueError("Declarative Task input contains unapproved files")
    return reference_snapshot(task, directory)
