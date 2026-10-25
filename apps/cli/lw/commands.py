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
            literal = next((kind for kind in kinds if get_origin(kind) is Literal), None)
            if literal is not None:
                options["choices"] = get_args(literal)
                options["type"] = type(get_args(literal)[0])
            else:
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
    Command(
        path=("suite", "push"),
        help="Publish an explicitly selected Agent Suite directory.",
        method="suite_push",
        mcp_name="suite_push",
        actor=True,
        arguments=(Argument("directory", ("dir",), str, "Selected confined Suite directory"),),
    ),
    Command(
        path=("agent", "rsi"), help="Read measured baselines, Agent rounds and file recovery.",
        method="agent_rsi", mcp_name="agent_rsi",
        arguments=(Argument("id", ("agent",), str, "Agent name or agent/name"),),
    ),
    Command(
        path=("agent", "harness"),
        help="Read exact confined Harness text and locked skills.",
        method="agent_harness",
        mcp_name="agent_harness",
        arguments=(
            Argument("id", ("agent",), str, "Agent name or agent/name"),
            Argument("version", ("version",), int, "Immutable Harness version"),
        ),
    ),
    Command(
        path=("agent", "baseline"),
        help="Establish a measured Reference from complete train evidence.",
        method="agent_baseline",
        mcp_name="agent_baseline",
        actor=True,
        arguments=(
            Argument("id", ("agent",), str, "Agent name or agent/name"),
            Argument("evaluationId", ("--evaluation",), str, "Exact complete train evaluation"),
            Argument("requestId", ("--request-id",), str | None, "Stable exact retry identity", None),
        ),
    ),
    *(
        Command(
            path=("agent", verb),
            help=help_text,
            method=method,
            mcp_name=method,
            human_only=human,
            actor=True,
            arguments=(
                Argument("id", ("agent",), str, "Agent name or agent/name"),
                Argument("document", ("document.yaml",), dict[str, Any], "Structured request or local YAML"),
            ),
        )
        for verb, method, help_text, human in (
            (
                "candidate",
                "agent_candidate",
                "Stage a bounded candidate without editing current files.",
                False,
            ),
            (
                "optimizer-grant",
                "agent_optimizer_grant",
                "Issue scoped Optimizer credentials to its controller.",
                True,
            ),
        )
    ),
    Command(
        path=("agent", "tool-trace"),
        help="Read public Optimizer interface/status audit without payloads.",
        method="agent_tool_trace",
        mcp_name="agent_tool_trace",
        arguments=(
            Argument("id", ("agent",), str, "Agent name or agent/name"),
            Argument("goal", ("--goal",), str, "Exact Goal"),
        ),
    ),
    *(
        Command(
            path=("agent-round", verb),
            help=help_text,
            method=method,
            mcp_name=method,
            actor=True,
            arguments=(
                Argument("id", ("plan",), str, "Approved Agent Plan"),
                Argument("revision", ("--revision",), int, "Exact approved revision"),
                *(
                    (
                        Argument(
                            "requestId", ("--request-id",), str | None, "Stable exact retry identity", None
                        ),
                    )
                    if verb == "start"
                    else ()
                ),
            ),
        )
        for verb, method, help_text in (
            (
                "start",
                "agent_round_start",
                "Activate exact approved files and dispatch the shared evaluator.",
            ),
            (
                "settle",
                "agent_round_settle",
                "Decide from platform evidence and retain or restore real files.",
            ),
        )
    ),
    Command(
        path=("agent-round", "show"),
        help="Read the durable Agent Plan and round.",
        method="plan_show",
        arguments=(
            Argument("id", ("plan",), str, "Agent Plan"),
            Argument("revision", ("--revision",), int | None, "Exact revision", None),
        ),
    ),
    Command(
        path=("agent-round", "contamination"),
        help="Report a fixed Optimizer native exposure event.",
        method="agent_contamination",
        mcp_name="agent_contamination",
        actor=True,
        arguments=(
            Argument("id", ("plan",), str, "Exact exposed Agent Plan"),
            Argument(
                "document",
                ("evidence.yaml",),
                dict[str, Any],
                "Fixed revision, request and native event hashes",
            ),
        ),
    ),
    Command(
        path=("agent-round", "capture"),
        help="Retain late native usage privately after contamination stops work.",
        method="agent_contamination_capture", mcp_name="agent_contamination_capture",
        human_only=True, actor=True,
        arguments=(
            Argument("id", ("plan",), str, "Stopped Agent Plan"),
            Argument("records", ("records.json",), list[dict[str, Any]],
                     "Native record array for this stopped Optimizer"),
            Argument("format", ("--format",), Literal["claude-code-stream-json", "claude-code-session-jsonl"],
                     "Native capture format", "claude-code-stream-json"),
        ),
    ),
    Command(
        path=("agent-promotion", "request"),
        help="Request bounded private evaluation after train acceptance.",
        method="agent_promotion_request",
        mcp_name="agent_promotion_request",
        actor=True,
        arguments=(
            Argument("id", ("plan",), str, "Accepted Agent Plan"),
            Argument("revision", ("--revision",), int, "Exact accepted revision"),
            Argument("requestId", ("--request-id",), str | None, "Stable exact retry identity", None),
        ),
    ),
    *(
        Command(
            path=("agent-promotion", verb),
            help=help_text,
            method=method,
            mcp_name=method,
            human_only=human,
            actor=write,
            arguments=(Argument("id", ("promotion",), str, "Exact promotion identity"),),
        )
        for verb, method, help_text, human, write in (
            (
                "show",
                "agent_promotion_show",
                "Read safe promotion status without test evidence.",
                False,
                False,
            ),
            ("review", "agent_promotion_review", "Read the private human promotion card.", True, False),
            (
                "next",
                "agent_promotion_next",
                "Claim opaque pending-native work, then bound private Target work.",
                False,
                True,
            ),
            ("cancel", "agent_promotion_cancel", "Cancel only the promotion's owned work.", False, True),
            (
                "cleanup",
                "agent_promotion_cleanup",
                "Retry the promotion's retained cleanup blocks.",
                False,
                True,
            ),
        )
    ),
    Command(
        path=("agent-promotion", "controller-grant"),
        help="Issue a separate private-controller capability.",
        method="agent_promotion_grant",
        mcp_name="agent_promotion_grant",
        human_only=True,
        actor=True,
        arguments=(
            Argument("id", ("promotion",), str, "Exact promotion identity"),
            Argument("wallSeconds", ("--wall-seconds",), int, "Finite permission duration", 900),
        ),
    ),
    *(
        Command(
            path=("agent-promotion", verb),
            help=help_text,
            method=method,
            mcp_name=method,
            actor=True,
            arguments=(
                Argument("id", ("promotion",), str, "Exact promotion identity"),
                Argument("caseRunId", ("--case-run",), str, "Opaque private work identity"),
                Argument("nativeSessionId", ("--native-session",), str, "Fresh bound Target conversation"),
                *(
                    (
                        Argument(
                            "runtimeRecordId",
                            ("--runtime-record",),
                            str | None,
                            "Private imported native record",
                            None,
                        ),
                    )
                    if verb == "complete"
                    else ()
                ),
            ),
        )
        for verb, method, help_text in (
            (
                "bind-native",
                "agent_promotion_bind",
                "Bind a fresh native conversation before receiving private input.",
            ),
            ("complete", "agent_promotion_complete", "Signal completion of this exact bound private Target."),
        )
    ),
    Command(
        path=("agent-promotion", "capture"),
        help="Import the bound private native capture with trusted ownership.",
        method="agent_promotion_capture",
        mcp_name="agent_promotion_capture",
        actor=True,
        arguments=(
            Argument("id", ("promotion",), str, "Exact promotion identity"),
            Argument(
                "records",
                ("records.json",),
                list[dict[str, Any]],
                "Structured native records or local JSON array",
            ),
            Argument(
                "format",
                ("--format",),
                Literal["claude-code-stream-json", "claude-code-session-jsonl"],
                "Native capture format",
                "claude-code-stream-json",
            ),
        ),
    ),
    Command(path=("policy", "create"), help="Freeze a validated Job export as a policy candidate.",
            method="policy_create", mcp_name="policy_create", actor=True, arguments=(
                Argument("checkpoint", ("job/checkpoint",), str, "Job ID/checkpoint relative path"),
            )),
    Command(path=("policy", "ls"), help="List immutable policy candidates and missing quality evidence.",
            method="policy_list", mcp_name="policy_list", output_type=list[dict[str, Any]]),
    Command(path=("policy", "show"), help="Read an exact policy version and its frozen provenance.",
            method="policy_show", mcp_name="policy_show", arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact immutable policy version"),
            )),
    Command(path=("policy", "promote"), help="Request private tests and human deployment review.",
            method="policy_promote", mcp_name="policy_promote", actor=True, arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact accepted immutable policy"),
                Argument("scene", ("--scene",), str, "Exact fixed Scene name@revision"),
                Argument("evaluation", ("--evaluation",), str | None,
                         "Exact current accepted train row", None),
                Argument("observation_check", ("--observation-check",), str | None,
                         "Trusted native observation check", None),
                Argument("plan", ("--plan",), str | None, "Current approved funding Plan", None),
                Argument("revision", ("--revision",), int | None, "Exact funding Plan revision", None),
                Argument("request_id", ("--request-id",), str | None, "Retry identity", None),
            )),
    Command(path=("promotion", "show"), help="Read public Controller promotion progress.",
            method="controller_promotion_show", mcp_name="controller_promotion_show", arguments=(
                Argument("id", ("promotion-id",), str, "Controller promotion ID"),
            )),
    Command(path=("policy", "check-observations"), help="Freeze source-Job samples and a numerical check.",
            method="policy_check_observations", mcp_name="policy_check_observations", actor=True, arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact immutable policy version"),
                Argument("artifact", ("--artifact",), str, "Existing source-Job artifact path"),
            )),
    Command(path=("policy", "observation-checks"), help="List numerical reports without qualifying a policy.",
            method="policy_observation_checks", mcp_name="policy_observation_checks",
            output_type=list[dict[str, Any]], arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact immutable policy version"),
            )),
    Command(path=("policy", "observation-check"), help="Read a frozen observation report and source status.",
            method="policy_observation_check_show", mcp_name="policy_observation_check_show", arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact immutable policy version"),
                Argument("check", ("check-id",), str, "Immutable observation check ID"),
            )),
    Command(path=("policy", "verify-observation-source"), help="Verify an existing native capture receipt.",
            method="policy_observation_check_verify_source",
            mcp_name="policy_observation_check_verify_source",
            actor=True, arguments=(
                Argument("id", ("policy/name@revision",), str, "Exact immutable policy version"),
                Argument("check", ("check-id",), str, "Immutable observation check ID"),
                Argument("artifact", ("--artifact",), str, "Existing source-Job capture receipt artifact"),
            )),
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
    Command(path=("agent-run", "create"),
            help="Freeze a finite public evaluation for an external runtime; no runtime is launched.",
            method="agent_run_create", mcp_name="agent_run_create", actor=True, arguments=(
                Argument("id", ("agent",), str, "Agent name or agent/name"),
                Argument("suite", ("--suite",), str, "Exact public Agent suite/name@revision"),
                Argument("harnessVersion", ("--harness-version",), int, "Immutable Harness version"),
                Argument("runtime", ("--runtime",), str, "Declared external runtime name"),
                Argument("model", ("--model",), str, "Declared runtime model; not a platform model proxy"),
                Argument("thinking", ("--thinking",), str, "Declared runtime thinking level"),
                Argument("runtimeVersion", ("--runtime-version",), str | None,
                         "Declared runtime version", None),
                Argument("purpose", ("--purpose",), Literal["train", "verification"],
                         "Full public train scope or one-case verification without a scoreboard", "train"),
                Argument("caseId", ("--case",), str | None, "Public case ID for verification only", None),
                Argument("wallSeconds", ("--wall-seconds",), int,
                         "Finite platform wall deadline, 1–7200 seconds", 900),
                Argument("requestId", ("--request-id",), str | None, "Stable exact retry identity", None),
            )),
    Command(path=("agent-run", "ls"), help="List retained external runtime evaluations.",
            method="agent_run_list", mcp_name="agent_run_list", output_type=list[dict[str, Any]], arguments=(
                Argument("agent", ("--agent",), str | None, "Agent identity filter", None),
            )),
    Command(path=("agent-run", "show"), help="Read frozen inputs, trusted case evidence and reported scores.",
            method="agent_run_show", mcp_name="agent_run_show", arguments=(
                Argument("id", ("run",), str, "Agent run ID"),
            )),
    Command(path=("agent-run", "next"),
            help="Hand the controller public work and a secret case-scoped Target connection.",
            method="agent_run_next", mcp_name="agent_run_next", actor=True, arguments=(
                Argument("id", ("run",), str, "Agent run ID; retries return the same active work"),
            )),
    Command(path=("agent-run", "complete"),
            help="Signal external completion; the platform observes and grades its own Session.",
            method="agent_run_complete", mcp_name="agent_run_complete", actor=True, arguments=(
                Argument("id", ("run",), str, "Agent run ID"),
                Argument("caseRunId", ("--case-run",), str, "Exact work identity"),
                Argument("nativeSessionId", ("--native-session",), str,
                         "Fresh native Target conversation identity"),
                Argument("runtimeRecordId", ("--runtime-record",), str | None,
                         "Explicit imported native record ID", None),
            )),
    Command(path=("agent-run", "cancel"),
            help="Revoke Target grants and close the platform's owned Session; signal external cancellation.",
            method="agent_run_cancel", mcp_name="agent_run_cancel", actor=True, arguments=(
                Argument("id", ("run",), str, "Agent run ID"),
            )),
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
        path=("catalog", "search"),
        help=(
            "Search local assets and online catalog candidates in one bounded response; source "
            "sizes are bytes and dimensions are metres. scope is all|local|online, usage is "
            "dressing|staticCollision, license is admitted|not_admitted, sort is "
            "relevance|name|sourceBytes, and limit is 1..50 (default 10). cursor continues the "
            "next page. Natural-language use queries may add CPU semantic recall after keyword "
            "matching misses; hard filters and exact IDs are never relaxed. Unknown attributes "
            "never satisfy a hard filter."
        ),
        method="catalog_search",
        arguments=(
            Argument("query", ("--query", "-q"), str | None,
                     "Text found in names, aliases, tags or descriptions (Chinese or English)", None),
            Argument("scope", ("--scope",), Literal["all", "local", "online"],
                     "Search local assets, online candidates, or both", "all"),
            Argument("kind", ("--kind",), str | None,
                     "Exact asset kind, such as environment, device or labware", None),
            Argument("provider", ("--provider",), str | None, "Exact catalog provider", None),
            Argument("usage", ("--usage",), Literal["dressing", "staticCollision"] | None,
                     "Only candidates eligible for dressing or staticCollision", None),
            Argument("license", ("--license",), Literal["admitted", "not_admitted"] | None,
                     "License admission; sent as licenseAdmitted=true|false", None),
            Argument("installed", ("--installed",), bool,
                     "Only candidates with a correlated installed local version", False),
            Argument("notInstalled", ("--not-installed",), bool,
                     "Only candidates without a correlated installed local version", False),
            Argument("minSourceBytes", ("--min-source-bytes",), int | None,
                     "Minimum source package size in bytes", None),
            Argument("maxSourceBytes", ("--max-source-bytes",), int | None,
                     "Maximum source package size in bytes", None),
            Argument("minDimensionM", ("--min-dimension-m",), float | None,
                     "Minimum largest bounding-box extent in metres", None),
            Argument("maxDimensionM", ("--max-dimension-m",), float | None,
                     "Maximum largest bounding-box extent in metres", None),
            Argument("approximate", ("--approximate",), bool,
                     "Append query matches that fail or have unknown hard filters", False),
            Argument("sort", ("--sort",), Literal["relevance", "name", "sourceBytes"],
                     "Exact-match ordering; approximate results follow", "relevance"),
            Argument("limit", ("--limit",), int, "Page size from 1 to 50; default 10", 10),
            Argument("cursor", ("--cursor",), str | None,
                     "Opaque nextCursor from the previous page; limit may change", None),
        ),
        mcp_name="catalog_search",
    ),
    Command(
        path=("catalog", "show"),
        help=(
            "Read one catalog entry by provider/package@revision or catalog: ref; summary is "
            "bounded and full is the complete catalog document. A documentDigest mismatch fails "
            "with catalog_changed."
        ),
        method="catalog_show",
        arguments=(
            Argument("id", ("provider/package@revision",), str,
                     "Catalog entry ID, with or without the catalog: prefix"),
            Argument("view", ("--view",), Literal["summary", "full"],
                     "Bounded summary or complete catalog document", "summary"),
            Argument("documentDigest", ("--document-digest",), str | None,
                     "Digest from a summary; restart on catalog_changed", None),
        ),
        mcp_name="catalog_show",
    ),
    Command(
        path=("catalog", "estimate"),
        help="Estimate source transfer and required disk for a catalog entry; no download is made.",
        method="catalog_estimate",
        arguments=(
            Argument("id", ("provider/package@revision",), str,
                     "Catalog entry ID, with or without the catalog: prefix"),
        ),
        mcp_name="catalog_estimate",
    ),
    Command(
        path=("acquisition", "start"),
        help="Acquire one catalog entry; --wait polls the same record until it finishes (bounded).",
        method="acquisition_start",
        arguments=(
            Argument("ref", ("provider/package@revision",), str,
                     "Catalog entry ID, with or without the catalog: prefix"),
            Argument("wait", ("--wait",), bool,
                     "Poll until succeeded, failed or cancelled, at most 120 seconds", False),
        ),
        mcp_name="acquisition_start",
    ),
    Command(
        path=("acquisition", "ls"),
        help="List real acquisition records newest first; each record keeps its own attempt and usage.",
        method="acquisition_list",
        arguments=(
            Argument("status", ("--status",),
                     Literal["queued", "downloading", "preparing", "validating", "importing",
                             "succeeded", "failed", "cancelled"] | None,
                     "Only records in this lifecycle status", None),
        ),
        mcp_name="acquisition_list",
    ),
    Command(
        path=("acquisition", "show"),
        help="Read one acquisition record: status, phase, progress, usage, result or failure.",
        method="acquisition_show",
        arguments=(Argument("id", ("id",), str, "Acquisition ID, such as acq-<32 hex>"),),
        mcp_name="acquisition_show",
    ),
    Command(
        path=("acquisition", "cancel"),
        help="Request cancellation; a published version is never deleted.",
        method="acquisition_cancel",
        arguments=(Argument("id", ("id",), str, "Acquisition ID, such as acq-<32 hex>"),),
        mcp_name="acquisition_cancel",
        actor=True,
    ),
    Command(
        path=("acquisition", "retry"),
        help="Retry a failed or cancelled acquisition with the same ID and a new attempt.",
        method="acquisition_retry",
        arguments=(Argument("id", ("id",), str, "Failed or cancelled acquisition ID"),),
        mcp_name="acquisition_retry",
        actor=True,
    ),
    Command(
        path=("authoring", "submit"),
        help=(
            "Submit one external Authoring Report from a JSON/YAML file; submissionId is the "
            "idempotency key and no attachment or file path is read by the gateway."
        ),
        method="authoring_submit",
        arguments=(
            Argument("document", ("json/file",), dict[str, Any],
                     "JSON document with submissionId, source, sourceVersion, reportedAt and a "
                     "draftId or exact object version"),
        ),
        mcp_name="authoring_submit",
        actor=True,
    ),
    Command(
        path=("authoring", "ls"),
        help="List received external reports newest first; summaries are bounded previews.",
        method="authoring_list",
        arguments=(
            Argument("scene", ("--scene",), str | None,
                     "Exact scene version name@rev, or a scene name", None),
            Argument("asset", ("--asset",), str | None,
                     "Exact asset version kind/name@rev, or an asset ID", None),
            Argument("draft", ("--draft",), str | None, "External draft ID", None),
            Argument("limit", ("--limit",), int, "Page size from 1 to 100; default 20", 20),
        ),
        mcp_name="authoring_list",
    ),
    Command(
        path=("authoring", "show"),
        help="Read one received external report: source, times, binding, summary, gaps and references.",
        method="authoring_show",
        arguments=(Argument("id", ("id",), str, "Stable submissionId"),),
        mcp_name="authoring_show",
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
