"""Cut the site's display font down to the characters the site uses.

The site self-hosts Noto Serif SC because Google Fonts is unreachable from
mainland China. This downloads the pinned upstream variable font, instances it
at weight 600, keeps printable ASCII plus every non-ASCII character in the
site's sources, and writes the WOFF2 together with the character list that
`pnpm site:build` checks against. Run it through `pnpm site:font` whenever
the build reports missing characters.
"""

import argparse
import hashlib
import tempfile
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

UPSTREAM = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/9b0f1436e455d902de067a2501422e5dc71ad16b/"
    "Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf"
)
UPSTREAM_SHA256 = "5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09"
SITE = Path(__file__).resolve().parent.parent / "apps" / "site"
WOFF2 = SITE / "public" / "fonts" / "noto-serif-sc-600.woff2"
CHARS = SITE / ".vitepress" / "display-font-chars.txt"
# Keep in step with apps/site/.vitepress/display-font.ts.
SOURCE_SUFFIXES = {".md", ".mts", ".ts", ".vue"}
SKIPPED_DIRS = {"node_modules", "cache", "dist"}


def site_chars() -> set[str]:
    chars: set[str] = set()
    for path in SITE.rglob("*"):
        if path.suffix in SOURCE_SUFFIXES and not SKIPPED_DIRS & set(path.relative_to(SITE).parts):
            chars.update(c for c in path.read_text(encoding="utf-8") if ord(c) > 0x7F)
    return chars


def fetch(directory: Path) -> Path:
    path = directory / "NotoSerifSC-VF.ttf"
    with urllib.request.urlopen(UPSTREAM) as response:
        path.write_bytes(response.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != UPSTREAM_SHA256:
        raise SystemExit(f"Downloaded font has SHA-256 {digest}, expected {UPSTREAM_SHA256}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", type=Path, help="a local copy of the upstream NotoSerifSC-VF.ttf")
    args = parser.parse_args()
    chars = site_chars()
    with tempfile.TemporaryDirectory() as tmp:
        font = instancer.instantiateVariableFont(TTFont(args.font or fetch(Path(tmp))), {"wght": 600})
        options = subset.Options()
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=[*range(0x20, 0x7F), *map(ord, chars)])
        subsetter.subset(font)
        font.flavor = "woff2"
        font.save(WOFF2)
    CHARS.write_text("".join(sorted(chars)) + "\n", encoding="utf-8")
    cmap = font.getBestCmap()
    absent = "".join(sorted(c for c in chars if ord(c) not in cmap))
    print(f"{WOFF2.relative_to(SITE)}: {len(cmap)} characters, {WOFF2.stat().st_size // 1024} KiB")
    if absent:
        print(f"Not in Noto Serif SC (the font stack falls back for these): {absent}")


if __name__ == "__main__":
    main()
