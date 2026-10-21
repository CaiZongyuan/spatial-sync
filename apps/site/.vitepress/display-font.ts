import { readdirSync, readFileSync } from 'node:fs'
import { extname, join } from 'node:path'

// The display font is a subset cut by tools/subset_site_font.py. These rules
// match that script, so the build can tell when a page uses a character the
// subset was not cut for.
const SOURCE_SUFFIXES = new Set(['.md', '.mts', '.ts', '.vue'])
const SKIPPED_DIRS = new Set(['node_modules', 'cache', 'dist'])

function siteChars(dir: string, chars = new Set<string>()): Set<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRS.has(entry.name)) siteChars(path, chars)
    } else if (SOURCE_SUFFIXES.has(extname(entry.name))) {
      for (const c of readFileSync(path, 'utf8')) if (c.codePointAt(0)! > 0x7f) chars.add(c)
    }
  }
  return chars
}

export function assertDisplayFontCovers(siteRoot: string) {
  const cut = new Set(readFileSync(join(siteRoot, '.vitepress/display-font-chars.txt'), 'utf8'))
  const missing = [...siteChars(siteRoot)].filter((c) => !cut.has(c)).sort()
  if (missing.length) {
    throw new Error(`The display font subset lacks ${missing.length} character(s) used by the site: ${missing.join('')}\nRun \`pnpm site:font\` and commit the result.`)
  }
}
