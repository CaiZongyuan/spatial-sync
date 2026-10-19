"""Command declarations shared by argparse and MCP registration.

Platform commands bind to GatewayClient methods, which own HTTP routes. Local
skill commands dispatch before creating that client. Parameters, help and result
types belong here; neither frontend needs another registration.
Complex legacy commands can migrate here when their CLI/MCP adapters are needed.
"""

from dataclasses import dataclass
from inspect import Parameter
from typing import Any


@dataclass(frozen=True)
class Argument:
    name: str
    flags: tuple[str, ...]
    annotation: Any
    help: str
    default: Any = Parameter.empty
    client_name: str | None = None

    def add_to(self, parser):
        options = {"help": self.help}
        if self.flags[0].startswith("-"):
            flags = self.flags
            options["dest"] = self.name
            if self.default is Parameter.empty:
                options["required"] = True
        else:
            flags = (self.name,)
            options["metavar"] = self.flags[0]
            if self.default is not Parameter.empty:
                options["nargs"] = "?"
        if self.default is not Parameter.empty:
            options["default"] = self.default
        if self.annotation is bool:
            options["action"] = "store_false" if self.default is True else "store_true"
        else:
            # Nullable options retain None when omitted; their supplied value is a string.
            options["type"] = str if self.annotation == str | None else self.annotation
        parser.add_argument(*flags, **options)


@dataclass(frozen=True)
class Command:
    path: tuple[str, ...]
    help: str
    method: str
    arguments: tuple[Argument, ...] = ()
    output_type: Any = dict[str, Any]
    mcp_name: str | None = None
    human_only: bool = False
    actor: bool = False

    async def invoke(self, client, arguments, *, actor=None):
        values = {
            argument.client_name or argument.name: arguments[argument.name] for argument in self.arguments
        }
        if actor is not None:
            values["actor"] = actor
        return await getattr(client, self.method)(**values)


COMMANDS = (
    Command(
        path=("skills", "ls"),
        help="List product skills bundled with this platform version; does not contact the gateway.",
        method="skills_list",
        output_type=list[dict[str, Any]],
    ),
    Command(
        path=("skills", "install"),
        help="Install product skills locally, keeping identical files and refusing conflicts.",
        method="skills_install",
        arguments=(Argument("target", ("--target",), str | None,
                            "Agent skill directory (default: ~/.claude/skills)", None),),
    ),
    Command(
        path=("library", "search"),
        help="Search scenes or assets in both library roots; defaults to scenes for compatibility.",
        method="library_search",
        arguments=(
            Argument("query", ("--query", "-q"), str | None, "Text to find in names or titles", None),
            Argument("launchable", ("--launchable",), bool, "Only list launchable scenes", False),
            Argument("resource", ("--resource",), str, "scenes or assets", "scenes"),
            Argument("kind", ("--kind",), str | None, "Asset kind; also selects assets", None),
            Argument("gaps", ("--gaps",), bool, "Only assets with completeness gaps", False),
        ),
        output_type=list[dict[str, Any]],
        mcp_name="library_search",
    ),
    Command(
        path=("library", "describe"),
        help="Describe a scene name[@rev] or asset kind/name[@rev], including validation and provenance.",
        method="library_describe",
        arguments=(Argument("id", ("id",), str, "Scene or asset ID; reads latest when rev is omitted"),),
        mcp_name="library_describe",
    ),
    Command(
        path=("asset", "ls"),
        help="Search asset versions in the built-in and user libraries.",
        method="asset_search",
        arguments=(
            Argument("kind", ("--kind",), str | None, "robot, device, labware or environment", None),
            Argument("gaps", ("--gaps",), bool, "Only list assets with completeness gaps", False),
            Argument("query", ("--query", "-q"), str | None, "Text to find in names or titles", None),
        ),
        output_type=list[dict[str, Any]],
    ),
    Command(
        path=("asset", "show"),
        help="Describe an asset's metadata, types, validation, provenance and indexed files.",
        method="library_describe",
        arguments=(Argument("id", ("kind/name[@rev]",), str, "Asset ID; reads latest when rev is omitted"),),
    ),
    Command(
        path=("asset", "import"),
        help="Pack a local asset directory and upload tar bytes; the gateway never receives a host path.",
        method="library_import",
        arguments=(Argument("directory", ("dir",), str, "Directory on this CLI/MCP client's filesystem"),),
        mcp_name="library_import",
        actor=True,
    ),
    Command(
        path=("scene", "ls"),
        help="Search scene versions in the built-in and user libraries.",
        method="scene_search",
        arguments=(
            Argument("query", ("--query", "-q"), str | None, "Text to find in scene names or titles", None),
            Argument("launchable", ("--launchable",), bool, "Only list launchable versions", False),
        ),
        output_type=list[dict[str, Any]],
    ),
    Command(
        path=("scene", "show"),
        help="Describe a scene version's entities, devices, actions, teach locations and validation.",
        method="library_describe",
        arguments=(Argument("id", ("name[@rev]",), str, "Scene name or name@rev; reads latest if omitted"),),
    ),
    Command(
        path=("scene", "launch"),
        help="Launch an exact scene version, or the latest launchable version of a name.",
        method="scene_launch",
        arguments=(
            Argument("sceneVersion", ("name[@rev]",), str, "Scene name or exact name@rev"),
            Argument(
                "source", ("--source",), str | None, "synthetic or newton; package default if omitted", None
            ),
        ),
        mcp_name="scene_launch",
        actor=True,
    ),
    Command(
        path=("ls",),
        help="List entities, optionally filtered by Catalog type or text query.",
        method="list_entities",
        arguments=(
            Argument("type", ("--type",), str | None, "Catalog type, such as centrifuge/v1", None),
            Argument("query", ("--query", "-q"), str | None, "Text to find in entity keys or titles", None),
        ),
        output_type=list[dict[str, Any]],
        mcp_name="list_entities",
    ),
    Command(
        path=("show",),
        help="Read definition, units, action schemas/conditions/risks, state and active calls before acting.",
        method="describe_entity",
        arguments=(Argument("key", ("key",), str, "Entity key from the Catalog"),),
        mcp_name="describe_entity",
    ),
    Command(
        path=("cancel",),
        help="Request cancellation of an active invocation.",
        method="cancel_invocation",
        arguments=(Argument("invocationId", ("id",), str, "Invocation ID", client_name="invocation_id"),),
        mcp_name="cancel_invocation",
        actor=True,
    ),
    Command(
        path=("approve",),
        help="Approve or deny a pending request with human confirmation in an interactive terminal.",
        method="approve_invocation",
        arguments=(
            Argument("invocation_id", ("id",), str, "Invocation ID"),
            Argument("deny", ("--deny",), bool, "Deny instead of approving", False),
            Argument("note", ("--note",), str, "Audit note for this decision", ""),
        ),
        mcp_name="approve_invocation",
        human_only=True,
    ),
    Command(
        path=("rec", "ls"),
        help="List local recordings.",
        method="recordings",
        output_type=list[dict[str, Any]],
    ),
)


def register_cli(commands):
    groups = {}
    for command in COMMANDS:
        parent = commands
        for index, name in enumerate(command.path[:-1], 1):
            path = command.path[:index]
            if path not in groups:
                group = parent.add_parser(name, help=f"{name} commands")
                groups[path] = group.add_subparsers(required=True)
            parent = groups[path]
        parser = parent.add_parser(command.path[-1], help=command.help, description=command.help)
        parser.add_argument("--json", action="store_true", help="machine-readable JSON")
        for argument in command.arguments:
            argument.add_to(parser)
        parser.set_defaults(registered_command=command)
