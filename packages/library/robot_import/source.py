"""Resolve uploaded robot resources within the extracted archive."""

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from packages.library.archive import invalid, package_files, relative_path
from packages.library.visuals import image_dimensions

XML_LIMIT = 8 * 1024 * 1024
XML_NODES = 50000
MESH_SUFFIXES = {".stl", ".obj", ".ply"}
TEXTURE_SUFFIXES = {".png", ".jpeg", ".jpg"}


class Source:
    def __init__(self, root, packages):
        self.root = root
        self.files = set(package_files(root)) - {"robot.json"}
        self.packages = packages
        self.xml_budget = XML_LIMIT
        self.node_budget = XML_NODES

    def resolve(self, owner, value, suffixes, *, directory=None):
        if value.startswith("package://"):
            package, _, member = value.removeprefix("package://").partition("/")
            directory = self.packages.get(package)
            if directory is None:
                raise invalid(f"Unknown archive package {package}", path=owner)
            try:
                relative_path(member)
            except ValueError as exc:
                raise invalid("package:// path must stay within its declared root", path=owner) from exc
            value = member
        elif directory is None:
            directory = str(PurePosixPath(owner).parent)
        if not value or value.startswith("/") or any(char in value for char in ":\\%?#"):
            raise invalid("Resource must be an archive-local path", path=owner)
        parts = [] if directory == "." else directory.split("/")
        for part in value.split("/"):
            if part == ".." and parts:
                parts.pop()
            elif part != ".":
                try:
                    parts.extend(relative_path(part).parts)
                except ValueError as exc:
                    raise invalid("Resource escapes the archive or is ambiguous", path=owner) from exc
        target = "/".join(parts)
        if PurePosixPath(target).suffix.lower() not in suffixes or target not in self.files:
            raise invalid(f"Missing or unsupported archive resource {target}", path=owner)
        return target

    def xml(self, name):
        path = self.root / name
        self.xml_budget -= path.stat().st_size
        if self.xml_budget < 0:
            raise invalid("Robot XML exceeds the expanded parsing budget", path=name)
        try:
            content = path.read_text(encoding="utf-8-sig")
            if "\x00" in content or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", content, re.I):
                raise ValueError("DTD and entity definitions are unsupported")
            if "xacro:" in content or "${" in content or "$(" in content:
                raise ValueError("Expand xacro before upload")
            root = ET.fromstring(content)
            self.node_budget -= sum(1 for _ in root.iter())
            if self.node_budget < 0:
                raise ValueError("Robot XML has too many expanded elements")
            return root
        except (ET.ParseError, UnicodeError, ValueError) as exc:
            raise invalid(str(exc), path=name) from exc

    def mesh_bytes(self, name):
        value = (self.root / name).read_bytes()
        suffix = PurePosixPath(name).suffix.lower()
        if suffix == ".obj" and re.search(rb"(?mi)^\s*(mtllib|call|csh)\b", value.replace(b"\\\n", b"")):
            raise invalid("OBJ must be self-contained without external materials or commands", path=name)
        return value

    def texture_bytes(self, name):
        value = (self.root / name).read_bytes()
        try:
            if not all(0 < dim <= 4096 for dim in image_dimensions(value)):
                raise ValueError("Texture exceeds 4096 × 4096")
        except ValueError as exc:
            raise invalid(str(exc), path=name) from exc
        return value

    def mjcf(self, entry):
        assets, resource_names = {}, {}
        # MuJoCo's compiler paths and asset files are all replaced before loading.
        # Includes are resolved at the source document, never by the SDK.
        def expand(name, stack, directories):
            if name in stack or len(stack) == 16:
                raise invalid("Recursive or excessively deep MJCF include", path=name)
            document = self.xml(name)
            if document.tag not in ("mujoco", "mujocoinclude"):
                raise invalid("Expected a MuJoCo XML document", path=name)
            parent = PurePosixPath(name).parent
            directories = directories.copy()
            for compiler in document.findall("compiler"):
                common = compiler.attrib.pop("assetdir", "")
                for kind in ("mesh", "texture"):
                    declared = compiler.attrib.pop(kind + "dir", common)
                    if declared:
                        try:
                            directories[kind] = str(parent / relative_path(declared.rstrip("/")))
                        except ValueError as exc:
                            raise invalid("Compiler directory must be archive-local", path=name) from exc
                for attribute in ("strippath", "fusestatic", "discardvisual"):
                    compiler.attrib.pop(attribute, None)

            pending = [(document, 0)]
            while pending:
                node, depth = pending.pop()
                if depth > 64:
                    raise invalid("MJCF nesting exceeds the parsing budget", path=name)
                for child in list(node):
                    if child.tag == "include":
                        target = self.resolve(name, child.get("file", ""), {".xml", ".mjcf"})
                        offset = list(node).index(child)
                        node.remove(child)
                        for element in reversed(list(expand(target, (*stack, name), directories))):
                            node.insert(offset, element)
                    else:
                        pending.append((child, depth + 1))
                if (node.tag in ("equality", "tendon") and len(node)
                        or node.tag in ("extension", "plugin", "flexcomp", "composite",
                                        "replicate", "attach", "hfield", "deformable")):
                    raise invalid(f"Unsupported MJCF element {node.tag}", path=name)
                if "content_type" in node.attrib:
                    raise invalid("MJCF resource decoder must be determined by its file extension", path=name)
                for key, value in list(node.attrib.items()):
                    if not key.startswith("file"):
                        continue
                    if node.tag not in ("mesh", "texture"):
                        raise invalid(f"Unsupported MJCF file resource on {node.tag}", path=name)
                    mesh = node.tag == "mesh"
                    target = self.resolve(name, value, MESH_SUFFIXES if mesh else TEXTURE_SUFFIXES,
                                          directory=directories.get(node.tag))
                    if key == "file" and "name" not in node.attrib:
                        node.set("name", PurePosixPath(target).stem)
                    if target not in resource_names:
                        virtual = f"resource{len(assets)}{PurePosixPath(target).suffix.lower()}"
                        assets[virtual] = self.mesh_bytes(target) if mesh else self.texture_bytes(target)
                        resource_names[target] = virtual
                    virtual = resource_names[target]
                    node.set(key, virtual)
                if node.tag == "texture" and "file" not in node.attrib:
                    if any(not 0 < int(node.get(key, "1")) <= 4096 for key in ("width", "height")):
                        raise invalid("MJCF texture exceeds the rendering budget", path=name)
            return document

        document = expand(entry, (), {})
        return ET.tostring(document, encoding="unicode"), assets
