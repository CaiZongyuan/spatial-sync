"""The installed ``lw`` entrypoint; importing it does not load the MCP SDK."""

import argparse
import asyncio
import json
import math
import sys

from .client import GatewayClient, GatewayError, condition_hint, exit_code
from .commands import register_cli


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def positive(value):
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def parse_value(value):
    def invalid_constant(token):
        raise ValueError(token)

    try:
        return json.loads(value, parse_constant=invalid_constant)
    except ValueError:
        return value


def nonnegative(value):
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return parsed


def parameters(values):
    result = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not key or key in result:
            raise UsageError(f"Expected a unique k=v parameter: {item}")
        result[key] = parse_value(value)
    return result


def parser():
    root = Parser(prog="lw", description="Inspect and control a laboratory gateway.")
    root.add_argument(
        "--json", action="store_true", help="machine-readable JSON (vault set requires hidden terminal input)"
    )
    commands = root.add_subparsers(dest="command", required=True)
    register_cli(commands)
    commands.add_parser("status", help="session, scene, state and simulation time")
    get = commands.add_parser("get", help="read entity properties")
    get.add_argument("key")
    get.add_argument("property", nargs="?")
    action = commands.add_parser("do", help="invoke a generic device action; waits by default")
    action.add_argument("key")
    action.add_argument("action")
    action.add_argument("params", nargs="*")
    waiting = action.add_mutually_exclusive_group()
    waiting.add_argument("--wait", dest="wait", action="store_true", default=True)
    waiting.add_argument("--no-wait", dest="wait", action="store_false")
    action.add_argument("--timeout", type=positive, default=120)
    action.add_argument("--plan", dest="planId")
    action.add_argument("--step", dest="stepId", type=int)
    action.add_argument("--plan-revision", dest="planRevision", type=int)
    invocation = commands.add_parser("inv", help="query an invocation")
    invocation.add_argument("id")
    invocation.add_argument("--wait", action="store_true")
    invocation.add_argument("--timeout", type=positive, default=120)
    wait = commands.add_parser("wait", help="wait for a property condition")
    for name in ("key", "property", "op", "value"):
        wait.add_argument(name)
    wait.add_argument("--timeout", type=positive, default=120)
    check = commands.add_parser("check", help="Assert a Catalog property condition")
    for name in ("key", "property", "op", "value"):
        check.add_argument(name)
    for command_parser in (wait, check):
        command_parser.add_argument("--by", type=nonnegative)
        command_parser.add_argument("--plan", dest="planId")
        command_parser.add_argument("--step", dest="stepId", type=int)
        command_parser.add_argument("--plan-revision", dest="planRevision", type=int)
    watch = commands.add_parser("watch", help="stream observations, events and calls as they arrive")
    watch.add_argument("key", nargs="?")
    session = commands.add_parser("session", help="control the simulation session")
    control = session.add_subparsers(dest="action", required=True)
    for name in ("pause", "resume", "reset"):
        control.add_parser(name)
    step = control.add_parser("step")
    step.add_argument("count", type=int, nargs="?", default=1)
    commands.add_parser("mcp", help="serve generic MCP tools over stdio")
    return root


def human(document):
    if isinstance(document, dict):
        if "invocationId" in document and "status" in document:
            error = document.get("error")
            suffix = f" {error['code']}: {error['message']}" if error else ""
            if hint := condition_hint(document):
                suffix += "\n" + hint
            if document.get("timedOut"):
                suffix += " (wait timed out)"
            return f"{document['invocationId']} {document['status']} {document['action']}{suffix}"
        if "sessionId" in document and "state" in document:
            return (
                f"{document['sessionId']} {document.get('sceneVersion', document.get('sceneId', ''))} "
                f"{document['state']} simTime={document['simTime']:g}s"
            )
        if "ack" in document:
            return f"{document['ack']['status']}\n{human(document['state'])}"
    if isinstance(document, list) and all(isinstance(item, dict) and "key" in item for item in document):
        return "\n".join(f"{item['key']}  {item['type']}" for item in document)
    if isinstance(document, list) and all(
        isinstance(item, dict) and "sceneVersion" in item for item in document
    ):
        return "\n".join(
            f"{item['sceneVersion']}  {item['title']}  "
            f"{','.join(item['sources'])}  {'launchable' if item['launchable'] else 'unavailable'}"
            for item in document
        )
    return document if isinstance(document, str) else json.dumps(document, ensure_ascii=False, indent=2)


