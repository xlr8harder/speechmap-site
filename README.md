# Adding a new model

1. Collect and judge the new model with [speechmap-eval](https://github.com/xlr8harder/speechmap-eval).
2. Keep `speechmap-data` as a sibling checkout, or set `SPEECHMAP_DATA_ROOT` to its path. `preprocess.py` reads production analyses from `$SPEECHMAP_DATA_ROOT/analysis`.
3. Add model metadata to `model_metadata.json` (one JSON object per line).
4. Install deps: `uv sync`.
5. Generate the site: `uv run python preprocess.py`.
6. Preview locally with `npm run dev` at http://localhost:8789/. For static-only iteration, `python3 -m http.server -d dist 8000` is faster, but Function routes will not work.
7. Commit the code/data checkpoints used for the build. Production deployment is a separate action from Git push.

## Build outputs
- Deployable site (generated and ignored):
  - `/dist/`
- Runtime JSON:
  - `/dist/data/metadata-core.json`
- Build-only artifacts (not tracked; stored in cache):
  - `/.cache/question-theme-summary/`
  - `/.cache/model-themes/`
  - `/.cache/theme_details/`

## Rebuild options
- Full build from analysis + metadata: `uv run python preprocess.py`
- Static-only rebuild from cache artifacts: `uv run python preprocess.py --static-only`
- Deploy the generated tree: `npx wrangler pages deploy dist --project-name speechmap`
