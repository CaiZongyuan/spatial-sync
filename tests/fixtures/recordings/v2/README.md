# Session recording format 2 fixture

This is a real 10.062 second Synthetic `lab-demo@2` session, captured through the
HTTP action pipeline and the normal format 2 writer. Its selected user package
changes rack.A1's pose and approach from the built-in version 1. The robot picks
Tube01 from rack.A1 and places it at the centrifuge's Slot1 before the session
is paused and closed. Frozen scene assets and original NSYN/JSON payloads are
included; the entire fixture is below 1 MB.

`expected.json` records the final paused pose/sequence and entity values obtained
independently from the public HTTP pose and state endpoints, before closing the
session. Pose components are quantized to the segment protocol's float32 values.
The browser and ReplaySource tests compare the replay's final state against this
reference. Version 1's ten retained reference samples remain unchanged.

`recording.json` uses metadata format 2, protocol 2 and exact scene package
identity. The segment still uses the published LWLG binary version 1. No property
index attachment is required: playback scans sequentially.
