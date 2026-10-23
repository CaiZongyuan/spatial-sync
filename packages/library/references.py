"""Conservative, explicit A2 consumers for exact library version deletion."""

import json
import re

from packages.info.recording import SCENE_VERSION, recording_metadata
from packages.library.common import LibraryError
from packages.library.reports import HASH, parse_report
from packages.library.templates import references, template_id


def dependency_reports(version, kind):
    """Do not let a missing latest pointer or a misbound history document erase a pin."""
    subject = f"{kind}:{version.id}"
    result = [version.validation] if version.validation is not None else []
    if version.reports:
        directory = version.reports.directory(subject, version.content_hash)
        ancestor = version.reports.root
        for part in ("", *directory.relative_to(ancestor).parts):
            ancestor = ancestor / part
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("Validation history has a filesystem link ancestor")
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Validation history document is a filesystem link")
            if path.name == "latest.json":
                version.reports.latest(subject, version.content_hash, version.validation)
            elif re.fullmatch(r"[a-f0-9]{32}", path.stem):
                report = version.reports._read(directory, path.stem, subject, version.content_hash)
                if report is None:
                    raise ValueError("Validation history identity differs from its directory")
                result.append(report)
            else:
                raise ValueError("Unrecognised validation history document")
    for report in result:
        parse_report(report, subject)
    return result


