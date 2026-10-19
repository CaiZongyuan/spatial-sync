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
recording. The telemetry history writer and indexed lookup arrive with #21.
Current session writers do not emit sidecars.

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
