from pathlib import Path

import pytest
import yaml

from landsat_grits.workflow import load_config, load_manifest, manifest_hash
from landsat_grits.storage import atomic_json


def test_config_paths_dates_and_defaults(tmp_path):
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump({
        "mgrs_path": "grid.parquet", "output": "archive", "tiles": ["50rkv"],
        "start": "1972-07-23", "end": "2026-10-20",
    }), encoding="utf-8")
    cfg = load_config(path)
    assert cfg["start"] == "1972-07-23T00:00:00+00:00"
    assert cfg["end"] == "2026-10-20T23:59:59.999999+00:00"
    assert cfg["tiles"] == ["50RKV"]
    assert Path(cfg["output"]) == tmp_path / "archive"
    assert cfg["missions"] == list(range(1, 10))


@pytest.mark.parametrize("extra", [
    {"max_cloud_cover": -1}, {"max_cloud_cover": 101},
    {"missions": [6.0]}, {"products": ["fake"]}, {"chunk": 0},
    {"resolutions": {"mss-l1": 0}}, {"start": "2025-01-01", "end": "2024-01-01"},
    {"max_cloud_cove": 10},
])
def test_bad_config_rejected(tmp_path, extra):
    cfg = {"mgrs_path": "grid.parquet", "output": "archive", "tiles": ["50RKV"],
           "start": "1972-07-23", "end": "2026-10-20"}
    cfg.update(extra)
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_manifest_tamper_detected(tmp_path):
    manifest = {"schema_version": 1, "tiles": [], "selection": "pilot"}
    manifest["manifest_sha256"] = manifest_hash(manifest)
    path = tmp_path / "manifest.json"
    atomic_json(path, manifest)
    assert load_manifest(path) == manifest
    manifest["selection"] = "all"
    atomic_json(path, manifest)
    with pytest.raises(ValueError, match="modified"):
        load_manifest(path)
