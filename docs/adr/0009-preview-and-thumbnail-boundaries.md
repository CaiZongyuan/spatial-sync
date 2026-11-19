---
status: accepted
date: 2026-11-19
---

# Preview and thumbnail generation and invalidation boundaries

Local Assets and Scene Versions separate **Thumbnail** (2D list/card images), **Scene Preview** (read-only initial scene captures from a physics source), and **Asset Visual** (declared 3D visual files). A **Thumbnail** is a derived display artifact rather than validation evidence: packages may include `previews/` at build or import time, and the Web UI fills missing entries through a shared serial offscreen renderer queue when operated by an `operator` (falling back to placeholders or line art for `viewer` roles or without WebGL). List Thumbnail caching in `ThumbnailStore` keys on package `contentHash`, theme/source, and an explicit renderer version constant rather than watching the entire `apps/web/src` file tree, so unrelated UI edits do not wipe cached list images.

**Scene Preview** captures remain authoritative initial-state readouts. Importing or publishing a Scene Version triggers background capture automatically for supported sources, and source or device-type changes degrade existing captures to a `stale` state (viewable with a clear warning and one-click recapture) rather than hiding the 3D viewport outright. Assets without a declared `Asset Visual` fall back to backend-extracted protocol `Visual` primitives from `asset.usda`, collision, or dimension metadata so Three.js conversion stays isolated in `apps/web/src/lib/geometry/primitives.ts`.

We considered leaving Thumbnail generation strictly to import/build time (which omits existing user packages and primitives without a headless WebGL pipeline), keeping full `apps/web/src` timestamp invalidation (which breaks caching on every UI edit), or opening `POST /api/v1/library/thumbnails` to anonymous callers (which weakens write boundaries). We also considered hiding `stale` Scene Previews as `unavailable`, which blocks browsing whenever shared Python or YAML files change. This accepted boundary reconciles the Web UI and Asset/Scene specifications; implementation and spec updates remain to be scheduled.