def output(document, machine):
    print(
        json.dumps(document, ensure_ascii=False, allow_nan=False) if machine else human(document), flush=True
    )


async def run(args, machine):
    declaration = getattr(args, "registered_command", None)
    if declaration and declaration.method in ("skills_list", "skills_install"):
        from .skills import install_skills, list_skills

        result = list_skills() if declaration.method == "skills_list" else install_skills(args.target)
        output(result, machine)
        return 0
    if declaration and declaration.method == "vault_set":
        import getpass
        import warnings

        from packages.vault import Vault, valid_name

        if machine or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise GatewayError(
                "Vault credentials require an interactive terminal without --json",
                detail={"code": "vault_requires_human"},
            )
        valid_name(args.name)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("Credential value (hidden): ")
        except (EOFError, getpass.GetPassWarning):
            raise GatewayError("A private terminal is required for credential input") from None
        Vault().set(args.name, value)
        print("Credential saved in the local Vault.", flush=True)
        return 0
    async with GatewayClient() as client:
        command = args.command
        if command == "mcp":
            from .mcp_server import serve

            await serve(client)
            return 0
        if command == "watch":
            async for message in client.watch(args.key):
                output(message, machine)
            return 0
        if declaration := getattr(args, "registered_command", None):
            if declaration.method == "approve_invocation":
                if machine or not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise GatewayError(
                        "approval_requires_human: use an interactive terminal without --json",
                        status_code=403,
                        detail={"code": "approval_requires_human"},
                    )
                preview = await client.describe_approval(args.invocation_id)
                print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
                decision = "deny" if args.deny else "approve"
                try:
                    confirmation = input(f'Type "{decision}" to confirm this decision: ')
                except EOFError:
                    confirmation = ""
                if confirmation.strip() != decision:
                    raise GatewayError("Approval cancelled; no decision was sent")
            result = await declaration.invoke(client, vars(args))
        elif command == "status":
            result = await client.status()
        elif command == "get":
            entity = (await client.get_state([args.key]))["entities"][args.key]
            if args.property is not None and args.property not in entity["values"]:
                raise GatewayError("Unknown property", detail={"key": args.key, "property": args.property})
            result = entity["values"][args.property] if args.property is not None else entity["values"]
        elif command == "do":
            result = await client.invoke_action(
                args.key,
                args.action,
                parameters(args.params),
                wait=args.wait,
                timeout=args.timeout,
                planId=args.planId, stepId=args.stepId, planRevision=args.planRevision,
            )
        elif command == "inv":
            result = await client.get_invocation(args.id, wait=args.wait, timeout=args.timeout)
        elif command in ("wait", "check"):
            operation = client.wait_until if command == "wait" else client.check_condition
            result = await operation(
                args.key,
                args.property,
                args.op,
                parse_value(args.value),
                **({"timeout": args.timeout} if command == "wait" else {}),
                planId=args.planId, stepId=args.stepId, planRevision=args.planRevision,
                by=args.by,
            )
        elif command == "session":
            result = await client.session_control(args.action, count=getattr(args, "count", 1))
        output(result, machine)
        return exit_code(result) if declaration or command in ("do", "inv", "wait", "check", "session") else 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    machine = "--json" in argv
    argv = [argument for argument in argv if argument != "--json"]
    try:
        return asyncio.run(run(parser().parse_args(argv), machine))
    except KeyboardInterrupt:
        return 0
    except (UsageError, GatewayError, ValueError) as exc:
        if isinstance(exc, UsageError) and argv[:2] == ["vault", "set"]:
            exc = UsageError(
                "Use lw vault set <name>; enter the credential only in the hidden terminal prompt"
            )
        if machine:
            output(
                exc.document() if isinstance(exc, GatewayError) else {"error": {"message": str(exc)}}, True
            )
        else:
            detail = f": {exc.detail}" if isinstance(exc, GatewayError) and exc.detail else ""
            print(f"lw: {exc}{detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
