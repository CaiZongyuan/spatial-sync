# CLI and MCP commands

`lw` uses `argparse`; `lw mcp` uses the pinned official Python MCP SDK. Device
operations call the HTTP API through `lw.client.GatewayClient`. Product skill
listing/installation are local file operations dispatched before creating that client.

`lw vault set` is also local: it accepts only hidden input in a real terminal and
writes the private local Vault before constructing an HTTP client. Metadata
commands `model ls/add/default` and `vault ls` use the shared command table and
HTTP client. MCP exposes `model_ls` only; there is no Vault MCP tool.

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

`skills ls` and `skills install [--target DIR]` are declared in the same table,
handled locally, and have no MCP tool registration. Their help and JSON output
work without a gateway. See [product platform skills](../../skills/README.md)
for the default directory, repeat installation and conflict behavior.

Every generated command accepts `--json` before or after the command, uses the
existing human renderer otherwise, and returns the information specification's
exit codes: 0 success, 2 rejection, 3 failure/cancellation, 4 wait timeout, or 1
usage/connection error. MCP derives input validation and structured output from
the declared types; invocation results retain rejection hints.

Scene discovery and launch use the same table:

```text
lw scene ls [--query TEXT] [--launchable] --json
lw scene show <name[@rev]> --json
lw scene launch <name[@rev]> [--source synthetic|newton] --json
```

Their generic MCP mirrors are `library_search`, `library_describe` and
`scene_launch`. They work before any session exists. Launch chooses the latest
launchable version when no revision is supplied, while show reads the latest
version, including validation failures. Launch defaults to a supported package
source and switches subsequent MCP device calls to the new active session.

Fixed embodied evaluations use `lw eval run <program> --suite suite/name@revision`.
The command returns an evaluation ID; inspect it with `lw eval show <id>` and read
the platform-written results with `lw score ls --suite <suite>`. `lw suite ls`
and `lw suite show` expose public train metadata only. During an evaluation,
`scene launch` can return a queued `requestId`; use `lw session-request show|cancel`
to inspect or cancel it. It does not become the active CLI session until it starts.
See [embodied evaluations](../../docs/EVALUATIONS.md) for package format, privacy,
queue behavior and the GPU-free example.

The migrated legacy commands are `ls`, `show`, `cancel`, `approve` and `rec ls`. Other
existing commands retain their adapters, including CLI property extraction,
action parameters, waits and simulation subcommands. The eight published MCP
tool names and schemas remain unchanged alongside the three library tools. Verify changes with
`uv run --no-sync python -m pytest tests/cli -q`; these tests use the installed
`lw` executable, actual HTTP handlers and official SDK/stdio clients.

Property history reads the active session's recording, or an explicit retained recording:

```text
lw history /World/Lab/Incubator temperatureC --since 1h --every 1m --agg mean --json
lw history /World/Lab/Centrifuge lidOpen --since 1h --agg last --recording ID --json
```

`history_query` is the generic MCP mirror with the same parameters. `/Real/` keys
select Live Lab; they return `live_unavailable` until its host is installed.
`LW_SESSION` selects an active session explicitly, while `--recording` takes precedence.

History uses the recording's original monotonic `sampleTimeMs`, not UNIX time or
simulation seconds. `--since` accepts a positive duration (`ms`, `s`, `m`, `h`, `d`)
or a numeric sample timestamp; `--until` accepts a numeric sample timestamp and
defaults to the recording's latest checkpoint. Relative `--since` counts backward
from that end, including for old recordings. Omit `--every` for raw measurements.
Buckets are half-open from `since`, `until` is inclusive, and each bucket uses its
last actual sample timestamp. Reset and reload contexts stay separate. Repeated
roll snapshots and stale transitions do not add measurements; missing snapshot
property timestamps are unknown and omitted. A stale snapshot with a known sample
timestamp retains that original measurement. `last` retains any JSON property value;
`mean`, `min` and `max` require numbers when aggregating.

`GET /api/v1/history` returns `[[tMs, value], ...]`. Its `X-History-*` headers name
the recording, effective range, interval (`raw` when omitted), aggregation, limit
and truncation. More than 2,000 resulting points returns the first 2,000 with an
explanation to narrow the window or increase `every`. CLI and MCP expose these
headers once as `{points, recordingId, sinceMs, untilMs, everyMs, agg, limit,
truncated, message}` so both machine and human output keep the explanation.
Closed segments can have optional verified `seg-NNNNNN.index.json` sidecars;
missing, stale or corrupt indexes fall back to sequential reads, including formats1/2.

Read attention messages at the start of a work round or after a long wait:

```text
lw inbox --json
lw inbox --since CURSOR --kinds alert,approval --json
```

The generic MCP mirror is `inbox_poll(after?, kinds?)`; both use
`GET /api/v1/inbox?after=&kinds=`. The response has `alerts`, `approvals`,
`messages` and `cursor`. The first read includes all current active alerts,
all pending approvals, and the latest20 messages matching the requested kinds.
Later reads return ordered messages after the cursor. Alerts include their
acknowledged state; raised/cleared transitions, approval status changes,
failed/rejected invocations, and actual plan revisions/states/steps enter the
message stream. Repeated state saves and cancelled calls add no failure notice.

Pass the returned opaque cursor unchanged. It remains valid across gateway
restarts using the same state root. Gateway sessions end on restart; old alert
episodes and session authorization do not become active again. Historical inbox
messages remain readable. The cursor follows one global stream, including kinds
filtered out of a response. Changing filters later does not rewind those events;
keep separate cursors for independent filtered readers. `LW_SESSION` selects
device operations, while the inbox covers the gateway.

Allowed kinds are `alert,approval,invocation,plan,job`; jobs gain a producer when
their platform feature is installed. A wrong-stream, damaged or unavailable
position returns `invalid_inbox_cursor`; omit `after` for a fresh snapshot.
An unreadable or failed journal returns `inbox_unavailable` without changing
device actions or human approval decisions. A delivered cursor follows actual
fsync of its captured message prefix. MCP connection instructions include the
current active alert count, or explicitly mark it unavailable when the inbox
cannot be read. Critical alerts should be reported to a person before device work.
