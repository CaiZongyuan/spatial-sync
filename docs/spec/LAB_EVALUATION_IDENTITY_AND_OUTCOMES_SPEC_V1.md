# #132 A/B — evaluation identities and valid outcomes

This engineering correction separates Candidate, environment and judge evidence
in the existing evaluator and Notebook, and retains healthy task timeouts as
valid failures. It does not change Wilson/Agent acceptance thresholds, held-out
promotion policy, runtime sandbox scope, model billing or #132C/D decisions.

## Frozen identities

- New runs retain recoverable Candidate provenance separately from internal
  `comparisonIdentity:{environment,judge}`. Only matching complete comparison
  evidence can select a measured Reference or be treated as comparable. Two
  missing identities are not equal evidence.
- The Candidate identity is version metadata, never part of the comparison
  group. Agent candidates freeze exact Harness/archive/skill bytes. Policy
  candidates retain immutable policy/checkpoint/ONNX/training-Task provenance;
  policy observation/inference implementation is fixed environment evidence.
- For supported repository Program candidates, identify the selected Program
  class. Preserve the complete original source/hash/location for recovery and
  take that class's source as Candidate content. The module's remaining imports,
  constants/helpers (including Synthetic kinematics) stay in environment input.
  All actual non-target Programs remain fixed. Shared/unknown class ownership
  stays unknown rather than claiming an unproved import closure; evaluation can
  still run, but it cannot establish a new comparable Reference.
  Programs may declare the internal generic `fixed-runtime` provenance role.
  Their complete class stays in environment inputs for both Program and policy
  candidates; PolicyProgram declares this role because weights are its candidate
  artifact and observation/target conversion code is fixed runtime behavior.
- Freeze Suite/Scene/evaluation-Task/binding/dependency inputs, actual supported
  Source/engine facts, observation implementation and judge/conditions/timeout/
  grading implementation. Distinguish observed implementation from declarations.
  The platform's actual Scene/template/binding resolvers, HTTP session commands,
  operation Plan/alert projections and `lw` MCP schemas/descriptions/client
  routing are fixed implementation inputs. External client/runtime SDK code
  and context remain unattested; existing runtime-declaration provenance is
  retained rather than presented as a platform implementation measurement.
  A trusted common runtime probe can bind actual inference runtime/provider
  availability for both Program and policy candidates; actual policy execution
  must agree with that probe. Candidate weights and call counts stay separate.
  The fixed Suite's evaluation Task is distinct from a candidate's training Task.
- Retain actual bytes/lock files or retrievable immutable artifacts. Dirty
  source cannot be described as recoverable using only a commit name/hash.
  Private input manifests/hashes stay internal; public surfaces expose only
  safe fixed conditions and equivalence groups.
- Admission precedes Source effects. Recheck the fixed evidence during execution
  and record completion evidence; changed or missing required facts are unknown/
  invalid, not interchangeable. A shared producer supplies core and Notebook.

## Compatibility and comparisons

Old rows stay readable with their original score and decision. Missing legacy
environment/judge proof is explicitly unknown; old records do not silently gain
new identities or qualify as new References. Re-evaluate both versions under a
new complete condition to establish that group's measured Reference.
Already committed Notebook entries keep their historical comparison, score,
automatic judgement and audit. Current reads project unknown comparisons without
a Reference or gap reduction, and expose the prior `recordedComparison` separately.

The existing public comparison request rejects unknown/different conditions.
CLI/MCP, Notebook and curves retain the same factual distinction. Candidate-only
changes can compare; judge, condition, Source or observation changes separate
groups across a gateway restart. Scene/binding integrity checks remain required.

## Healthy task deadlines

A healthy observed task reaching its fixed deadline is a valid failed episode.
Retain recording, exact episode ID, timeout reason and observed milestones,
include it in the denominator and continue the fixed protocol. Cancel its own
incomplete invocation as resource cleanup without converting that negative into
an infrastructure error or retry.

Source failure, observation gaps, missing/stale critical physical data, scope
mismatch or unconfirmed cleanup remain invalid/incomplete. Preserve attempts,
partial recordings and resource facts; publish no complete result for an invalid
required scope. Fresh known-false state stays a valid negative. Do not require
unrelated properties or absent derived process/alert events to exist.

## Evidence

Use actual core admission/Reference selection plus restart, paired owned-file
identity changes and recoverable snapshots. Program-class-only changes retain a
group; shared helpers/non-target Program/conditions/judge changes separate it.
Agent and policy consumers use the same server-produced identities and fail
closed when missing. Paired real healthy Synthetic deadlines and controlled
unavailable Sources prove exact retained denominators and continuation. Existing
Notebook/cost/read/replay consumers retain unsuccessful and invalid attempts.

No Program behavior, geometry or published recording mutation is needed for this
correction. Repository mandatory checks, bounded simplification, non-author
Standards/Spec and exact-head integration apply; #132 remains open for C/D.
