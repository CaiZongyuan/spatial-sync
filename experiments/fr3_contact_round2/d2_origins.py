"""Standard-library checks of loaded module origins, performed inside the billed Job.

Disk pins alone are insufficient: old same-named Task modules must be rejected
even when their bytes match. This module imports no Torch, Isaac or simulator.
"""

import hashlib
import inspect
import json
import os
from pathlib import Path
import sys


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity():
    return json.loads(Path(os.environ["FR3_DIAG_IDENTITY_MANIFEST"]).read_text(encoding="utf-8"))


def check_loaded(pins, stage, required=()):
    """Check actual __file__, ModuleSpec/loader origin and content; never import targets."""
    records = []
    try:
        for name, pin in pins.items():
            module = sys.modules.get(name)
            if module is None:
                if name in required:
                    raise RuntimeError("Required module was not loaded: " + name)
                continue
            expected = Path(pin["path"]).resolve()
            actual = Path(getattr(module, "__file__", "")).resolve()
            spec = getattr(module, "__spec__", None)
            origin = getattr(spec, "origin", None)
            loader = getattr(module, "__loader__", None)
            loader_path = loader.get_filename(name) if hasattr(loader, "get_filename") else None
            record = {"name": name, "expected": str(expected), "actual": str(actual),
                      "spec_origin": origin, "loader_path": loader_path, "stage": stage}
            records.append(record)
            if (actual != expected or (origin is not None and Path(origin).resolve() != expected)
                    or (name != "__main__" and origin is None)
                    or (loader_path is not None and Path(loader_path).resolve() != expected)
                    or sha(actual) != pin["sha256"]):
                raise RuntimeError("Loaded module differs from frozen origin/content: " + name)
    except BaseException as error:
        publish(stage, records, error)
        raise
    publish(stage, records)


def check_symbol(value, pin, label):
    """Check the executed function's code filename or the actual class source module."""
    expected = Path(pin["path"]).resolve()
    code = getattr(value, "__code__", None)
    actual = Path(code.co_filename if code is not None else inspect.getsourcefile(value) or "").resolve()
    error = None
    if actual != expected or sha(actual) != pin["sha256"]:
        error = RuntimeError("Loaded symbol differs from frozen source: " + label)
    publish("loaded_symbol", [{"label": label, "actual": str(actual), "expected": str(expected)}], error)
    if error:
        raise error


def publish(stage, records, error=None):
    evidence = Path(os.environ["FR3_DIAG_EVIDENCE"])
    with (evidence / "module-origins.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"stage": stage, "records": records,
            "error": repr(error) if error else None, "env_constructed_by_guard": False}) + "\n")


def module_pins(manifest, *, include_main=False):
    evidence = Path(os.environ["FR3_DIAG_EVIDENCE"])
    pins = dict(manifest["loaded_module_pins"])
    for name, filename in manifest["run_module_names"].items():
        pins[name] = {"path": str(evidence / filename),
                      "sha256": manifest["prepared_module_sha256"][filename]}
    if include_main:
        pins["__main__"] = {"path": manifest["audit_entry"],
                            "sha256": manifest["prepared_module_sha256"]["d2_entry.py"]}
    return pins


def registration(manifest, *, resolve_class=False):
    import gymnasium as gym

    spec = gym.spec("Lab-FR3-Pick-Tube-v0")
    if (spec.entry_point != "fr3_contact.environment:FR3ContactEnv"
            or spec.kwargs.get("env_cfg_entry_point") != "fr3_contact.env_cfg:FR3TubeEnvCfg"):
        raise RuntimeError("Gym registration resolves a different Task/configuration")
    if resolve_class:
        environment = sys.modules["fr3_contact.environment"]
        config = sys.modules["fr3_contact.env_cfg"]
        check_symbol(environment.FR3ContactEnv,
                     manifest["loaded_module_pins"]["fr3_contact.environment"], "registered Task class")
        check_symbol(environment.FR3ContactEnv.__init__,
                     manifest["loaded_module_pins"]["fr3_contact.environment"], "registered Task constructor")
        check_symbol(config.FR3TubeEnvCfg,
                     manifest["loaded_module_pins"]["fr3_contact.env_cfg"], "registered Task configuration")
    publish("gym_registration_resolved" if resolve_class else "gym_registration_string", [{
        "entry_point": spec.entry_point, "env_cfg_entry_point": spec.kwargs["env_cfg_entry_point"]}])
