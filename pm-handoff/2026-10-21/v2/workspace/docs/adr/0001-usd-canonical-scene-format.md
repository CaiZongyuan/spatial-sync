# USD is the canonical format for Assets and Scene Versions

Assets and Scene Versions are stored as USD: an Asset is a USD package with UsdPhysics annotations, and a scene stage is a composition layer that references Asset versions. Browser visuals (glTF) are derived from it by a build step. We chose USD because Newton, Isaac Sim and Isaac Lab all read it natively and USD Prim Paths are already our `entityKey`, so the RSI loop can run interactive simulation and batch training on the same scene without translating between worlds.

The existing Python-builder scenes (`lab-demo`, `falling-boxes`, `robot-and-tubes`, `usd-lab`) stay as built-in scenes, wrapped as scene packages whose stage points at their builder; converting them to USD is not required.

## Considered Options

- **Python builders plus YAML composition**: what the repo had, but Isaac Lab cannot consume it, so every trained scene would need a second definition.
- **A custom JSON scene format**: needs an exporter per backend and duplicates what UsdPhysics already standardizes.
