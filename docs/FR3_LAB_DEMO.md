# FR3 laboratory demo

`fr3-lab@2` is a local imported scene assembled from the original laboratory
assets. It demonstrates import, Newton startup, PBR visuals, asset gaps and a
representative CLI interaction. This is the A3 scope approved for #18; full
four-slot I3, 1 mm grip stability and learned contact-policy quality remain
separate, unpassed quality work. Training and Policy acceptance are unchanged.

The scene uses the ordinary main-branch Newton runtime. Experimental integrator,
weld-strength and finger-goal probes were not adopted.

## Local inputs

The original local asset conversion lives under
`.scratch/rsi-platform/issue-18/factory/mechanical-a-001/private-seed-packages-003-adopted-003/`.
Its `assets/` contains the split laboratory assets and its `scenes/` retains the
older scene. The demonstrated layout is the eight-file scene package
`.scratch/rsi-platform/issue-18/layout-v2-20261023/candidate-rack200-speed1/`.
It includes the raised, rotated FR3, matching fixed pedestal, articulated
centrifuge and teaching. Original imported versions are preserved.

On this workstation, the already imported library is
`/home/caii/.cache/spatial-sync/.scratch/a3-resume-20261025/native-original-002/library`.
It contains the split assets and `fr3-lab@2`. Keep these local inputs when moving
the workbench; a fresh Git checkout alone does not contain the imported assets.

## Start the local demo

Use the root README's Python/Node dependencies. This workstation's frozen WSL
environment `/home/caii/.venvs/spatial-issue35/` already includes simulation/USD
and the main-branch dependencies. The following runs from the primary checkout
in WSL. It copies the imported library into a fresh demo root and starts one
ordinary API worker:

```bash
cd /mnt/d/Projects/Robots/spatial-sync
FR3_DEMO_ROOT="$PWD/.scratch/fr3-lab-demo-$(date +%Y%m%dT%H%M%S)"
mkdir -p "$FR3_DEMO_ROOT"
cp -a /home/caii/.cache/spatial-sync/.scratch/a3-resume-20261025/native-original-002/library "$FR3_DEMO_ROOT/library"
export PYTHONPATH="$PWD/apps/cli:$PWD"
export LW_LIBRARY="$FR3_DEMO_ROOT/library"
export SPATIAL_STATE_ROOT="$FR3_DEMO_ROOT/state"
export SPATIAL_RECORDINGS_ROOT="$FR3_DEMO_ROOT/recordings"
export SPATIAL_ALLOWED_ORIGINS=http://127.0.0.1:5258
export CUDA_VISIBLE_DEVICES="" PXR_WORK_THREAD_LIMIT=1
/home/caii/.venvs/spatial-issue35/bin/python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8058 --workers 1
```

In another WSL terminal, use the installed CLI against that API:

```bash
cd /mnt/d/Projects/Robots/spatial-sync
export PYTHONPATH="$PWD/apps/cli:$PWD"
export LW_URL=http://127.0.0.1:8058
FR3_DEMO_LW=/home/caii/.venvs/spatial-issue35/bin/lw
"$FR3_DEMO_LW" --json scene ls
"$FR3_DEMO_LW" --json scene show fr3-lab@2
"$FR3_DEMO_LW" --json scene launch fr3-lab@2 --source newton
```

Use the returned `sessionId` as `LW_SESSION`. Describe the arm and run the
representative interaction through the same action pipeline:

```bash
export LW_SESSION=the-returned-session-id
"$FR3_DEMO_LW" --json show /World/Lab/RobotA
"$FR3_DEMO_LW" --json do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1 --wait --timeout 120
```

The demonstrated invocation reached `succeeded`. A successful invocation is not
a claim of long-term physical stability: the original geometry still permits
the placed tube to tip later. Read the returned outcome rather than assuming
every physical run succeeds.

Start the Web app from the checkout with `SPATIAL_API_URL=http://127.0.0.1:8058`
and `pnpm --filter @spatial-sync/web dev --host 127.0.0.1 --port 5258 --strictPort`.
Open `http://127.0.0.1:5258/sessions/<sessionId>` for the scene and `/assets` for
the split assets. The biosafety cabinet has a PBR preview and explicitly shows
no implementation type; this is an exposed asset gap.

Close the demo's own session from its Web page, then stop the API and Web
commands in their owning terminals. Imported packages and recordings remain
in the fresh demo root.
