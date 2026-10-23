"""Native L1 discovery contracts, per-group planning, and access preflight."""

from pathlib import Path

from affine import Affine
import pytest
from shapely.geometry import box, mapping
import yaml

from landsat_grits import workflow
from landsat_grits.bands import PRODUCTS
from landsat_grits.grid import GridSpec
from landsat_grits.storage import atomic_json


def _item(identifier, collection, platform, instruments, date="2020-01-01T12:00:00Z"):
    return {
        "type": "Feature", "id": identifier, "collection": collection,
        "geometry": mapping(box(114, 28, 115, 29)),
        "properties": {
            "platform": platform, "instruments": instruments, "datetime": date,
        },
        "assets": {},
    }


@pytest.fixture
def planning(tmp_path, monkeypatch):
    config_path = tmp_path / "native.yaml"
    config_path.write_text(yaml.safe_dump({
        "mgrs_path": "source.parquet", "output": "archive", "tiles": ["50RKV"],
        "start": "1972-07-23", "end": "2026-10-20",
    }), encoding="utf-8")
    geometry = mapping(box(114, 28, 115, 29))

    def grid(_source, tile, resolution):
        width, height, x, y = {
            15: (13, 9, 300015, 3199985),
            30: (7, 5, 300000, 3200000),
            60: (4, 3, 300000, 3200040),
        }[resolution]
        return GridSpec(
            tile, "EPSG:32650", Affine(resolution, 0, x, 0, -resolution, y),
            width, height, resolution, geometry, "frozen.parquet", "a" * 64,
        )

    items = [
        _item("LM01_L1TP_024035_19720805_20200902_02_T2", "landsat-c2-l1",
              "landsat-1", ["MSS"], "1972-08-05T12:00:00Z"),
        _item("LT05_L1TP_123039_20000101_20200902_02_T1", "landsat-c2l1",
              "landsat-5", ["TM"]),
        _item("LE07_L1TP_123039_20200101_20200114_02_T1", "landsat-c2l1",
              "landsat-7", ["ETM"]),
        _item("LC08_L1TP_123039_20200101_20200114_02_T1", "landsat-c2l1",
              "landsat-8", ["OLI", "TIRS"]),
    ]
    searches = []

    def search(collection, geometry, start, end, platforms, **kwargs):
        searches.append({"collection": collection, "platforms": platforms, **kwargs})
        matching = [
            item for item in items if item["collection"] == collection
            and item["properties"]["platform"] in platforms
            and (not kwargs.get("instruments")
                 or set(item["properties"]["instruments"]) & set(kwargs["instruments"]))
        ]
        return matching[:kwargs["limit"]] if kwargs.get("limit") is not None else matching

    monkeypatch.setattr(workflow, "load_grid", grid)
    monkeypatch.setattr(workflow, "search_items", search)
    return workflow.load_config(config_path), searches


def test_native_defaults_freeze_pan_grid_and_estimate_each_resolution(
    planning, tmp_path, monkeypatch,
):
    config, searches = planning
    assert config["products"] == ["mss-l1", "tm-l1", "etm-l1", "oli-l1"]
    manifest = workflow.plan(config)
    tile = manifest["tiles"][0]
    assert tile["scene_counts"] == {"mss-l1": 1, "tm-l1": 1, "etm-l1": 1, "oli-l1": 1}
    assert tile["group_grids"]["oli-l1"]["pan"]["width"] == 13
    assert tile["group_grids"]["oli-l1"]["pan"]["height"] == 9
    assert tile["group_grids"]["oli-l1"]["pan"]["resolution"] == 15
    assert tile["group_grids"]["oli-l1"]["thermal"]["resolution"] == 30
    # Raw integer pixels + one byte valid mask; pan has 117 pixels, not 35.
    # MSS: 4 uint8 + 2 uint16 QA; TM: 7 uint8 + 2 uint16 QA;
    # ETM+: 8 uint8 at 30 m, pan uint8 at 15 m, 2 uint16 QA;
    # OLI: 10 uint16 at 30 m, pan uint16 at 15 m, 2 uint16 QA.
    assert manifest["estimated_uncompressed_array_bytes"] == (
        12 * (4 * 2 + 2 * 3)
        + 35 * (7 * 2 + 2 * 3)
        + 35 * (8 * 2 + 2 * 3) + 117 * 2
        + 35 * (10 * 3 + 2 * 3) + 117 * 3
    )
    usgs = [call for call in searches if call.get("source") == "usgs"]
    assert len(usgs) == 1
    assert set(usgs[0]["instruments"]) == {"TM", "ETM", "OLI", "TIRS"}
    path = tmp_path / "frozen.json"
    atomic_json(path, manifest)
    frozen = workflow.load_manifest(path)

    def no_grid_reload(*args, **kwargs):
        pytest.fail("Ingestion must use the frozen 15 m grid rather than reopening source parquet")

    monkeypatch.setattr(workflow, "load_grid", no_grid_reload)
    store = workflow.store_for(frozen, frozen["tiles"][0], PRODUCTS["oli-l1"])
    assert store.group_grids["pan"].width == 13
    assert store.group_grids["pan"].transform.c == 300015
    assert store.group_grids["optical"].width == 7


