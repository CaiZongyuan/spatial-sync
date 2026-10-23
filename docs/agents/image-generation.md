# Image Generation

How agents generate bitmap images in this repo through the Codex `imagegen` skill in CLI mode.

## Setup

- Read `~/.codex/skills/.system/imagegen/SKILL.md` first; follow its CLI-mode section and leave `scripts/image_gen.py` unmodified.
- Credentials come from `~/.codex/config.toml`: take the `[model_providers.<name>]` table named by `model_provider`, and pass its `base_url` and `experimental_bearer_token` as `OPENAI_BASE_URL` and `OPENAI_API_KEY` to the child process only. Read them with `tomllib` inside the same shell command that runs the script, so they stay in memory and never reach output, files or logs.
- The project `.venv` lacks `openai` and `pillow`. Run the script in a throwaway environment so the project environment stays untouched:

```bash
uv run --no-project --with openai --with pillow python \
  ~/.codex/skills/.system/imagegen/scripts/image_gen.py generate \
  --model gpt-image-2.5-flare --prompt-file <prompt.txt> \
  --out output/imagegen/<name>.png \
  --size 1024x1024 --quality high --output-format png
```

## Models and parameters

- `gpt-image-2.5-flare` for everyday images (about 25 s per 1024px image); `gpt-image-2.5-sunburst` for refined work.
- To compare models, keep the prompt and parameters identical and add `--no-augment`.
- Put prompt files in `.scratch/` or the session scratchpad.

## Output

- Write final images to `output/imagegen/`. `output/` is not gitignored, so leave generated images out of commits unless the user asks for them.
- The service ignores the requested `--size`: a `1024x1024` request has returned `1254x1254` and `1536x1024`. Check the actual dimensions with Pillow, view the image to check the content and any text, then report the path with its actual size.
