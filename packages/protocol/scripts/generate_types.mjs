import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { compile } from 'json-schema-to-typescript'

const root = new URL('../types/', import.meta.url)
const schema = JSON.parse(await readFile(new URL('protocol.schema.json', root), 'utf8'))
const types = await compile(schema, 'ProtocolContract', {
  bannerComment: '/* Generated from Pydantic by pnpm protocol:generate. Do not edit. */',
  additionalProperties: false,
  style: { singleQuote: true, semi: false },
  cwd: fileURLToPath(root),
  unreachableDefinitions: true,
})
await writeFile(new URL('index.ts', root), `${types}\nexport type CommandRequest = Command\n`, 'utf8')