def test_tm_pilot_passes_instrument_filter_before_applying_limit(planning):
    config, searches = planning
    config["products"], config["missions"] = ["tm-l1"], [5]
    manifest = workflow.plan(config, limit_per_search=1)
    assert len(searches) == 1
    assert searches[0]["source"] == "usgs"
    assert searches[0]["collection"] == "landsat-c2l1"
    assert searches[0]["instruments"] == ("TM",)
    assert searches[0]["platforms"] == ("landsat-5",)
    assert searches[0]["limit"] == 1
    assert manifest["tiles"][0]["scene_counts"] == {"tm-l1": 1}
    assert manifest["selection"] == "pilot"
    assert manifest["search_complete"] is False


def test_auto_access_rejects_mixed_usgs_plan_before_even_earlier_mss_download(
    planning, monkeypatch,
):
    config, _ = planning
    manifest = workflow.plan(config)
    assert manifest["tiles"][0]["items"][0]["collection"] == "landsat-c2-l1"

    def no_download(*args, **kwargs):
        pytest.fail("USGS access preflight must precede every scene append")

    monkeypatch.setattr(workflow.SceneStore, "append", no_download)
    with pytest.raises(ValueError, match="access.mode=local"):
        workflow.ingest(manifest)
    assert not Path(config["output"]).exists()


def test_grid_reuse_and_chunk_estimate_follow_independent_pan_grid(planning, monkeypatch):
    from dataclasses import replace

    config, _ = planning
    config["chunk"] = 16
    original = workflow.load_grid
    calls = []

    def counted(source, tile, resolution):
        calls.append((tile, resolution))
        grid = original(source, tile, resolution)
        return replace(grid, width=33, height=17) if resolution == 15 else grid

    monkeypatch.setattr(workflow, "load_grid", counted)
    manifest = workflow.plan(config)
    assert calls == [("50RKV", 60), ("50RKV", 30), ("50RKV", 15)]
    # Pan spans 3 x 2 chunks, while all the 30/60 m groups fit one spatial chunk.
    assert manifest["estimated_data_and_valid_chunks_upper_bound"] == 2 * (6 + 9 + 16 + 18)


def test_local_output_inside_sources_is_rejected_before_writing(planning, monkeypatch):
    config, _ = planning
    config["access"] = {"mode": "local", "directory": str(Path(config["output"]).parent)}
    manifest = workflow.plan(config)

    def no_write(*args, **kwargs):
        pytest.fail("Overlapping output and source paths must fail before any append")

    monkeypatch.setattr(workflow.SceneStore, "append", no_write)
    with pytest.raises(ValueError, match="outside"):
        workflow.ingest(manifest)
    assert not Path(config["output"]).exists()


def test_local_index_validation_precedes_receipt_and_verify(planning, monkeypatch):
    from contextlib import contextmanager

    config, _ = planning
    config["access"] = {
        "mode": "local", "directory": str(Path(config["output"]).parent / "sources"),
    }
    manifest = workflow.plan(config)
    appended = []

    @contextmanager
    def changed_sources(access):
        yield
        raise ValueError("Source directory changed during ingestion")

    monkeypatch.setattr(workflow, "local_asset_index", changed_sources)
    monkeypatch.setattr(workflow.SceneStore, "append", lambda self, item: appended.append(item) or True)
    monkeypatch.setattr(workflow, "verify", lambda *args: pytest.fail("Must check sources first"))
    with pytest.raises(ValueError, match="Source directory changed"):
        workflow.ingest(manifest)
    assert len(appended) == 4
    assert not Path(config["output"]).exists()
