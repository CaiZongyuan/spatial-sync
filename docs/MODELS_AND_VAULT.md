# Models, local Vault and prices

Open **Resources · Models** at `/resources/models`. The LLM table shows registered
provider/model IDs, default thinking levels, credential names with a fixed mask,
and configured prices. An empty installation has no sample models or prices.
Adapter health, latency and users stay unknown until their actual data source is
connected. The Robot Model directory is explicitly unconnected in this delivery.

## Configure a model

Run the following in a local terminal, replacing each placeholder with your own
configuration. The Vault prompt hides the value; never put it in arguments, a
pipe, an environment setup command or an HTTP request.

```text
lw vault set <credential-name>
lw vault ls --json
lw model add <provider>/<model_id> --thinking high --credential <credential-name>
lw model default <provider>/<model_id>
lw model ls --query <provider> --json
```

`vault set` requires real terminal input and output. It rejects `--json`, redirected
input/output and value arguments before contacting a gateway. All other commands
above support JSON output. Vault writes are local; model commands and `vault ls`
use the configured gateway (`LW_URL`). A remote gateway's Vault therefore must
be configured by running `lw vault set` on that gateway's machine.

The state directory is `SPATIAL_STATE_ROOT`, defaulting to
`~/.spatial-sync/state`. Its `vault/` credential files require POSIX mode0600,
inside a mode0700 directory. On Windows, the files and directory have a protected
DACL granting only the current user full control. Existing insecure permissions,
symlinks and reparse points are refused; Windows does not rely on `chmod`.
Names are enumerated from filenames without reading credential contents.

There is no HTTP write/read-value endpoint and no Vault MCP tool. The MCP
`model_ls` tool returns model metadata only. Existing device MCP tools retain
their schemas and still use the single HTTP action pipeline.

The internal `Vault.adapter_environment` handoff resolves explicitly named
credentials to child environment variables without modifying the gateway's
environment. Its result is only for a child process's `env`; it must never be
serialized into arguments, logs, Trace, recordings or model messages. This
delivery does not start an Agent adapter or make external health requests.

## Prices

Choose **Edit price table** on the model page. Enter actual USD rates per million
input/output/cache-read tokens, and optional USD per GPU hour for named nodes.
Leave unknown rates empty; zero means free. An unknown rate displays `*` and
cannot establish an amount. Amounts derived from these rates are estimates.

The gateway persists `models.yaml` and `prices.yaml` under the state directory.
The price file contains a version, revision, model and GPU rates, and an audit
entry recording the operator and UTC time of each HTTP edit. The page shows
these price changes. Saving an outdated revision returns `prices_changed` (409);
reload before editing again. Invalid or unreadable configuration returns a safe
error and is not overwritten.

The price file's rates use this structure; absent rates remain `null`:

```yaml
version: 1
revision: 0
models: [] # provider, modelId, inputUsdPerMillion, outputUsdPerMillion, cacheReadUsdPerMillion
gpus: []   # nodeId, usdPerHour
audit: []  # maintained by the gateway when prices are saved through HTTP
```

## HTTP metadata

Paths below start with `/api/v1`. They are separate from the spatial protocol and
recording format. Mutations require the operator role; price edits record
`X-Actor` using the existing `user:name`, `cli:name` or `agent:name` convention.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/vault` | `{names: [...]}` only, never values |
| GET | `/models?q=...` | Model metadata, default reference, management permission |
| POST | `/models` | Register `{provider, modelId, defaultThinking, credentialName}`; credential name is optional |
| PUT | `/models/default` | Select an existing `{ref: "provider/model_id"}` |
| GET | `/prices` | Versioned rates, revision, audit and editing permission |
| PUT | `/prices` | Replace `{revision, models, gpus}` and append operator audit |

Registration rejects duplicate models, unknown credential names and invalid
thinking levels. Setting a default rejects unregistered models. Prices reject
negative/nonfinite rates, duplicate references and stale revisions. Validation
responses for these routes omit rejected inputs, including accidentally supplied
credential fields.
