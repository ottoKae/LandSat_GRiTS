"""Append a second OLI WRS path and audit its full pixels without mosaicking."""

# ruff: noqa: E402
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

import numpy as np

from landsat_grits.access import resolve_item_assets
from landsat_grits.bands import PRODUCTS, source_band_for_item
from landsat_grits.catalog import scene_key
from landsat_grits.storage import atomic_json
from landsat_grits.workflow import ingest, load_manifest, store_for, verify

SPEC = importlib.util.spec_from_file_location(
    "verify_full_tile", REPO / "tooling/diagnostics/20261021_17mpu/verify_full_tile.py"
)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
EVIDENCE = REPO / "docs/evidence/shenzhen-20261021"


class TimePlane:
    """Expose one selected archive plane to the existing one-plane scanner."""

    def __init__(self, array, index):
        if not 0 <= index < array.shape[0]:
            raise IndexError(index)
        self.array, self.index = array, index

    def __getattr__(self, name):
        if name == "shape":
            return (1, *self.array.shape[1:])
        return getattr(self.array, name)

    def __getitem__(self, selection):
        if not isinstance(selection, tuple) or selection[0] != 0:
            raise ValueError("Scanner must request the selected plane with time index zero")
        return self.array[(self.index, *selection[1:])]


def main():
    report_path = EVIDENCE / "oli-l2-second-path-full-verify.json"
    report = {
        "status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Full local data and valid decoding for both OLI scenes; independent source samples for the new scene",
        "implicit_mosaic": False, "groups": {},
    }

    def save():
        atomic_json(report_path, report)

    try:
        previous = json.loads((EVIDENCE / "oli-l2-full-verify.json").read_text())
        if previous["status"] != "passed":
            raise ValueError("The initial OLI full-tile verification must pass before appending")
        manifest = load_manifest(EVIDENCE / "oli-l2-second-path-manifest.json")
        item = manifest["tiles"][0]["items"][0]
        report["ingest"] = ingest(manifest)
        report["structural_verification"] = verify(manifest)
        store = store_for(manifest, manifest["tiles"][0], PRODUCTS["oli-l2"])
        report["source_geometry_coverage"] = VERIFIER.geometry_coverage(store.grid, item)
        with store.lock:
            root, commits = store._open()
            if len(commits) != 2 or commits[1]["key"] != scene_key(item):
                raise AssertionError("Expected exactly two chronological OLI scenes")
            report["commits"] = commits
            report["source_orbit_properties"] = {
                k: v for k, v in item["properties"].items() if "wrs" in k or k == "platform"
            }
            assets = resolve_item_assets(
                item, store.access,
                asset_keys=[b.asset_key for bands in store.groups.values() for b in bands],
            )
            for name, bands in store.groups.items():
                group, grid = root[name], store.group_grids[name]
                if group["data"].shape != (2, len(bands), grid.width, grid.height):
                    raise AssertionError("OLI source scenes were merged or spatial grid changed")
                np.testing.assert_array_equal(group["x"][:], grid.x)
                np.testing.assert_array_equal(group["y"][:], grid.y)
                np.testing.assert_array_equal(group["scale"][1], [b.scale for b in bands])
                np.testing.assert_array_equal(group["offset"][1], [b.offset for b in bands])
                np.testing.assert_array_equal(
                    group["source_band"][1], [source_band_for_item(b, item) for b in bands]
                )
                result = {"shape_time_bands_x_y": list(group["data"].shape), "bands": []}
                report["groups"][name] = result
                old_records = {b["band"]: b for b in previous["stores"][0]["groups"][name]["bands"]}
                for bi, band in enumerate(bands):
                    print(f"Verify unchanged time 0 and new time 1: {name}/{band.name}", flush=True)
                    first = {key: TimePlane(group[key], 0) for key in ("data", "valid", "present")}
                    second = {key: TimePlane(group[key], 1) for key in ("data", "valid", "present")}
                    unchanged = VERIFIER.scan_band(first, bi, band)
                    if unchanged["decoded_sha256"] != old_records[band.name]["decoded_sha256"]:
                        raise AssertionError(f"Append modified previous scene {name}/{band.name}")
                    record = VERIFIER.scan_band(second, bi, band)
                    if record["present"] != (band.asset_key in item["assets"]):
                        raise AssertionError("Band presence differs from source item")
                    record["source_comparison"] = VERIFIER.compare_samples(
                        second, bi, band, grid, assets[band.asset_key], store.access, record
                    )
                    record["previous_plane_hash_unchanged"] = True
                    result["bands"].append(record)
                    save()
        report["repeat_ingest"] = ingest(manifest)
        if report["repeat_ingest"]["written"] or report["repeat_ingest"]["already_committed"] != 1:
            raise AssertionError("Repeat ingest did not skip exactly the second scene")
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = VERIFIER.safe_error(exc)
        print(report["error"], flush=True)
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
