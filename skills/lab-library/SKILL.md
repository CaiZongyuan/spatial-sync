---
name: lab-library
description: "Find laboratory assets or scene versions, inspect validation and completeness, import packages, revalidate an immutable version, and select a scene to launch."
---

# Laboratory library

## Human boundaries

Agents must not execute approvals, denials or revocations, or enter Vault values
through terminal, HTTP, MCP, browser or direct storage changes. Return the pending
request identifier and reason to a person; continue after the platform reports
their decision. Preserve sessions and files owned by other work.

## Find and inspect

1. Search the gateway selected by `LW_URL`, then describe the returned exact ID:

   ```text
   lw asset ls --json
   lw asset ls --kind robot --gaps --json
   lw asset show <kind/name@rev> --json
   lw scene ls --limit 20 --json
   lw scene show <name@rev> --json
   ```

   Read the content hash, source root, six completeness items, gaps, declared
   types, validation checks and available sources. For a scene, also read its
   devices, taught locations, referenced assets and untyped entities. Copy IDs
   and entity paths from these responses. A missing or stale report is visible
   evidence to explain; an image is not a validation result.

## Import or recheck

2. Import an authored package directory from the CLI/MCP client's filesystem:

   ```text
   lw asset import <package-directory> --json
   lw scene import <package-directory> --json
   ```

   The client uploads tar bytes. Supply one root `asset.yaml` or `scene.yaml`,
   its declared files, and for scenes `devices.yaml` and `locations.yaml`. Omit
   `validation.json`; the gateway generates the report. Correct schema/upload
   errors in the authored package before retrying. A successful import can
   contain failed validation checks: read the returned version and report.
   Reimporting unchanged bytes returns the existing version. Edit the authored
   package and import again to produce a new version; published versions are
   immutable.
   For robot sources, supply root `robot.json` with its URDF, MJCF or USD entry,
   package-local dependencies and original license. Use the returned exact ID
   to inspect or calibrate the Asset:

   ```text
   lw asset import <robot-source-directory> --json
   lw asset show <robot/name@rev> --json
   lw asset calibrate <robot/name@rev> <recipe.json> --json
   lw asset validate <robot/name@rev> --json
   ```

   Calibration applies the complete recipe to the retained source and returns
   an immutable version. Read its `joint-calibration.json` and its report.
   In P8, compare each solver's measured base height, tilt, self-penetration and
   joint limits. If physics is skipped, report the missing runtime.
3. Recheck an existing exact version when the task needs a current report:

   ```text
   lw asset validate <kind/name@rev> --json
   lw scene validate <name@rev> --json
   ```

   Revalidation appends a report without changing published bytes or their
   content hash. Check `reportId`, `contentHash`, `checks` and `launchable`.
   Validation exits 3 when a check fails. Warnings remain visible; a physics
   check marked not run is not evidence of a physics pass. Correct failed
   checks rather than changing report files or launch flags.

## Prepare a motion reference

Upload a rights-cleared single-person MP4 or WebM. Queue CPU extraction using
the returned video ID. Read the Job and wait for the immutable Clip result.

```text
lw motion video-upload <video.mp4> --license <license-id> --attribution <verified-source> --json
lw motion start <video-id> --node <pose-node-id> --request-id <stable-request-id> --json
lw job show <job-id> --json
lw motion show <clip-id> --json
lw motion retarget <reference-request.json> --json
lw motion preview <reference-id> --json
```

The retarget request supplies the exact Clip ID and digest, robot Asset ID
and content hash, and an explicit Asset-local semantic mapping. Check the
feasibility report and preview on that Asset's detail page. Retain the exact
Reference ID and digest, source rights, and pose model identity. [Motion and
Task inputs](../lab-training-plan/SKILL.md)

## Remove or restore a version

For a cleanup task explicitly authorized by the person, move an exact unused
user-library version to recovery storage with `lw scene rm <name@rev> --json`
or `lw asset rm <kind/name@rev> --json`. This ordinary operation creates no
Approval. Use `--request-id <stable-id>` for retries of the same request.
Read the returned status: only `committed` means the version left the active
library. `deletion_pending` includes the record to resume with
`lw deletion show <deletion-id> --json`; do not resubmit a different request
just because execution is still running.

Use `lw deletion ls --json` to find retained versions and their disk usage,
and `lw deletion restore <deletion-id> --json` to restore the original bytes.
Report `restored` only after that terminal status. References, incomplete
inspection, read-only built-ins, changed bytes and restore conflicts keep the
version or recovery package intact; return the concrete blockers. Human
approval decisions remain a separate boundary above.

## Choose a session

4. Select an exact launchable scene and one of its declared sources:

   ```text
   lw scene ls --launchable --limit 20 --json
   lw scene launch <name@rev> --source <supported-source> --json
   ```

   Launch only when the active slot is available. `scene_not_launchable`
   includes the failed report; `source_unavailable` means the requested runtime
   is unavailable. Report that precise state and preserve unrelated sessions.
   Device operations and outcome verification follow
   [lab-operate](../lab-operate/SKILL.md).

The generic MCP equivalents are `library_search`, `library_describe`,
`library_import`, `library_calibrate`, `library_validate` and `scene_launch`. They share the CLI's
gateway behavior and human boundaries. Complete the task with the actual
version, content hash and relevant checks, or the precise remaining blocker.

`library_describe` defaults to a bounded summary. To inspect complete action
schemas, Config, reports or provenance, use its exact `id` and `documentDigest`
with `view="page"`, optionally selecting a top-level `section` such as `types`
or `validation`. Start at `offset=0`, pass each `nextOffset` until null, then
concatenate `content` and parse the JSON. `previewTruncated` means more details
are available through pages. On `description_changed`, fetch a fresh summary
and restart when reports/recordings/reference metadata is stable. `view="full"`
returns the legacy complete object and can exceed tool output limits. CLI
`lw library describe <name@rev>` shares these options; `scene show` and `asset show`
keep their full default.
