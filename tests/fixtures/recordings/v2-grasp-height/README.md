# Nonzero grasp-height storage compatibility fixture

The public `SessionLog` writer produced this format 2 / binary version 1 archive
from two explicitly authored Synthetic states and the published v2 Manifest and
Catalog. The arm Config has `graspHeightM=0.020`. The held tube's bottom is 20mm
below its stored physical TCP at both samples. `expected.json` contains the
literal poses and information values; spatial values use NSYN float32 storage.

This is a storage compatibility fixture, not a live Source recording, FK result
or FR3 feasibility claim. Its joint vector is deliberately independent of the
authored poses: replay must preserve archived state without invoking a current
program, recomputing FK or moving the TCP down to the tube bottom. The fictional
`lab-demo@80` identity belongs only to this fixture and is not a published package.
The real format 1 and format 2 Source recordings remain unchanged.
