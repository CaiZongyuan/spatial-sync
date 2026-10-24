"""Recoverable Candidate and fixed environment/judge facts for evaluations."""

import ast
import hashlib
import inspect
import json
import platform
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from packages.library.archive import package_files
from packages.library.common import REPO_ROOT

TEXT = {".py", ".yaml", ".yml", ".json", ".lock"}
ENVIRONMENT = (
    "packages/programs",
    "packages/policies",
    "packages/sim_runtime",
    "packages/protocol",
    "apps/api/app/sources",
    "apps/api/app/session_manager.py",
    "devices",
    "uv.lock",
    "packages/info/program.py",
    "packages/info/diff.py",
    "packages/info/catalog.py",
    "packages/info/declarations.py",
    "packages/info/binding_validation.py",
    "packages/info/store.py",
    "packages/info/log.py",
    "packages/info/journal.py",
)
JUDGE = (
    "packages/info",
    "packages/agent_evaluation",
    "apps/api/app/evaluations.py",
    "apps/api/app/agent_evaluations.py",
    "apps/api/app/evaluation_views.py",
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def source_files(paths, *, root=REPO_ROOT):
    result = {}
    for name in paths:
        path = root / name
        if not path.exists():
            raise FileNotFoundError("Required implementation input is unavailable")
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        selected = [item for item in files if item.is_file() and item.suffix in TEXT]
        if not selected:
            raise FileNotFoundError("Required implementation input is empty")
        for item in selected:
            if item.is_symlink() or item.is_junction() or not item.resolve().is_relative_to(root.resolve()):
                raise ValueError("Implementation input is not an owned regular file")
            result[item.relative_to(root).as_posix()] = item.read_bytes()
    return result


def digest_files(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + content.replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def code_digest(paths, *, root=REPO_ROOT):
    return digest_files(source_files(paths, root=root))


def implementation_identities(*, root=None):
    """Compatibility projection for Notebook public implementation snapshots."""
    root = REPO_ROOT if root is None else root
    return {
        "scheme": "public-code-lf-sha256/1",
        "candidate": code_digest(("packages/programs",), root=root),
        "environment": code_digest(ENVIRONMENT, root=root),
        "judge": code_digest(JUDGE, root=root),
    }


def split_program_class(content, name):
    canonical = content.replace(b"\r\n", b"\n")
    tree = ast.parse(canonical.decode("utf-8"))
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    if len(matches) != 1 or matches[0].decorator_list:
        raise ValueError("Selected Program class cannot be isolated")
    node = matches[0]
    lines = canonical.splitlines(keepends=True)
    candidate = b"".join(lines[node.lineno - 1 : node.end_lineno])
    environment = b"".join(lines[: node.lineno - 1]) + ("# Candidate class: " + name + "\n").encode()
    environment += b"".join(lines[node.end_lineno :])
    return candidate, environment, {"class": name, "firstLine": node.lineno, "lastLine": node.end_lineno}


def program_registry(source):
    if source == "synthetic":
        from apps.api.app.sources.synthetic_source import REGISTRY
    elif source == "newton":
        from packages.sim_runtime.info_catalog import REGISTRY
    else:
        raise ValueError("Source implementation is unknown")
    return REGISTRY


def freeze_identity(identifier, suite, scene, subject, directory, *, candidate=None, root=REPO_ROOT):
    """Freeze input bytes before Source effects; observed Source facts bind later."""
    environment = source_files(ENVIRONMENT, root=root)
    judge = source_files(JUDGE, root=root)
    original = environment | judge
    reason = None
    selected = None
    slot = None
    program_candidate = candidate is None
    if candidate is None:
        candidate = {"kind": "scene-program", "programId": subject, "contentHash": None}
    if program_candidate or candidate["kind"] == "policy":
        try:
            bindings = scene.report()["effectiveBindings"][suite.metadata["source"]]
            targets = [binding for binding in bindings if binding["key"] == suite.task["robot"]]
            if len(targets) != 1 or program_candidate and targets[0]["program"] != subject:
                raise ValueError("Fixed Suite Program slot is unknown")
            registry = program_registry(suite.metadata["source"])
            program = registry[targets[0]["program"]]
            selected = Path(inspect.getsourcefile(program)).resolve().relative_to(root.resolve()).as_posix()
            raw = original[selected]
            content, masked, location = split_program_class(raw, program.__name__)
            slot = {
                "programId": targets[0]["program"],
                "target": suite.task["robot"],
                "module": selected,
                "location": location,
            }
            shared = any(
                binding["key"] != slot["target"]
                and binding["program"] in registry
                and issubclass(registry[binding["program"]], program)
                for binding in bindings
            )
            if shared:
                reason = "shared_candidate_implementation"
            else:
                environment[selected] = masked
            if program_candidate:
                candidate |= {
                    "contentHash": hashlib.sha256(content).hexdigest(),
                    "module": selected,
                    "originalFileHash": hashlib.sha256(raw).hexdigest(),
                    "location": location,
                    "target": suite.task["robot"],
                }
        except (KeyError, ValueError, TypeError, OSError):
            reason = "candidate_implementation_unknown"
    inputs = {
        "suite": suite.id,
        "suiteContentHash": suite.content_hash,
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": suite.metadata["source"],
        "assets": sorted(
            [*scene.asset_dependencies(), *scene.template_dependencies()], key=lambda item: item["id"]
        ),
    }
    environment_facts = {
        "code": digest_files(environment),
        "inputs": inputs,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "protocol": suite.metadata,
        "seeds": getattr(suite, "seeds", None),
    }
    task = getattr(suite, "task", None)
    judge_facts = {
        "code": digest_files(judge),
        "suite": suite.id,
        "criteria": task if task is not None else suite.grading,
    }
    # Retain complete original bytes, not just masked hashes or a possibly dirty commit.
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / (identifier + ".zip")
    with ZipFile(archive, "x", compression=ZIP_DEFLATED) as output:
        for name, raw in sorted(original.items()):
            output.writestr("code/" + name, raw)
        packages = [(suite.path, "suite"), (scene.path, "scene")]
        dependencies = getattr(scene, "template_assets", {}) | getattr(scene, "runtime_assets", {})
        packages.extend(
            (Path(snapshot["path"]), "dependencies/" + identifier.replace("@", "/"))
            for identifier, snapshot in sorted(dependencies.items())
        )
        for package, prefix in packages:
            for name in package_files(package):
                output.writestr(prefix + "/" + name, (package / name).read_bytes())
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(original.items())}
    return {
        "scheme": "evaluation-identity/1",
        "candidateIdentity": candidate,
        "environmentInputs": environment_facts,
        "judgeInputs": judge_facts,
        "files": manifest,
        "selectedProgramModule": selected,
        "programSlot": slot,
        "unknownReason": reason,
        "recovery": {"file": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "staticIdentity": fingerprint(
            {"environment": environment_facts, "judge": judge_facts, "candidate": candidate}
        ),
    }


def bind_source_identity(frozen, session, *, execution_facts=None):
    """Use actual loaded Source/engine facts; declarations alone prove no equality."""
    source = session.manifest.source
    actual = {
        "kind": source.kind,
        "newtonVersion": source.newtonVersion,
        "solver": source.solver,
        "implementation": type(session.source).__module__ + "." + type(session.source).__qualname__,
        "options": {
            name: getattr(session.options, name)
            for name in ("physicsDt", "controlHz", "factHz", "device", "strictLabels")
        },
    }
    supported = actual["implementation"] in (
        "apps.api.app.sources.synthetic_source.SyntheticSource",
        "apps.api.app.sources.newton_source.NewtonSource",
    )
    reason = frozen.get("unknownReason")
    if execution_facts is not None:
        version = execution_facts.get("runtimeVersion") if isinstance(execution_facts, dict) else None
        providers = execution_facts.get("availableProviders") if isinstance(execution_facts, dict) else None
        unavailable = execution_facts == {
            "available": False,
            "runtimeVersion": None,
            "availableProviders": [],
        }
        if unavailable or (
            isinstance(execution_facts, dict)
            and (
                set(execution_facts) == {"runtimeVersion", "availableProviders"}
                or set(execution_facts) == {"available", "runtimeVersion", "availableProviders"}
                and execution_facts["available"] is True
            )
            and isinstance(version, str)
            and version
            and isinstance(providers, list)
            and all(isinstance(item, str) and item for item in providers)
        ):
            actual["observationRuntime"] = {
                "available": not unavailable,
                "runtimeVersion": version,
                "availableProviders": sorted(set(providers)),
            }
        else:
            reason = "execution_runtime_unknown"
    if not supported or source.kind == "newton" and not source.newtonVersion:
        reason = "source_implementation_unknown"
    slot = frozen.get("programSlot")
    if slot:
        registry = program_registry(source.kind)
        target_class = registry.get(slot["programId"])
        for key, binding in session.catalog.entities.items():
            other = registry.get(binding["program"])
            if key != slot["target"] and other is not None and target_class is not None:
                if other is target_class or issubclass(other, target_class):
                    reason = "shared_candidate_implementation"
    identity = (
        None
        if reason
        else {
            "environment": fingerprint(frozen["environmentInputs"] | {"observedSource": actual}),
            "judge": fingerprint(frozen["judgeInputs"]),
        }
    )
    return identity, actual, reason


def comparison_identity(value):
    identity = value.get("comparisonIdentity")
    return (
        identity
        if isinstance(identity, dict)
        and all(isinstance(identity.get(key), str) and identity[key] for key in ("environment", "judge"))
        else None
    )


def verify_identity(frozen, *, root=REPO_ROOT, directory=None):
    if frozen is None:
        return
    current = source_files((*ENVIRONMENT, *JUDGE), root=root)
    if {name: hashlib.sha256(raw).hexdigest() for name, raw in current.items()} != frozen["files"]:
        raise ValueError("Fixed evaluation implementation changed")
    if directory is not None:
        archive = directory / frozen["recovery"]["file"]
        if (
            not archive.is_file()
            or archive.is_symlink()
            or archive.is_junction()
            or not archive.resolve().is_relative_to(directory.resolve())
            or hashlib.sha256(archive.read_bytes()).hexdigest() != frozen["recovery"]["sha256"]
        ):
            raise ValueError("Recoverable evaluation implementation snapshot is unavailable")


def comparable(first, second):
    one, two = comparison_identity(first), comparison_identity(second)
    return bool(one and two and one == two and first.get("key") == second.get("key"))
