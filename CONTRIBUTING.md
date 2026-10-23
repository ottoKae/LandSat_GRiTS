# Development

Use Python 3.12 or 3.13. Install the repository with `uv sync --extra dev --locked`.
Run commands from the repository root:

```sh
uv run --locked ruff check src tests examples tooling/release
uv run --locked python -c "from pathlib import Path; Path('.test-tmp').mkdir(exist_ok=True)"
uv run --locked pytest -p no:cacheprovider --basetemp .test-tmp/dev-run
uv build --out-dir dist
```

`--basetemp` is disposable and may be cleared by pytest. Never use an archive,
project directory or shared parent as its value. Tests create synthetic data and
do not download satellite imagery. One optional historical compatibility check
requires `LANDSAT_GRITS_LEGACY_MGRS` pointing at the exact historical grid table;
ordinary CI skips it.

Keep source archives, credentials, `.env` files and runtime logs out of commits.
Save dated validation summaries, command logs and evidence receipts locally in
`logs/YYYY-MM-DD/<run-name>/`; this directory is ignored by Git. Keep test counts
and run histories out of the README. Record source paths and SHA-256 when archiving reports.
Historical acceptance evidence is not part of the wheel or source distribution.
Changes to storage semantics need recovery and reader compatibility tests.

CI builds both source and wheel distributions, installs the wheel in a fresh
environment, runs the offline demo and checks bundled GUI resources. The first
`v0.2.0b1` tag publishes a GitHub prerelease only after all four matrix jobs pass.
It does not publish to PyPI or download images. See `docs/RELEASE.md`.
