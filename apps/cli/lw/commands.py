"""Command declarations shared by argparse and MCP registration.

Platform commands bind to GatewayClient methods, which own HTTP routes. Local
skill commands dispatch before creating that client. Parameters, help and result
types belong here; neither frontend needs another registration.
Complex legacy commands can migrate here when their CLI/MCP adapters are needed.
"""

import json
from dataclasses import dataclass
from inspect import Parameter
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import yaml


def yaml_document(path):
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Plan must be a mapping")
        return value
    except OSError as exc:
        raise ValueError("Cannot read plan document") from exc
    except yaml.YAMLError as exc:
        raise ValueError("Plan document is not valid YAML") from exc


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
        elif get_origin(self.annotation) is Literal:
            options["choices"] = get_args(self.annotation)
            options["type"] = type(get_args(self.annotation)[0])
        else:
            # Omitted nullable options stay None; supplied values use their concrete type.
            kinds = get_args(self.annotation)
            options["type"] = str if get_origin(self.annotation) in (dict, list) else (
                next(kind for kind in kinds if kind is not type(None))
                if type(None) in kinds else self.annotation
            )
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
        # CLI grammar accepts a path without I/O; MCP supplies the structured object directly.
        for argument in self.arguments:
            name = argument.client_name or argument.name
            if get_origin(argument.annotation) is list and isinstance(values[name], str):
                try:
                    values[name] = json.loads(Path(values[name]).read_text(encoding="utf-8"))
                    if not isinstance(values[name], list) or any(
                        not isinstance(item, dict) for item in values[name]
                    ):
                        raise ValueError("Runtime records must be a JSON array of objects")
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError("Cannot read runtime records JSON array") from exc
            if get_origin(argument.annotation) is dict and isinstance(values[name], str):
                path = Path(values[name])
                values[name] = yaml_document(path)
                if self.method in ("plan_submit", "plan_revise") and values[name].get("kind") == "training":
                    interview = values[name].get("interview")
                    if not isinstance(interview, str):
                        raise ValueError("Training plan must name an interview Markdown file")
                    try:
                        interview_path = (path.parent / interview).resolve()
                        if not interview_path.is_relative_to(path.parent.resolve()):
                            raise ValueError("Interview must be inside the plan directory")
                        values[name]["interview"] = interview_path.read_text(encoding="utf-8")
                    except OSError as exc:
                        raise ValueError("Cannot read training plan interview document") from exc
        if actor is not None:
            values["actor"] = actor
        return await getattr(client, self.method)(**values)


def description_arguments(default, metavar="id"):
    return (
        Argument("id", (metavar,), str, "Scene or asset ID; page requires an exact @rev"),
        Argument("view", ("--view",), Literal["summary", "page", "full"],
                 "Bounded summary, lossless page, or complete legacy object", default),
        Argument("section", ("--section",), str | None,
                 "Page one top-level field; omit for whole document", None),
        Argument("offset", ("--offset",), int, "Page offset in Unicode codepoints", 0),
        Argument("documentDigest", ("--document-digest",), str | None,
                 "Whole-description digest from summary; restart on description_changed", None),
    )


def runtime_import_options():
    return (
        Argument("format", ("--format",), Literal["claude-code-stream-json", "claude-code-session-jsonl"],
                 "Native capture format", "claude-code-stream-json"),
        Argument("sourceLabel", ("--source-label",), str | None, "Observed capture label", None),
        Argument("agentId", ("--agent",), str | None, "Optional existing Agent association", None),
        Argument("harnessVersion", ("--harness-version",), int | None, "Explicit Harness version", None),
        Argument("planId", ("--plan",), str | None, "Optional existing Plan association", None),
        Argument("planRevision", ("--plan-revision",), int | None, "Exact associated Plan revision", None),
        Argument("goalId", ("--goal",), str | None, "Optional existing Goal association", None),
        Argument("clearAssociations", ("--clear-associations",), bool, "Clear existing associations", False),
    )


