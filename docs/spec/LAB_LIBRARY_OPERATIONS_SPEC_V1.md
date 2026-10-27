# Ordinary library operations and responsive browsing

Approved scope: issue #206 and the user's instruction to execute its plan.
This extends the Assets and Scenes and Web UI specifications. Spatial protocol,
recording formats, device actions and their approval boundaries are unchanged.

## Browse

`GET /api/v1/library/scenes?view=summary&limit=20&cursor=...` reads indexed
declarations and the last recorded validation. It does not load geometry,
resolve every template or inspect complete preview captures. `q`, `source` and
`launchable` filter before the selected page is described. The maximum page is
100. A cursor binds the query and indexed identities; changed membership gives
409 `library_changed`. Missing physical counts remain unknown.

The response retains `scenes` and adds `pagination` with `total`, `limit`,
`cursor` and nullable `nextCursor`. Omitting pagination on the old full endpoint
retains its complete result and existing fields. CLI defaults keep their array
shape. Full and bounded details retain all their existing information.

File I/O runs outside the API event loop. Derived metadata caches are bounded
and invalidated by changes to their input files/directories. Launch and actual
file use still verify immutable bytes; a cache is not execution authorization.
Details may show the last validation while individual previews are unavailable.

## Removal and recovery

`POST /api/v1/library/deletions` submits an explicit ordinary operator request
for a user version: resource, exact id, contentHash and requestId. The same
operation is available to authorized Agents without a human Approval. Restricted
evaluation/Optimizer credentials retain their existing scope limits.

`GET /api/v1/library/deletions/{id}` reads its actual execution status;
`POST /api/v1/library/deletions/{id}/restore` restores exactly the retained bytes.
Only unreferenced user versions can be removed. Content identity, reference
inspection, active/starting sessions, read-only built-ins and version allocation
remain protected. Requests are idempotent per actor/requestId; changed arguments
conflict. Committed removal withdraws the version from discovery and retains the
package in the recovery area. A restore cannot overwrite another identity.
Imports keep their monotonically allocated revisions.

Accepted work is distinct from committed work. Long operations expose their
record and failures rather than requiring another approval. This version adds
no automatic permanent purge policy. Retained disk usage is visible.

The legacy DELETE endpoints still create human approvals. Old approvals and
transaction receipts remain readable/recoverable. Existing pending approvals
cannot delete an already withdrawn identity a second time. Neither CLI nor MCP
gains a generic approval tool.

## Presentation

Scene and Asset lists use static images in the visible area and bounded
preloading. They do not import the interactive renderer or load GLBs to make
every card. Missing images have truthful placeholders; detail/explicit preview
work can generate them. Image failures do not block the list or other images.
Search coalesces input and retires obsolete results. Details load 3D on demand.

Approval navigation polls counts/role only. The approval page defaults to pending
items with stable pagination so every request can be reached. Decisions update
the count, page and actual execution progress independently.

## Verification

Use real HTTP/installed CLI/official MCP for removal, restoration, identity,
references, restart recovery, role restrictions and legacy approval compatibility.
Prove a blocked filesystem task cannot block concurrent health. Test an index
of at least 1000 entries and stable pages, stale cursors and zero results without
opening geometry. Retain tampering/preview validation and old-recording playback
checks. Browser checks cover static lists, failure/retry, pagination and actual
direct removal/restoration. Follow docs/VERIFICATION.md; Linux standard checks
and Windows focused filesystem/browser checks run separately from profiling.
