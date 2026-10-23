# Embodied evaluations

Start the gateway with the normal [launch command](VERIFICATION.md). The shipped
`suite/lab-demo-pick-place@1` evaluates the existing `robot_arm.virtual` program
in `lab-demo@3`, without Newton or a GPU:

```text
lw suite ls --json
lw suite show suite/lab-demo-pick-place@1 --json
lw eval run robot_arm.virtual --suite suite/lab-demo-pick-place@1 --json
lw eval show <evaluationId> --json
lw score ls --suite suite/lab-demo-pick-place@1 --json
```

`eval run` returns an accepted evaluation ID immediately. `eval show` reports
`queued`, `running`, `completed`, `failed` or `cancelled`. `eval cancel` cancels
an evaluation and closes its owned session. A program must actually be bound
to the Task entity in the pinned scene; the evaluator does not swap programs or
load policies, checkpoints or script packages.

Each train episode starts a fresh session with its declared seed, runs the
Task action through the shared action pipeline, records it, and evaluates the
Task's `success` condition against the final authoritative information state.
Named milestones retain conditions observed during execution, including stages
that no longer hold at the end. Human approval gates are denied during evaluation.
Every completed episode has a complete recording; source, capture, recording
close and observation-gap failures fail the evaluation without creating a score
from fewer episodes. Evaluation recordings remain retained independently of the
ordinary recording-count limit.

The first complete train run for matching comparison conditions becomes the
baseline. A later candidate is accepted only when its Wilson 95% lower bound is
strictly greater than the Reference success rate. Completed rejected runs remain
in the append-only scoreboard. Comparison conditions include the exact suite and
scene hashes, actual source and Newton version, solver, and target entity binding
fingerprint. Synthetic records have `newtonVersion: null`. The source fingerprint
identifies the installed builtin implementation; it is not a Controller version.

The example has two train episodes with one fixed parameter pair. Synthetic
seeds drive existing virtual-program random streams, including sensor noise;
they do not randomize tube placements or taught arm motion. These runs demonstrate
evaluation plumbing and do not establish contact-physics performance.

## Evaluation center

Open **Evaluation (评估)** at `/eval`, then **打开计分板** on a suite card.
`/eval/lab-demo-pick-place@1` opens the shipped embodied suite directly. The page
reads the actual public Suite, evaluation and scoreboard APIs every five seconds;
opening it does not start an evaluation or acquire the session slot.

The scoreboard shows the runtime code fingerprint, bound Device Program, success
rate, Wilson 95% interval, elapsed time and decision. Select a row for its actual
stage observations and episode replay links. Registered Controller versions and
training costs display as unknown. Each complete condition group has its own
curve and current baseline/accepted Reference; rejected candidates remain visible.
The Agent suite `suite/lab-ops-basic@1` exposes ten public laboratory tasks and its
fixed environment at `/eval/lab-ops-basic@1`. Its model-driven execution, scores,
Trace, costs and formal baseline remain unavailable. The page keeps held-out test
locked. See [Agent and Harness versions](AGENT_HARNESS.md) for snapshots and the
private final-state/process grading boundary.

Use the comparison selectors to compare two public train evaluations. The HTTP
equivalent is `GET /api/v1/suites/{name}/{revision}/scoreboard?compare=<id>&against=<id>`.
Both parameters are required together. Records must belong to the requested suite
and optional `subject` filter. Unequal complete recorded keys return HTTP409
`not_comparable`; missing, hidden test or filtered-out IDs uniformly return404.
The default response remains `{ "rows": [...] }`. Each public row additionally
has `conditionsGroup`, the first public evaluation ID with the same suite,
subject and complete conditions; the UI uses this marker to separate curves.
It contains no digest of private inputs.

The held-out test row is always locked, with no revealed question, seed, count or
score. The role panel explains Builder, Optimizer, Evaluator and Target access;
it does not switch API roles. Builder edits the local package directly. Public
evaluation responses include only the defined DTO fields, including nested
conditions, results, episodes and error codes.

## The single session slot

A whole evaluation owns the session slot across its episodes. A pre-existing
interactive session continues until its owner closes it. Later evaluations and
interactive launches wait in FIFO order. Public writes to evaluation sessions,
including actions, cancellation, approval, lifecycle commands and native viewer
startup, are refused. Their workspace displays the live state as read-only.
Opening or closing Web observers does not pause evaluation.

During evaluation, a scene launch returns HTTP 202 with a `requestId` and a
`Location` pointing to `/api/v1/session-requests/{id}`. This is an explicit queued
launch, and does not contain a session ID until startup succeeds. The scene page
shows its status and cancellation control; the CLI equivalents are:

```text
lw session-request show <requestId> --json
lw session-request cancel <requestId> --json
```

Queued launch requests expire after ten minutes. Cancelling a request during
`starting` records `cancelling`; its creating worker finishes startup and closes
that request's session before reporting `cancelled`. If startup has already been
handed over, cancellation reports `started` and its session ID. No device action
is replayed into a later episode. Ordinary launches
outside evaluation retain their immediate HTTP 201 behavior, same-scene reuse and
different-scene conflict. CLI/MCP default device targeting excludes evaluator-owned
sessions.

The session's device view uses the same authoritative client and stays read-only
during evaluation. Real devices at `/live` remain independent of that session
slot: their actions, plans, alerts, history and continuous recording continue to
work, including when the CLI explicitly selects an evaluation session. Live
plans and high-risk actions still require human approval. Evaluation recording
pins and Live recording reader/retention ownership are preserved together.

Cleanup failures, including ordinary recording close after the source stops,
block subsequent queue work. Retry closing an ordinary session after resolving
its failure. For an evaluation, an
operator can retry only that evaluation's cleanup with
`POST /api/v1/evaluations/{id}/cleanup`. It releases the slot only after cleanup
succeeds. Gateway restart does not resume interrupted evaluations or queued launches.

## Suite packages

The gateway discovers version directories under `library/suites/<name>/<rev>/`
and the operator-configured user library (`LW_LIBRARY`). Restart after adding a
new version. Suite upload/import, full Isaac Lab Task ingestion and policy
promotion belong to their later features.

Each embodied package contains exactly three files:

```text
suite.yaml
task.yaml
private/seeds.yaml
```

Use the shipped package as the schema example. `suite.yaml` pins the exact scene
and its content hash, source, and explicit train/test cases with action parameters.
`task.yaml` contains the declarative evaluation subset: `name`, `scene`, `robot`,
`implements`, `timeoutSeconds`, `success`, and ordered `milestones` (`id`, `when`).
Conditions use the existing information-plane condition language. The seed map
has exactly one JSON-safe nonnegative integer per case ID. Full training Task
fields and Python execution are not loaded here.

Publish changes in a new version directory and retain old package bytes. The
gateway hashes the complete package, checks it when queued work starts and between
episodes, and fails changed inputs. Suites retain their pinned scene dependencies
against library deletion. Directory access remains under the local account's
filesystem permissions.

All public suite, evaluation, recording and session projections omit private seeds
and grading predicates. Evaluation recordings omit the already optional `seed`
field from their first write; ordinary interactive recordings preserve their seed.
No private sidecar is written beneath the public recording file tree. Public
`split: test` execution is refused regardless of caller headers; promotion-only test
execution is not implemented. The shipped example seeds are public demonstration
values, rather than a confidential held-out benchmark.

The shared command table exposes generic MCP tools `suite_list`, `suite_show`,
`evaluation_run`, `evaluation_show`, `evaluation_list`, `evaluation_cancel`,
`score_list`, `session_request_show` and `session_request_cancel`. They use the same
HTTP interfaces and never expose raw suite package files.