COMMANDS = (
    Command(path=("runtime", "import"), help="Import an explicitly selected local runtime capture file.",
            method="runtime_import_file", arguments=(
                Argument("file", ("file",), str, "Explicit local UTF-8 capture, uploaded as content"),
                *runtime_import_options(),
            ), actor=True),
    Command(path=("runtime", "ingest"), help="Import native runtime record objects from a JSON array.",
            method="runtime_import_records", mcp_name="runtime_import", arguments=(
                Argument("records", ("records",), list[dict[str, Any]],
                         "Array of native objects (CLI: local JSON-array file)"),
                *runtime_import_options(),
            ), actor=True),
    Command(path=("runtime", "ls"), help="List imported runtime sessions and reported usage.",
            method="runtime_list", mcp_name="runtime_list", output_type=list[dict[str, Any]], arguments=(
                Argument("q", ("--query",), str | None, "Source/session/model text", None),
                Argument("runtime", ("--runtime",), str | None, "Runtime source", None),
                Argument("agent", ("--agent",), str | None, "Explicit associated Agent", None),
                Argument("plan", ("--plan",), str | None, "Explicit associated Plan", None),
                Argument("goal", ("--goal",), str | None, "Explicit associated Goal", None),
            )),
    Command(path=("runtime", "show"), help="Read an imported runtime session, messages and evidence links.",
            method="runtime_show", mcp_name="runtime_show",
            arguments=(Argument("id", ("id",), str, "Imported runtime session ID"),)),
    Command(path=("demo", "add"), help="Keep a proven successful recorded Task episode as a Demonstration.",
            method="demo_add", arguments=(
                Argument("recordingId", ("recordingId",), str, "Complete successful episode recording"),
                Argument("task", ("--task",), str | None,
                         "Exact source Task; required without a linked Plan", None),
            ), mcp_name="demo_add", actor=True),
    Command(path=("demo", "ls"), help="List Demonstrations and immutable DemoSet references.",
            method="demo_list", arguments=(
                Argument("task", ("--task",), str | None, "Exact source Task version", None),
            ), mcp_name="demo_list", output_type=list[dict[str, Any]]),
    Command(path=("notebook", "show"), help="Read train-only Hypotheses and human judgement audit.",
            method="notebook_show", arguments=(
                Argument("goal", ("goal",), str, "Goal ID or default Task goal"),
                Argument("refuted", ("--refuted",), bool, "Only refuted effective Hypotheses", False)),
            mcp_name="notebook_show"),
    Command(path=("loop", "show"), help="Read Goal rounds, comparison evidence and actual/committed budget.",
            method="loop_show", arguments=(Argument("goal", ("goal",), str, "Goal ID or default Task goal"),),
            mcp_name="loop_show"),
    Command(path=("notebook", "override"), help="Record a person's Hypothesis judgement and audit.",
            method="notebook_override", arguments=(
                Argument("goal", ("goal",), str, "Goal resource"),
                Argument("entry", ("entry",), str, "Entry ID"),
                Argument("hypothesisId", ("hypothesis",), str, "Hypothesis ID"),
                Argument("verdict", ("verdict",), Literal["confirmed", "refuted", "inconclusive"],
                         "Human judgement"),
                Argument("note", ("--note",), str, "Reason for changing the effective judgement"),
                Argument("requestId", ("--request-id",), str | None, "Stable retry identity", None),
            ), human_only=True, actor=True),
    Command(path=("tensorboard",), help="Start or return the owned local TensorBoard service.",
            method="tensorboard", mcp_name="tensorboard", actor=True),
    Command(path=("job", "start"), help="Queue a declared seed of an approved training plan.",
            method="job_start", arguments=(
                Argument("planId", ("planId",), str, "Current approved training plan ID"),
                Argument("seed", ("--seed",), int | None, "One of the declared seeds", None),
                Argument("planRevision", ("--revision",), int | None, "Expected approved revision", None),
                Argument("requestId", ("--request-id",), str | None, "Stable retry identity", None),
            ), mcp_name="job_start", actor=True),
    Command(path=("job", "ls"), help="List retained jobs and their real queue/approval state.",
            method="job_list", arguments=(
                Argument("plan", ("--plan",), str | None, "Plan ID", None),
                Argument("status", ("--status",), str | None, "Lifecycle status", None),
                Argument("kind", ("--kind",), str | None, "Job kind", None),
            ), output_type=list[dict[str, Any]], mcp_name="job_list"),
    Command(path=("job", "show"), help="Read a Job's pinned inputs, progress and artifacts.",
            method="job_show", arguments=(Argument("id", ("id",), str, "Job ID"),), mcp_name="job_show"),
    Command(path=("job", "cancel"), help="Cancel a job; release its lease after owned cleanup.",
            method="job_cancel", arguments=(Argument("id", ("id",), str, "Job ID"),),
            mcp_name="job_cancel", actor=True),
    Command(path=("job", "logs"), help="Read log chunks; CLI --follow continues until complete.",
            method="job_logs", arguments=(Argument("id", ("id",), str, "Job ID"),
                Argument("after", ("--after",), int, "Cursor from a previous chunk", 0),
                Argument("follow", ("--follow",), bool, "Wait for output; CLI repeats bounded reads", False)),
            mcp_name="job_logs"),
    Command(path=("node", "ls"), help="Read actual node capabilities, profiles, leases and queue.",
            method="node_list", output_type=list[dict[str, Any]], mcp_name="node_ls"),
    Command(
        path=("task", "push"), help="Snapshot an Isaac Lab Task source package without executing it.",
        method="task_push",
        arguments=(Argument("directory", ("dir",), str, "Task directory on this client"),),
        mcp_name="task_push", actor=True,
    ),
    Command(path=("task", "ls"), help="List immutable Task versions.", method="tasks_list",
            output_type=list[dict[str, Any]], mcp_name="task_list"),
    Command(path=("task", "show"), help="Read an exact Task version and its hash.", method="task_show",
            arguments=(Argument("id", ("task/name@rev",), str, "Exact Task version"),), mcp_name="task_show"),
    Command(
        path=("plan", "submit"), help="Submit a training plan and interview for human approval.",
        method="plan_submit", arguments=(Argument("document", ("plan.yaml",), dict[str, Any],
                                                  "Training plan; CLI reads its local interview file"),),
        mcp_name="plan_submit", actor=True,
    ),
    Command(
        path=("agent", "ls"), help="List Agents and their configured model and Reference version.",
        method="agents_list", output_type=list[dict[str, Any]], mcp_name="agent_list",
    ),
    Command(
        path=("agent", "show"), help="Describe an Agent; null model fields mean unconfigured.",
        method="agent_show", arguments=(Argument("id", ("id",), str, "Agent name or agent/name"),),
        mcp_name="agent_show",
    ),
    Command(
        path=("agent", "versions"), help="List immutable Harness versions.", method="agent_versions",
        arguments=(Argument("id", ("id",), str, "Agent name or agent/name"),),
        output_type=list[dict[str, Any]], mcp_name="agent_versions",
    ),
    Command(
        path=("agent", "snapshot"), help="Snapshot the editable Harness without promoting its Reference.",
        method="agent_snapshot", arguments=(Argument("id", ("id",), str, "Agent name or agent/name"),),
        mcp_name="agent_snapshot", actor=True,
    ),
    Command(
        path=("agent", "export"), help="Export a credential-free Harness tar to a new local file.",
        method="agent_export", arguments=(
            Argument("id", ("id",), str, "Agent name or agent/name"),
            Argument("version", ("version",), int, "Immutable Harness version"),
            Argument("output", ("--output",), str, "New output .tar path on this client's filesystem"),
        ), mcp_name="agent_export",
    ),
    Command(
        path=("agent", "import"), help="Import and restore a Harness tar as a new immutable Candidate.",
        method="agent_import", arguments=(
            Argument("id", ("id",), str, "Agent name or agent/name"),
            Argument("archive", ("archive",), str, "Harness .tar on this client's filesystem"),
        ), mcp_name="agent_import", actor=True,
    ),
    Command(
        path=("agent", "create"), help="Create a new Agent from a credential-free Harness tar.",
        method="agent_create", arguments=(
            Argument("id", ("id",), str, "New Agent name or agent/name"),
            Argument("archive", ("archive",), str, "Harness .tar on this client's filesystem"),
            Argument("title", ("--title",), str | None, "Agent title", None),
        ), mcp_name="agent_create", actor=True,
    ),
    Command(
        path=("harness", "diff"), help="Compare two immutable Harness versions.", method="harness_diff",
        arguments=(Argument("id", ("id",), str, "Agent name or agent/name"),
                   Argument("before", ("vA",), int, "Earlier Harness version"),
                   Argument("after", ("vB",), int, "Later Harness version")), mcp_name="harness_diff",
    ),
    Command(
        path=("suite", "ls"), help="List public train metadata for pinned Agent and embodied suites.",
        method="suites_list", output_type=list[dict[str, Any]], mcp_name="suite_list",
    ),
    Command(path=("robot", "ls"), help="Read robot Embodiments and scene bindings; starts no Source.",
            method="robots_list", mcp_name="robot_list", output_type=list[dict[str, Any]]),
    Command(path=("robot", "show"), help="Read an Embodiment, control composition and version history.",
            method="robot_show", mcp_name="robot_show", arguments=(
                Argument("id", ("embodiment",), str, "Embodiment ID, such as fr3-sim"),)),
    Command(path=("robot-model", "ls"), help="Read Robot Model sources, loading support and quality.",
            method="robot_models_list", mcp_name="robot_model_list", output_type=list[dict[str, Any]]),
    Command(path=("robot-model", "show"), help="Read a Robot Model's source, licence and capability.",
            method="robot_model_show", mcp_name="robot_model_show", arguments=(
                Argument("id", ("model",), str, "Robot Model ID"),)),
    Command(path=("controller", "show"), help="Read the explicitly current Controller and retained versions.",
            method="controller_show", mcp_name="controller_show", arguments=(
                Argument("id", ("controller",), str, "Controller ID, such as fr3-arm"),)),
    Command(
        path=("suite", "show"), help="Describe a pinned suite without private seeds or grading.",
        method="suite_show", arguments=(
            Argument("suite", ("suite/name@revision",), str, "Exact suite version"),
        ),
        mcp_name="suite_show",
    ),
    Command(
        path=("eval", "run"), help="Queue a fixed train suite for an existing scene-bound program.",
        method="evaluation_run", arguments=(
            Argument("subject", ("program",), str, "Existing program ID, such as robot_arm.virtual"),
            Argument("suite", ("--suite",), str, "Exact suite/name@revision"),
            Argument("planId", ("--plan",), str | None, "Approved training Plan for Goal accounting", None),
            Argument("planRevision", ("--revision",), int | None, "Expected fixed Plan revision", None),
        ), mcp_name="evaluation_run", actor=True,
    ),
    Command(
        path=("eval", "show"), help="Read evaluation progress, recording IDs and its final score.",
        method="evaluation_show", arguments=(Argument("id", ("id",), str, "Evaluation ID"),),
        mcp_name="evaluation_show",
    ),
    Command(
        path=("eval", "ls"), help="List public train evaluations.", method="evaluations_list",
        output_type=list[dict[str, Any]], mcp_name="evaluation_list",
    ),
    Command(
        path=("eval", "cancel"), help="Cancel a queued or running evaluation and close its owned session.",
        method="evaluation_cancel", arguments=(Argument("id", ("id",), str, "Evaluation ID"),),
        mcp_name="evaluation_cancel", actor=True,
    ),
    Command(
        path=("score", "ls"), help="Read the platform-written train scoreboard for an exact suite.",
        method="score_list", arguments=(
            Argument("suite", ("--suite",), str, "Exact suite/name@revision"),
            Argument("subject", ("--subject",), str | None, "Program ID filter", None),
        ), output_type=list[dict[str, Any]], mcp_name="score_list",
    ),
    Command(
        path=("session-request", "show"), help="Read an explicitly queued interactive launch request.",
        method="session_request_show", arguments=(Argument("id", ("id",), str, "Launch request ID"),),
        mcp_name="session_request_show",
    ),
    Command(
        path=("session-request", "cancel"), help="Cancel an interactive launch that has not started.",
        method="session_request_cancel", arguments=(Argument("id", ("id",), str, "Launch request ID"),),
        mcp_name="session_request_cancel", actor=True,
    ),
    Command(
        path=("inbox",), help="Read attention messages and continue with a persistent opaque cursor.",
        method="inbox_poll", mcp_name="inbox_poll", arguments=(
            Argument("after", ("--since",), str | None, "Cursor from the previous inbox response", None),
            Argument("kinds", ("--kinds",), str | None,
                     "Comma-separated alert, approval, invocation, plan, job", None),
        ),
    ),
    Command(
        path=("history",), help="Query recorded property measurements, with at most 2000 points.",
        method="history_query", mcp_name="history_query", arguments=(
            Argument("key", ("key",), str, "Entity key; /Real/ selects Live Lab"),
            Argument("property", ("property",), str, "Property name"),
            Argument("since", ("--since",), str, "Lookback duration (1h) or raw sampleTimeMs"),
            Argument("until", ("--until",), str | None, "End sampleTimeMs; defaults to recording end", None),
            Argument("every", ("--every",), str | None, "Bucket duration (1m); omit for raw samples", None),
            Argument("agg", ("--agg",), str, "mean, last, min or max", "mean"),
            Argument("recording", ("--recording",), str | None, "Retained recording ID", None),
        ),
    ),
    Command(
        path=("plan", "create"), help="Submit an operation plan; CLI reads a local YAML file.",
        method="plan_create", arguments=(Argument("document", ("plan.yaml",), dict[str, Any],
                                                    "Operation plan content (CLI: local YAML file)"),),
        mcp_name="plan_create", actor=True,
    ),
    Command(
        path=("plan", "revise"), help="Create an immutable plan revision, preserving completed content.",
        method="plan_revise", arguments=(Argument("id", ("id",), str, "Plan ID"),
                                          Argument("document", ("plan.yaml",), dict[str, Any],
                                                   "New operation plan content (CLI: local YAML file)")),
        mcp_name="plan_revise", actor=True,
    ),
    Command(
        path=("plan", "show"), help="Read the plan, step results, bindings and revision differences.",
        method="plan_show", arguments=(Argument("id", ("id",), str, "Plan ID"),
                                        Argument("revision", ("--revision",), int | None,
                                                 "Historical revision", None)),
        mcp_name="plan_show",
    ),
    Command(
        path=("plan", "ls"), help="List plans, optionally filtered by status.", method="plan_list",
        arguments=(Argument("status", ("--status",), str | None, "Plan status", None),),
        output_type=list[dict[str, Any]], mcp_name="plan_list",
    ),
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
        help="Read a bounded scene/asset summary; page details with exact id and documentDigest.",
        method="library_describe",
        arguments=description_arguments("summary"),
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
        arguments=description_arguments("full", "kind/name[@rev]"),
    ),
    Command(
        path=("asset", "import"),
        help="Pack a local asset or scene directory and upload tar bytes to the gateway.",
        method="library_import",
        arguments=(Argument("directory", ("dir",), str, "Directory on this CLI/MCP client's filesystem"),),
        mcp_name="library_import",
        actor=True,
    ),
    Command(
        path=("asset", "validate"),
        help="Recheck an exact immutable asset version and append a new validation report.",
        method="library_validate",
        arguments=(Argument("id", ("kind/name@rev",), str, "Exact asset version to validate"),),
        mcp_name="library_validate",
        actor=True,
    ),
    Command(
        path=("asset", "rm"),
        help="Request human approval to delete an unused exact library version; never approves it.",
        method="library_remove",
        arguments=(Argument("id", ("kind/name@rev",), str, "Exact asset version to request deletion"),),
        mcp_name="library_remove", actor=True,
    ),
    Command(
        path=("scene", "rm"),
        help="Request human approval to delete an unused exact scene version; never approves it.",
        method="library_remove",
        arguments=(Argument("id", ("name@rev",), str, "Exact scene version to request deletion"),),
        actor=True,
    ),
    Command(
        path=("scene", "import"),
        help="Pack a local scene directory and upload tar bytes for immutable import and validation.",
        method="library_import",
        arguments=(Argument("directory", ("dir",), str, "Directory on this CLI/MCP client's filesystem"),),
        actor=True,
    ),
    Command(
        path=("scene", "validate"),
        help="Recheck an exact immutable scene version and append a new validation report.",
        method="library_validate",
        arguments=(Argument("id", ("name@rev",), str, "Exact scene version to validate"),),
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
        arguments=description_arguments("full", "name[@rev]"),
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
            Argument("seed", ("--seed",), int | None, "Seed for a new session; gateway default is 42", None),
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
        path=("alerts",),
        help="List active and recent alert episodes; --active excludes cleared and closed contexts.",
        method="alerts_list",
        arguments=(
            Argument("active", ("--active",), bool, "Only current active episodes", False),
            Argument("state", ("--state",), str | None, "raised, acknowledged or cleared", None),
            Argument("severity", ("--severity",), str | None, "info, warning or critical", None),
        ),
        output_type=list[dict[str, Any]],
        mcp_name="alerts_list",
    ),
    Command(
        path=("alerts", "ack"),
        help="Acknowledge an active alert with an optional note; agents may acknowledge.",
        method="alert_ack",
        arguments=(
            Argument("alertId", ("id",), str, "Alert episode ID"),
            Argument("note", ("--note",), str, "Audit note retained with the first acknowledgement", ""),
        ),
        mcp_name="alert_ack",
        actor=True,
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
    Command(
        path=("vault", "set"),
        help="Save a local credential with hidden interactive input; never accepts values as arguments.",
        method="vault_set",
        arguments=(Argument("name", ("name",), str, "Credential name, such as llm/anthropic"),),
        human_only=True,
    ),
    Command(
        path=("vault", "ls"),
        help="List credential names only; never returns credential values.",
        method="vault_names",
        output_type=list[str],
    ),
    Command(
        path=("model", "ls"),
        help="List configured LLM models and actual available health metadata.",
        method="models",
        arguments=(Argument("query", ("--query", "-q"), str | None, "Filter provider/model_id", None),),
        output_type=list[dict[str, Any]],
        mcp_name="model_ls",
    ),
    Command(
        path=("cost",),
        help="Read recorded GPU usage and price estimates; disconnected LLM usage stays unknown.",
        method="cost_query", mcp_name="cost_query",
        arguments=(
            Argument("since", ("--since",), Literal["1h", "1d", "7d", "30d", "today", "all", "custom"],
                     "Reporting range (UTC)", "7d"),
            Argument("by", ("--by",), str | None,
                     "Group by agent, model, node, goal, plan or job", None),
            Argument("view", ("--view",), Literal["summary", "series", "anomalies"],
                     "Read projection", "summary"),
            Argument("agent", ("--agent",), str | None, "Requesting Agent identity", None),
            Argument("model", ("--model",), str | None, "Model reference", None),
            Argument("node", ("--node",), str | None, "Node identity", None),
            Argument("goal", ("--goal",), str | None, "Pinned Plan goal", None),
            Argument("plan", ("--plan",), str | None, "Pinned Plan identity@revision", None),
            Argument("start", ("--start",), str | None, "Custom start with timezone", None),
            Argument("end", ("--end",), str | None, "Custom end with timezone", None),
        ),
    ),
    Command(
        path=("model", "add"),
        help="Register provider/model_id with a default thinking level and optional Vault name.",
        method="add_model",
        arguments=(
            Argument("ref", ("provider/model_id",), str, "LLM model reference"),
            Argument("default_thinking", ("--thinking",), str, "Default thinking level", "default"),
            Argument("credential_name", ("--credential",), str | None, "Existing Vault name", None),
        ),
    ),
    Command(
        path=("model", "default"),
        help="Select a registered model as the platform default.",
        method="default_model",
        arguments=(Argument("ref", ("provider/model_id",), str, "Registered LLM model reference"),),
    ),
)


def register_cli(commands):
    groups = {}
    parsers = {}
    command_paths = {command.path for command in COMMANDS}
    for command in COMMANDS:
        parent = commands
        for index, name in enumerate(command.path[:-1], 1):
            path = command.path[:index]
            if path not in groups:
                group = parsers.get(path)
                if group is None:
                    group = parent.add_parser(name, help=f"{name} commands")
                    parsers[path] = group
                groups[path] = group.add_subparsers(required=path not in command_paths)
            parent = groups[path]
        parser = parsers.get(command.path)
        if parser is None:
            parser = parent.add_parser(command.path[-1], help=command.help, description=command.help)
            parsers[command.path] = parser
        parser.add_argument("--json", action="store_true", help="machine-readable JSON")
        for argument in command.arguments:
            argument.add_to(parser)
        parser.set_defaults(registered_command=command)
