# Light paper textures

The SVG files are the art sources for the three deterministic light-theme tiles from the [UI audit](https://github.com/CaiZongyuan/spatial-sync/issues/86), introduced in commit `1875245`. The PNG files are transparent browser-rasterized derivatives, used at their original CSS sizes:

| Tile | Size | Seed | Frequency | Octaves |
|---|---|---|---|---|
| `warm-grain` |260×260|7|.78|2|
| `white-grain` |260×260|23|.9|2|
| `cloud` |900×900|4|.0055|4|

The SVG color matrices remain authoritative. `tokens.css` keeps the two gradients and five-layer size/attachment lists; dark grain uses its existing SVG independently.

Regenerate with the repository's frozen Playwright/Chromium installation:

```sh
node tools/bake_paper_textures.mjs --report .scratch/paper-textures.json
```

The report records browser version, source/output hashes and the actual natural-size RGBA roundtrip differences. Transparent color conversion may round channel values. Review the composed page when regenerating; natural-tile measurements alone do not establish identical page pixels or a performance improvement.
