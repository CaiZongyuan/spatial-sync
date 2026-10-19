"""Check Markdown CLI examples against the actual parser without executing commands."""

import argparse
import contextlib
import io
import re
import shlex
from pathlib import Path

from .__main__ import UsageError, parser


def examples(text):
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            command = re.sub(r"^[$>]\s+", "", stripped)
            if re.match(r"^lw[ \t]+", command):
                yield number, command
            elif re.search(r"\blw[ \t]+", command):
                raise ValueError(f"Line {number}: put one complete lw invocation on each code line")
        else:
            for fragment in re.findall(r"`([^`\n]+)`", line):
                if re.match(r"^lw[ \t]+", fragment):
                    yield number, fragment
                elif re.search(r"\blw[ \t]+", fragment):
                    raise ValueError(f"Line {number}: put one lw invocation in each code span")
            if re.match(r"^\s*(?:[-*]\s+)?lw\s+", line):
                yield number, re.sub(r"^\s*(?:[-*]\s+)?", "", line)


def check_command(command):
    tokens = shlex.split(command, comments=True)
    if not tokens or tokens[0] != "lw":
        raise ValueError("Expected an lw command")
    arguments = [token for token in tokens[1:] if token != "--json"]
    help_requested = any(token in ("--help", "-h") for token in arguments)
    path_tokens = [token for token in arguments if token not in ("--help", "-h")]
    root = parser()
    current = root
    selected = [root]
    # Check subcommands before argparse's help action can short-circuit validation.
    while subcommands := next(
        (action for action in current._actions if isinstance(action, argparse._SubParsersAction)), None
    ):
        if not path_tokens:
            if help_requested:
                break
            raise ValueError("A subcommand is required")
        name = path_tokens.pop(0)
        if name not in subcommands.choices:
            raise ValueError(f"Unknown command or subcommand: {name}")
        current = subcommands.choices[name]
        selected.append(current)
    if help_requested:
        # Help may omit operands, but cannot bypass validation of supplied arguments.
        arguments = [token for token in arguments if token not in ("--help", "-h")]
        for command_parser in selected:
            for action in command_parser._actions:
                action.required = False
                if not action.option_strings and action.nargs is None:
                    action.nargs = "?"
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root.parse_args(arguments)
    except UsageError as error:
        raise ValueError(str(error)) from None
    except SystemExit as error:
        if error.code != 0:
            raise ValueError("Invalid command arguments") from None


def check_skills(root):
    documents = sorted(Path(root).glob("*/SKILL.md"))
    if not documents:
        raise ValueError("No product SKILL.md files found")
    count = 0
    for document in documents:
        try:
            commands = list(examples(document.read_text(encoding="utf-8")))
        except ValueError as error:
            raise ValueError(f"{document.parent.name}/SKILL.md: {error}") from None
        for line, command in commands:
            try:
                check_command(command)
            except ValueError as error:
                raise ValueError(f"{document.parent.name}/SKILL.md:{line}: {error}") from None
            count += 1
    if not count:
        raise ValueError("No documented lw commands found")
    return {"skills": len(documents), "commands": count}
