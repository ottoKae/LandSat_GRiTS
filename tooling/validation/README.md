# Full-tile validation helpers

These optional scripts support advanced pilot verification. They are not part of
the installed package or the quickstart. Their reusable checks are covered by
offline tests in `tests/`.

| Script | Purpose and prerequisites |
| --- | --- |
| `verify_full_tile.py` | Decode every local block and independently sample source pixels. Takes a frozen single-scene manifest and explicit `--report` path; use `--help` for options. |
| `prepare_pilot.py` | Select one complete-band scene per sensor for historical 17MPU acceptance tests. Retains the original date ranges and sibling S1-GRiTS grid path. Review these defaults before downloading. |
| `append_second_path.py` | Append and verify the second OLI scene from a prepared Shenzhen manifest. Requires the original scene's successful verification report. |
| `download.py` | Resume the historical 49QHE annual pilot in quarterly stores. Requires its frozen manifests and job list; preparation also uses the sibling GRiTS-Resolver configuration and original scene-count assertions. |

Generated reports, worker logs and preparation configs belong in
`logs/YYYY-MM-DD/<task>/`, which Git ignores. The fixed default task names are
`17mpu-pilot`, `shenzhen-pilot` and `yearly-sensors`. The first two use the local
run date; annual resume state stays at `logs/2026-10-22/yearly-sensors/` so workers
keep the same inputs across midnight. Before running a resume or append helper,
place its required manifests, job list and previous reports in its task directory.
For the generic verifier,
choose `--report` under the same log layout. Existing satellite archives remain
under `outputs/`; these scripts do not relocate them.

For ordinary installation, downloading and viewing, use
[`docs/QUICKSTART.md`](../../docs/QUICKSTART.md).
