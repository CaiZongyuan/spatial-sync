# Small USD library scene

These self-authored packages demonstrate two rigid tubes, a collision work
surface, two ordinary USD Config Site prims and one nonphysical dressing layer.
They are loading fixtures, not models of production laboratory equipment. All
geometry and PBR materials are CC0; `generate.py` reproduces the provided GLBs
and provenance from the same dimensions as the USD.

With the gateway running from an environment containing the simulation and USD
extras, run from the repository root:

```powershell
uv run --no-sync lw asset import examples/usd-library/assets/usd-fixture-room --json
uv run --no-sync lw asset import examples/usd-library/assets/usd-fixture-table --json
uv run --no-sync lw asset import examples/usd-library/assets/usd-fixture-tube --json
uv run --no-sync lw scene import examples/usd-library/scene --json
uv run --no-sync lw scene launch usd-library-fixture@1 --source newton --json
```

Each report records the actual structural and physics checks performed. Inspect
any failures before launch. The scene uses Newton on CPU by default. Open its
session in the browser to see the provided package GLBs and dressing. The tubes
use the existing `tube.newton` program and declarative template bindings.
