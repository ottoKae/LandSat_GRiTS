"""Integration checks for frozen discovery, mission selection, and verification."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from affine import Affine
import pytest
import yaml

from landsat_grits import workflow
from landsat_grits.catalog import CatalogError, scene_fingerprint, scene_key
from landsat_grits.cli import main
from landsat_grits.grid import GridSpec
from landsat_grits.storage import atomic_json


def _item(collection, platform, identifier):
    return {
        "type": "Feature",
        "collection": collection,
        "id": identifier,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[3, 1], [3.01, 1], [3.01, 1.01], [3, 1.01], [3, 1]]],
        },
        "properties": {
            "platform": platform,
            "datetime": "2002-06-01T10:00:00Z",
            "eo:cloud_cover": 12,
        },
        "assets": {},
    }


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump({
        "mgrs_path": "mgrs.parquet",
        "output": "archive",
        "tiles": ["31NEA"],
        "start": "1972-07-23",
        "end": "2026-10-20",
        "products": ["mss-l1", "tm-l2", "etm-l2", "oli-l2"],
    }), encoding="utf-8")
    return path


@pytest.fixture
def fixed_grid(monkeypatch):
    def load(_source, tile, resolution):
        return GridSpec(
            tile_id=tile,
            crs="EPSG:32631",
            transform=Affine(resolution, 0, 500100, 0, -resolution, 110100),
            width=int(120 / resolution),
            height=int(180 / resolution),
            resolution=resolution,
            geometry=_item("unused", "unused", "unused")["geometry"],
            source_path="synthetic-mgrs.parquet",
            source_sha256="0" * 64,
        )

    monkeypatch.setattr(workflow, "load_grid", load)
    return load


@pytest.fixture
def catalog(monkeypatch):
    # Synthetic scenes deliberately share acquisition times and, across
    # collections, an ID. Neither date nor an unscoped ID is a scene identity.
    items = [
        _item("landsat-c2-l1", "landsat-5", "shared-id"),
        _item("landsat-c2-l2", "landsat-5", "shared-id"),
        _item("landsat-c2-l2", "landsat-7", "etm-scene"),
        _item("landsat-c2-l2", "landsat-8", "oli-scene"),
    ]
    calls = []

    def search(collection, geometry, start, end, platforms, **kwargs):
        calls.append({
            "collection": collection, "platforms": platforms,
            "geometry": geometry, "limit": kwargs.get("limit"),
        })
        selected = [
            deepcopy(item) for item in items
            if item["collection"] == collection
            and item["properties"]["platform"] in platforms
        ]
        limit = kwargs.get("limit")
        return selected if limit is None else selected[:limit]

    monkeypatch.setattr(workflow, "search_items", search)
    return SimpleNamespace(items=items, calls=calls)


def test_frozen_inventory_keeps_same_time_scenes_and_separate_sensor_grids(
    config_file, fixed_grid, catalog, tmp_path,
):
    manifest = workflow.plan(workflow.load_config(config_file))
    tile = manifest["tiles"][0]
    assert tile["scene_counts"] == {
        "mss-l1": 1, "tm-l2": 1, "etm-l2": 1, "oli-l2": 1,
    }
    assert len(tile["items"]) == 4
    assert {scene_key(item) for item in tile["items"]} == {
        scene_key(item) for item in catalog.items
    }
    assert tile["grids"]["mss-l1"]["resolution"] == 60
    assert all(tile["grids"][key]["resolution"] == 30
               for key in ("tm-l2", "etm-l2", "oli-l2"))
    assert manifest["search_complete"] is True
    assert manifest["selection"] == "all_matching_catalog_items"
    assert manifest["estimated_uncompressed_array_bytes"] > 0
    assert not Path(manifest["config"]["output"]).exists()
    path = tmp_path / "frozen.json"
    atomic_json(path, manifest)
    loaded = workflow.load_manifest(path)
    assert loaded == json.loads(json.dumps(manifest))
    assert [scene_key(item) for item in loaded["tiles"][0]["items"]] == [
        scene_key(item) for item in tile["items"]
    ]


def test_mission_selection_keeps_both_landsat5_sensors_but_excludes_other_missions(
    config_file, fixed_grid, catalog,
):
    config = workflow.load_config(config_file)
    config["missions"] = [5, 6]
    manifest = workflow.plan(config)
    tile = manifest["tiles"][0]
    assert set(tile["grids"]) == {"mss-l1", "tm-l2"}
    assert tile["scene_counts"] == {"mss-l1": 1, "tm-l2": 1}
    assert {item["properties"]["platform"] for item in tile["items"]} == {"landsat-5"}
    assert len(catalog.calls) == 2
    assert all(call["platforms"] == ("landsat-5",) for call in catalog.calls)


def test_limited_search_is_explicitly_a_pilot(config_file, fixed_grid, catalog):
    manifest = workflow.plan(workflow.load_config(config_file), limit_per_search=1)
    assert manifest["selection"] == "pilot"
    assert manifest["search_complete"] is False
    assert manifest["limit_per_search"] == 1
    assert len(manifest["tiles"][0]["items"]) == 2
    assert all(call["limit"] == 1 for call in catalog.calls)


def test_landsat6_only_records_no_imagery_without_search_or_download(
    config_file, monkeypatch,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Landsat 6 must not trigger a grid, source search, or pixel download")

    monkeypatch.setattr(workflow, "load_grid", forbidden)
    monkeypatch.setattr(workflow, "search_items", forbidden)
    monkeypatch.setattr(workflow.SceneStore, "append", forbidden)
    config = workflow.load_config(config_file)
    config["missions"] = [6]
    manifest = workflow.plan(config)
    assert "No imagery" in manifest["mission_notes"]["6"]
    assert manifest["estimated_uncompressed_array_bytes"] == 0
    assert manifest["tiles"][0]["items"] == []
    assert manifest["tiles"][0]["grids"] == {}
    report = workflow.ingest(manifest)
    assert report["written"] == report["already_committed"] == 0
    assert report["stores"] == []
    assert not list(Path(config["output"]).rglob("*.zarr"))


def test_late_discovery_failure_does_not_publish_a_partial_manifest(
    config_file, fixed_grid, monkeypatch, tmp_path, capsys,
):
    calls = []

    def search(collection, *args, **kwargs):
        calls.append(collection)
        if collection == "landsat-c2-l1":
            return [_item(collection, "landsat-5", "already-discovered")]
        raise CatalogError("Second collection failed before discovery completed")

    monkeypatch.setattr(workflow, "search_items", search)
    destination = tmp_path / "must-not-exist.json"
    status = main(["plan", str(config_file), "--manifest", str(destination)])
    assert status == 1
    assert calls == ["landsat-c2-l1", "landsat-c2-l2"]
    assert not destination.exists()
    assert not (tmp_path / "archive").exists()
    assert "Second collection failed" in capsys.readouterr().err


def test_verify_rejects_a_changed_source_with_the_same_scene_identity(
    config_file, fixed_grid, catalog, monkeypatch, tmp_path,
):
    config = workflow.load_config(config_file)
    config["missions"], config["products"] = [5], ["tm-l2"]
    manifest = workflow.plan(config)
    expected = manifest["tiles"][0]["items"][0]
    committed = deepcopy(expected)
    committed["properties"]["eo:cloud_cover"] = 99
    identity = scene_key(expected)
    assert scene_key(committed) == identity
    store_path = tmp_path / "existing.zarr"
    store_path.mkdir()
    store = SimpleNamespace(path=store_path, audit=lambda: {
        "keys": [identity], "fingerprints": {identity: scene_fingerprint(committed)},
    })
    monkeypatch.setattr(workflow, "store_for", lambda *args: store)
    with pytest.raises(ValueError, match="differs from frozen manifest"):
        workflow.verify(manifest)


def test_verify_reports_missing_expected_scenes_despite_other_committed_scenes(
    config_file, fixed_grid, catalog, monkeypatch, tmp_path,
):
    config = workflow.load_config(config_file)
    config["missions"], config["products"] = [5], ["tm-l2"]
    manifest = workflow.plan(config)
    expected_key = scene_key(manifest["tiles"][0]["items"][0])
    different = _item("landsat-c2-l2", "landsat-5", "different-scene")
    different_key = scene_key(different)
    store_path = tmp_path / "existing.zarr"
    store_path.mkdir()
    store = SimpleNamespace(path=store_path, audit=lambda: {
        "keys": [different_key],
        "fingerprints": {different_key: scene_fingerprint(different)},
    })
    monkeypatch.setattr(workflow, "store_for", lambda *args: store)
    report = workflow.verify(manifest)
    assert report["manifest_satisfied"] is False
    assert report["missing_scenes"] == [f"31NEA/{expected_key}"]


def test_concurrent_planners_cannot_replace_the_same_manifest(config_file, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from landsat_grits import cli

    entered, release = Event(), Event()
    calls = []
    destination = tmp_path / "shared.json"

    def paused_plan(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(timeout=10), "Test planner was not released"
        return {"schema_version": 1, "selection": "first-plan"}

    monkeypatch.setattr(cli, "plan", paused_plan)
    command = ["plan", str(config_file), "--manifest", str(destination)]
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(main, command)
        try:
            assert entered.wait(timeout=10)
            assert main(command) == 1
        finally:
            release.set()
        assert first.result(timeout=10) == 0
    assert calls == [True]
    assert json.loads(destination.read_text(encoding="utf-8"))["selection"] == "first-plan"
    assert main(command) == 1
