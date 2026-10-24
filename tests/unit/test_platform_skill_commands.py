"""The actual checker validates subcommands and arguments without performing lab actions."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from lw.__main__ import UsageError, parser

ROOT = Path(__file__).resolve().parents[2]


def check(root=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_platform_skills.py"), *([str(root)] if root else [])],
        cwd=ROOT,
        env=os.environ | {"LW_URL": "http://[broken"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def test_every_shipped_command_matches_the_real_parser_without_a_gateway():
    result = check()
    assert result.returncode == 0, result.stdout + result.stderr
    checked = json.loads(result.stdout)
    assert checked["skills"] == 8
    assert checked["commands"] > 0


@pytest.mark.parametrize(
    "command",
    [
        "lw scene laucnh example@1 --json",
        "lw session nonexistent",
        "lw scene nonexistent --help",
        "lw --help imaginary",
        "lw skills install --targte nowhere",
        "lw skills install --targte nowhere --help",
        "lw skills install --help --targte nowhere",
        "lw skills install --target --help",
        "lw scene launch demo@1 --sorce synthetic --help",
        "lw approve --ntoe note --help",
        "lw scene launch",
        "lw do",
        "lw session step not-a-number",
        "lw session step not-a-number --help",
        "lw do --timeout not-a-number --help",
        "lw do --wait --no-wait --help",
    ],
)
def test_checker_detects_real_subcommand_and_parameter_typos(tmp_path, command):
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        f"# Example\nValid: `lw status --json`\n\n```text\n{command}\n```\n", encoding="utf-8"
    )
    result = check(tmp_path)
    assert result.returncode == 1
    assert "example/SKILL.md:" in result.stderr


@pytest.mark.parametrize("command", [
    "lw --help", "lw scene --help", "lw do --help", "lw scene launch --help", "lw approve --help",
    "lw skills install -h", "lw approve --note audit --help",
])
def test_checker_accepts_real_help_without_executing_commands_or_requiring_operands(tmp_path, command):
    skill = tmp_path / "help-example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"`{command}`\n", encoding="utf-8")
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("command", ["lw session step -- --help", "lw do -- --help"])
def test_literal_help_after_end_of_options_keeps_actual_types_and_required_operands(tmp_path, command):
    with pytest.raises(UsageError):
        parser().parse_args(shlex.split(command)[1:])
    skill = tmp_path / "literal-help"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"```text\n{command}\n```\n", encoding="utf-8")
    result = check(tmp_path)
    assert result.returncode == 1
    assert "literal-help/SKILL.md:2:" in result.stderr


@pytest.mark.parametrize("command", [
    'lw skills install --target="--help"',
    'lw do /Thing action "message=--help"',
    'lw do /Thing action "message=quoted --help -- text"',
    "lw do -- --help action",
    "lw approve --note=-- --help",
    "lw skills install --target=-- --help",
])
def test_checker_retains_literal_values_and_real_help_in_the_actual_parser(tmp_path, command):
    try:
        parser().parse_args(shlex.split(command)[1:])
    except SystemExit as error:
        assert error.code == 0
    skill = tmp_path / "literal-values"
    skill.mkdir()
    (skill / "SKILL.md").write_text(f"```text\n{command}\n```\n", encoding="utf-8")
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_reads_inline_bare_and_prompt_examples_and_never_executes_them(tmp_path):
    skill = tmp_path / "example"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "`lw status --json`\nlw session reset\n```bash\n$ lw do /Fake unused action=true --json\n```\n",
        encoding="utf-8",
    )
    result = check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"commands": 3' in result.stdout
