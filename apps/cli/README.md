# CLI and MCP commands

`lw` uses `argparse`; `lw mcp` uses the pinned official Python MCP SDK. Both call
the HTTP API through `lw.client.GatewayClient`.

Add a shared command to `COMMANDS` in `lw/commands.py`. Declare its CLI path,
help, arguments, GatewayClient method and output type once. Set `mcp_name` to
publish its MCP tool; leave it unset for a CLI-only command. Paths can contain
multiple words, such as `("rec", "ls")`.

An argument's `name`, `annotation` and `default` define its MCP parameter. Its
`flags` define the CLI spelling; `client_name` handles an existing HTTP-client
parameter with a different name. String and numeric arguments use their Python
types; boolean options are switches. The HTTP client owns request serialization
and service errors.

Set `actor=True` for methods accepting an `actor` keyword: MCP sends the actual
client identity, and CLI retains `cli:lw`. Set `human_only=True` for approvals
and other human operations; this always excludes the command from MCP even if
`mcp_name` is present. This flag controls exposure, while the HTTP API still owns
authorization.

Every generated command accepts `--json` before or after the command, uses the
existing human renderer otherwise, and returns the information specification's
exit codes: 0 success, 2 rejection, 3 failure/cancellation, 4 wait timeout, or 1
usage/connection error. MCP derives input validation and structured output from
the declared types; invocation results retain rejection hints.

The migrated commands are `ls`, `show`, `cancel`, `approve` and `rec ls`. Other
existing commands retain their adapters, including CLI property extraction,
action parameters, waits and simulation subcommands. The eight published MCP
tool names and schemas remain unchanged. Verify changes with
`uv run --no-sync python -m pytest tests/cli -q`; these tests use the installed
`lw` executable, actual HTTP handlers and official SDK/stdio clients.