class ReferenceInspection:
    def __init__(self, assets, scenes, recordings, sessions, validator, suites=None, *, live=None,
                 tasks=None, jobs=None):
        self.assets, self._scenes = assets, scenes
        self.recordings, self.sessions, self.validator = recordings, sessions, validator
        self.suites = suites
        self.tasks = tasks
        self.jobs = jobs
        self._inspected = {}
        self.live = live or (lambda: None)

    @property
    def scenes(self):
        return self._scenes()

    def scan(self, target, kind):
        found, unknown = [], []

        def reference(owner_kind, identifier, reason):
            item = {"kind": owner_kind, "id": identifier, "reason": reason}
            if item not in found:
                found.append(item)

        def problem(owner_kind, identifier, error):
            unknown.append({"kind": owner_kind, "id": identifier, "reason": str(error)})

        # Future product stores have no A2 reader. Existing contents cannot be declared empty.
        reserved = (("tasks",) if self.tasks is None else ()) + (("jobs",) if self.jobs is None else ())
        for name in reserved + ("policies",):
            directory = self.assets.roots["user"] / name
            if directory.is_symlink() or directory.is_junction() or (
                directory.exists() and (not directory.is_dir() or any(directory.iterdir()))
            ):
                problem("training", name, "This product store is not supported by A2 reference inspection")

        if self.jobs is not None:
            try:
                jobs = self.jobs()
                directory = self.assets.roots["user"] / "jobs"
                if directory.is_symlink() or directory.is_junction():
                    raise ValueError("Linked job store")
                if directory.exists() and any(path.name not in jobs for path in directory.iterdir()):
                    raise ValueError("Unindexed job outputs")
                for job in jobs.values():
                    scene = self.scenes.resolve(job["scene"])
                    if scene.content_hash != job["sceneContentHash"]:
                        raise ValueError("Pinned job scene changed")
                    if kind == "scene" and target.id == job["scene"]:
                        reference("job", job["jobId"], "Pinned training job scene")
                    if kind == "asset" and target.id in {item["id"] for item in job["assets"]}:
                        reference("job", job["jobId"], "Pinned training job asset")
            except (OSError, ValueError, KeyError, LibraryError):
                problem("job", "library", "Cannot inspect every retained training job")

        if self.tasks is not None:
            try:
                for task in self.tasks.list():
                    if kind == "scene" and task["task"]["scene"] == target.id:
                        reference("task", task["id"], "Pinned training Task scene version")
            except (OSError, ValueError, LibraryError):
                problem("task", "library", "Cannot inspect every pinned training Task scene")

        if self.suites is not None:
            try:
                self.suites.refresh()
                for suite in self.suites.versions.values():
                    suite.verify()
                    scene = self.scenes.resolve(suite.metadata["scene"])
                    if scene.content_hash != suite.metadata["sceneContentHash"]:
                        raise ValueError("Suite scene content changed")
                    if kind == "scene" and target.id == scene.id:
                        reference("suite", suite.id, "Pinned evaluation scene version")
            except (OSError, ValueError, LibraryError):
                problem("suite", "library", "Cannot inspect every pinned evaluation scene")

        collections = (("asset", self.assets), ("scene", self.scenes))
        for owner_kind, collection in collections:
            # The normal index glob can omit a damaged manifest. Detect unindexed revision directories.
            pattern = "*/*/*" if owner_kind == "asset" else "*/*"
            for root_name, root in collection.roots.items():
                directory = root / ("assets" if owner_kind == "asset" else "scenes")
                for path in directory.glob(pattern):
                    if not path.is_dir() or path.is_symlink() or path.is_junction() or not any(
                        version.root == root_name and version.path == path
                        for version in collection.versions.values()
                    ):
                        problem(owner_kind, path.relative_to(root).as_posix(), "Unindexed or linked package")
            for version in sorted(collection.versions.values(), key=lambda value: value.id):
                if owner_kind == kind and version.id == target.id:
                    continue
                dependencies = set()
                try:
                    relative = version.path.relative_to(collection.roots[version.root]).as_posix()
                    collection.indexed_file(version.root, relative + f"/{owner_kind}.yaml")
                    reports = dependency_reports(version, owner_kind)
                    dependencies = set(references(version.bindings)) if owner_kind == "scene" else set()
                    seen_hashes = {}
                    for report in reports:
                        entries = report.get("referencedAssets", [])
                        if not isinstance(entries, list):
                            raise ValueError("Invalid recorded asset dependencies")
                        for entry in entries:
                            if not isinstance(entry, dict):
                                raise ValueError("Invalid recorded asset dependency")
                            identifier = template_id(entry.get("id"))
                            dependencies.add(identifier)  # Hashless exact IDs still retain a reference.
                            digest = entry.get("contentHash")
                            if digest is not None:
                                if not isinstance(digest, str) or not HASH.fullmatch(digest):
                                    raise ValueError("Invalid recorded dependency hash")
                                if identifier in seen_hashes and seen_hashes[identifier] != digest:
                                    raise ValueError("Conflicting historical dependency hashes")
                                seen_hashes[identifier] = digest
                    if not version.metadata["stage"].startswith("python:"):
                        latest = version.report()
                        check = next((item for item in (latest or {}).get("checks", [])
                                      if item["id"] == "S7"), None)
                        if not check:
                            key = (owner_kind, version.id, version.content_hash)
                            if key not in self._inspected:
                                self._inspected[key] = self.validator.run_structure(
                                    version.path, owner_kind, f"{owner_kind}:{version.id}",
                                    version.metadata, self.assets, getattr(version, "bindings", None),
                                    getattr(version, "locations", None),
                                )
                            latest = self._inspected[key]
                            check = next((item for item in latest["checks"] if item["id"] == "S7"), None)
                        for entry in (latest or {}).get("referencedAssets", []):
                            dependencies.add(template_id(entry.get("id")))
                        if not check or check["status"] != "pass":
                            raise ValueError("USD dependency inspection did not complete")
                    for identifier in dependencies:
                        dependency = self.assets.resolve(identifier)
                        if identifier in seen_hashes and dependency.content_hash != seen_hashes[identifier]:
                            raise ValueError("Recorded dependency bytes differ: " + identifier)
                    if kind == "asset" and target.id in dependencies:
                        reference(owner_kind, version.id, "Exact template, USD or historical dependency")
                except (OSError, ValueError, LibraryError) as exc:
                    # Retain a directly authored reference even when another inspection failed.
                    if kind == "asset" and target.id in dependencies:
                        reference(owner_kind, version.id, "Known dependency; other inspection is incomplete")
                    problem(owner_kind, version.id, exc)

        if self.recordings.root.exists():
            for directory in sorted(self.recordings.root.iterdir()):
                try:
                    if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
                        raise ValueError("Uninspectable recording entry")
                    metadata = directory / "recording.json"
                    if metadata.is_symlink() or metadata.is_junction():
                        raise ValueError("Recording metadata is a filesystem link")
                    value = recording_metadata(json.loads(metadata.read_bytes()))
                    if value.get("sessionId") != directory.name:
                        raise ValueError("Recording identity differs from its directory")
                    if value["kind"] == "session":
                        scene = value["sceneVersion"]
                        if not isinstance(scene, str) or not SCENE_VERSION.fullmatch(scene):
                            raise ValueError("Historical recording has an unknown scene")
                        if kind == "scene" and scene == target.id:
                            reference("recording", directory.name, "Recorded exact scene version")
                        if kind == "asset" and scene not in self.scenes.versions:
                            raise ValueError("Recorded scene dependencies cannot be inspected: " + scene)
                except (OSError, ValueError) as exc:
                    problem("recording", directory.name, exc)

        for session in self.sessions():
            if session.state in ("STOPPED", "FAILED") and not (
                session.source or getattr(session, "_starting_source", None)
            ):
                continue
            if kind == "scene" and session.scene_version.id == target.id:
                reference("session", session.session_id, f"Session holds this version ({session.state})")
            if kind == "asset":
                held = session.scene_version
                try:
                    templates = set(references(held.bindings)) | set(held.template_assets)
                    if target.id in templates:
                        reference("session", session.session_id,
                                  "Session holds an exact template: " + held.id)
                    indexed = self.scenes.versions.get(held.id)
                    if indexed is None or indexed.content_hash != held.content_hash:
                        # The graph above covers the held version only while that exact immutable
                        # scene remains indexed. Otherwise its transitive asset closure is unknown.
                        raise ValueError("Held scene version is no longer indexed unchanged: " + held.id)
                except (OSError, ValueError, LibraryError) as exc:
                    problem("session", session.session_id, exc)
        live = self.live()
        if live is not None and live.bindings.layout:
            layout = live.bindings.layout
            if kind == "scene" and target.id == layout:
                reference("live", live.bindings.live, "Configured exact Live layout scene version")
            if kind == "asset":
                indexed = self.scenes.versions.get(layout)
                held = live.layout_version
                if indexed is None or (held is not None and held.content_hash != indexed.content_hash):
                    problem("live", live.bindings.live, "Live layout asset dependencies cannot be inspected")
        return {
            "references": found, "complete": not unknown, "uninspected": unknown,
            "coverage": ["all scene and asset versions", "all bound validation history",
                         "all recording entries", "starting and retained sessions", "configured Live layout"]
                        + (["all Task versions"] if self.tasks is not None else []),
            "training": ("Task and Job pins inspected; nonempty policy stores still block deletion"
                         if self.tasks is not None and self.jobs is not None else
                         "Task scene pins inspected; nonempty job/policy stores still block deletion"
                         if self.tasks is not None else
                         "No A2 training store reader; nonempty reserved stores block deletion"),
        }
