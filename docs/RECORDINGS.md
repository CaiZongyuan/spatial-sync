# Recording compatibility

New recordings use `recording.json` format 2 and protocol 2. Session recordings
carry `kind: "session"`, the selected exact `sceneVersion` (for example
`lab-demo@2`) and its SHA256 `sceneContentHash`. The hash pins the scene package
bytes used at launch. Reloading and gateway recovery retain this identity.
Live recordings carry `kind: "live"` and explicit JSON `null` for both fields;
they do not acquire a synthetic scene version.

Readers accept published format 1 and format 2 metadata. A format 1 recording
is projected as a session recording with `sceneVersion: "<sceneId>@1"` and an
unknown (`null`) content hash. This projection never rewrites the original
file. Old crash metadata without a scene name stays unknown. Format 2 requires
explicit, valid identity and never falls back to version 1. Recording discovery,
scene detail, the recordings page and replay use these same attribution rules.

The segment header and record layout remain **LWLG binary version 1**
(`4c 57 4c 47 01 00`). Metadata version 2 does not change binary payloads,
spatial coordinates, protocol messages or recording-relative scene assets.

## Optional property index

A format 2 segment entry may include
`"propertyIndex": "seg-NNNNNN.index.json"`. It names a sibling attachment;
recording replay continues to scan the binary segment in FIFO order. An absent
or unavailable index requires sequential scanning and does not invalidate the
recording. Current session and Live writers emit these sidecars for indexed
history reads.

The sidecar contract is:

```json
{
  "formatVersion": 1,
  "segment": "seg-000001.bin",
  "properties": [
    {
      "entityKey": "/Real/Incubator2",
      "property": "temperatureC",
      "startSampleTimeMs": 1000,
      "endSampleTimeMs": 3000,
      "recordOffsets": [6, 240]
    }
  ]
}
```

Each entry covers one `(entityKey, property)` and its inclusive sample time
range within that segment. Offsets are zero-based bytes from the beginning of
the binary file to the record's uint32 length header, in FIFO order. The index
is a lookup hint: readers still decode the referenced records and filter the
property and time range. Recovery may discard stale index references while
rebuilding segment metadata; it preserves independently playable samples.

## Continuous Live recordings

A configured Live laboratory owns one stable recording across gateway restarts.
Each Host lifetime adds a new Catalog revision; Live segments contain information
snapshots, observations, invocations, alerts and plans. They have no spatial
manifest or frame stream. Replay recognizes `kind: live` and shows read-only device
and plan panels. Published format 1/2 session recordings retain their spatial
replay behavior and original bytes.

Segments rotate on UTC day boundaries and the normal size limit. Admission time
selects the UTC partition even when the disk writer is delayed. The current UTC
day and previous six days are retained; active, queued and reading segments remain
protected until their work finishes. Live recordings do not consume the session
recording count limit. A download of an active segment returns the exact byte
prefix advertised by its checkpoint, so concurrent writes cannot change the body
after its headers have been sent.

`sampleTimeMs` is an arbitrary-origin gateway receive clock in milliseconds.
Reopening a Live recording uses private UTC correlation to preserve that logical
axis, including gateway downtime. It does not turn sample times into Unix times.
Property metadata retains the actual measurement time at segment rolls;
`simTime` stays in seconds and invocation `createdAtMs` stays UTC. History and
replay consume these clocks without remapping event payloads.

Exclusive ownership, allocation counters, partition dates and clock correlation
live under the state root's `lives/` directory, outside downloadable recordings.
Recovery keeps independently readable information prefixes and marks interrupted
recordings incomplete. The retained-file allowlist prevents a deleted expired
segment from reappearing after an interrupted cleanup.
