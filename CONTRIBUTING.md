# Development

Use Python 3.12 or 3.13. Install the repository with `uv sync --extra dev --locked`.
Run commands from the repository root:

```sh
uv run --locked ruff check src tests examples tooling/release tooling/validation
uv run --locked python -c "from pathlib import Path; Path('.test-tmp').mkdir(exist_ok=True)"
uv run --locked pytest -p no:cacheprovider --basetemp .test-tmp/dev-run
uv build --out-dir dist
```

`--basetemp` is disposable and may be cleared by pytest. Never use an archive,
project directory or shared parent as its value. Use a short dedicated path on
Windows: deeply nested test outputs can exceed the host's path-length limit.
Tests create synthetic data and
do not download satellite imagery. One optional historical compatibility check
requires `LANDSAT_GRITS_LEGACY_MGRS` pointing at the exact historical grid table;
ordinary CI skips it.

Keep `tests/`, reusable validation tools, examples and `uv.lock` in Git so a fresh
checkout can reproduce the checks. Ignore test outputs, caches and environments,
not test source files. Optional full-tile checks live in `tooling/validation/`.

Keep imagery, credentials, `.env` files and runtime logs out of commits.
Save dated validation summaries, command logs and evidence receipts locally in
`logs/YYYY-MM-DD/<run-name>/`; this directory is ignored by Git. Keep test counts
and run histories out of the README. Record source paths and SHA-256 when archiving reports.
Use `docs/` for maintained usage and design guides. Historical acceptance evidence
and local `config/pilots/` settings are not part of the repository or distributions.
Changes to storage semantics need recovery and reader compatibility tests.

CI builds both source and wheel distributions, installs the wheel in a fresh
environment, runs the offline demo and checks bundled GUI resources. The first
`v0.2.0b1` tag publishes a GitHub prerelease only after all four matrix jobs pass.
It does not publish to PyPI or download images. See `docs/RELEASE.md`.
