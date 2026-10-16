# Session log v1 fixture

This is the first 10.012 seconds of a real Synthetic `lab-demo` gateway recording.
The robot picks Tube01 from rack.A1, places it in centrifuge.slot1, then starts a
second pick/place. The original NSYN and JSON record payloads are preserved.
All referenced available assets are local; the intentionally missing primary GLB
keeps a recording-relative missing URL so the native primitive fallback is used.

`expected.json` contains ten reference samples independently extracted in Python
from the log's original FIFO order. They pin Tube01's float32 pose and location,
the robot's joint positions, and the latest spatial sequence. Both ReplaySource
unit tests and the real browser journey compare against these values.

The artifact is under 1 MB and uses `LWLG` format 1, protocol 2. Record lengths
include the kind byte, timestamp and payload (9 + payload bytes).
