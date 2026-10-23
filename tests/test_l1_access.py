"""Free/local/mocked USGS access coverage; no charged S3 requests are made."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import traceback

from affine import Affine
import numpy as np
import pytest
import rasterio
import requests
from shapely.geometry import box, mapping

from landsat_grits import access
from landsat_grits.access import (
    AssetAccessError,
    local_asset_index,
    raster_access,
    read_metadata,
    resolve_item_assets,
    snapshot_local_assets,
    validate_local_snapshot,
)
from landsat_grits.catalog import CatalogError, USGS_STAC_URL, search_items
from landsat_grits.bands import PRODUCTS
from landsat_grits.grid import GridSpec
from landsat_grits.storage import SceneStore, read_json

PRODUCT_ID = "LC09_L1TP_122039_20251208_20251208_02_T1"
BASE = f"collection02/level-1/standard/oli-tirs/2025/122/039/{PRODUCT_ID}"
GEOMETRY = {"type": "Point", "coordinates": [114.4, 31.1]}


@pytest.fixture(autouse=True)
def prevent_real_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Unit tests must not make live HTTP requests.")
    monkeypatch.setattr(requests.Session, "request", fail)


def asset(suffix):
    filename = f"{PRODUCT_ID}_{suffix}"
    return {
        "href": f"https://landsatlook.usgs.gov/data/{BASE}/{filename}",
        "alternate": {"s3": {
            "href": f"s3://usgs-landsat/{BASE}/{filename}",
            "storage:requester_pays": True,
        }},
    }


def scene(identifier=PRODUCT_ID, platform="LANDSAT_9", instruments=("OLI", "TIRS")):
    return {
        "id": identifier,
        "type": "Feature",
        "collection": "landsat-c2l1",
        "geometry": GEOMETRY,
        "properties": {
            "datetime": "2025-12-08T02:50:27Z",
            "platform": platform,
            "instruments": list(instruments),
            "eo:cloud_cover": 10,
        },
        "assets": {
            "blue": asset("B2.TIF"),
            "pan": asset("B8.TIF"),
            "lwir11": asset("B10.TIF"),
            "qa_radsat": asset("QA_RADSAT.TIF"),
            "MTL.json": asset("MTL.json"),
            "VAA": asset("VAA.TIF"),
            "thumbnail": asset("thumb_small.jpeg"),
        },
    }


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.body

    def close(self):
        pass


class Session:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        return self.pages.pop(0)


def page(items, next_link=None):
    return Response({"features": items, "links": [next_link] if next_link else []})


def search(session, **kwargs):
    platforms = kwargs.pop("platforms", ("landsat-9",))
    return search_items(
        "landsat-c2l1", GEOMETRY, "2025-01-01", "2025-12-31",
        platforms, session=session, source="usgs", **kwargs,
    )


def test_usgs_source_platform_normalization_and_post_pagination_preserve_properties():
    original = scene()
    session = Session(
        page([original], {
            "rel": "next", "href": f"{USGS_STAC_URL}/search", "method": "POST",
            "merge": False, "body": {"next": "cursor", "collections": ["landsat-c2l1"]},
        }),
        page([original, scene("another-scene")]),
    )
    result = search(session)
    assert len(result) == 2
    assert result[0]["properties"]["platform"] == "LANDSAT_9"
    assert original["properties"]["platform"] == "LANDSAT_9"
    assert session.calls[0][1] == f"{USGS_STAC_URL}/search"
    assert session.calls[1][2]["json"] == {
        "next": "cursor", "collections": ["landsat-c2l1"],
    }
    assert result[0]["id"] == PRODUCT_ID


def test_usgs_instrument_filter_precedes_pilot_limit_and_keeps_original_case():
    mss = scene("mss", platform="LANDSAT_5", instruments=("MSS",))
    tm = scene("tm", platform="LANDSAT_5", instruments=("TM",))
    session = Session(
        page([mss], {"rel": "next", "href": f"{USGS_STAC_URL}/search?next=p2"}),
        page([tm]),
    )
    result = search_items(
        "landsat-c2l1", GEOMETRY, "2025-01-01", "2025-12-31",
        ("landsat-5",), session=session, source="usgs", instruments=("tm",), limit=1,
    )
    assert [item["id"] for item in result] == ["tm"]
    assert result[0]["properties"]["instruments"] == ["TM"]
    assert len(session.calls) == 2


def test_etm_plus_instrument_spelling_and_late_pagination_error():
    etm = scene("LE07_L1TP_122039_20251208_20251208_02_T1",
                platform="LANDSAT_7", instruments=("ETM+",))
    assert search(Session(page([etm])), platforms=("landsat-7",),
                  instruments=("ETM",), limit=1) == [etm]
    session = Session(
        page([etm], {"rel": "next", "href": f"{USGS_STAC_URL}/search?next=p2"}),
        Response({}, 503),
    )
    with pytest.raises(CatalogError, match="503"):
        search(session)


def test_unknown_catalog_source_rejected():
    with pytest.raises(ValueError, match="source"):
        search_items(
            "landsat-c2l1", GEOMETRY, "2025-01-01", "2025-12-31",
            (), source="unknown",
        )


@pytest.mark.parametrize("operation", [
    lambda item: resolve_item_assets(item),
    lambda item: read_metadata(item),
    lambda item: raster_access(item["assets"]["blue"]).__enter__(),
])
def test_usgs_default_fails_before_any_asset_request(operation, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("AWS must never be initialized without explicit configuration.")
    monkeypatch.setattr(access, "_boto_session", fail)
    with pytest.raises(AssetAccessError, match="aws-requester-pays"):
        operation(scene())


@pytest.fixture
def unpacked(tmp_path):
    root = tmp_path / "products"
    folder = root / PRODUCT_ID
    folder.mkdir(parents=True)
    for suffix, dtype, value, resolution in (
        ("B2.TIF", "uint16", 12345, 30),
        ("B8.TIF", "uint16", 23456, 15),
        ("B10.TIF", "uint16", 30000, 30),
        ("QA_RADSAT.TIF", "uint16", 0, 30),
    ):
        with rasterio.open(
            folder / f"{PRODUCT_ID}_{suffix}", "w", driver="GTiff",
            width=3, height=2, count=1, dtype=dtype, crs="EPSG:32650",
            transform=Affine(resolution, 0, 200000, 0, -resolution, 3500000),
        ) as dst:
            dst.write(np.full((2, 3), value, dtype=dtype), 1)
    metadata = {"LANDSAT_METADATA_FILE": {
        "PRODUCT_CONTENTS": {"LANDSAT_PRODUCT_ID": PRODUCT_ID},
        "IMAGE_ATTRIBUTES": {"SPACECRAFT_ID": "LANDSAT_9"},
        "LEVEL1_RADIOMETRIC_RESCALING": {"RADIANCE_MULT_BAND_10": 0.001},
        "LEVEL1_THERMAL_CONSTANTS": {"K1_CONSTANT_BAND_10": 799.0284},
    }}
    (folder / f"{PRODUCT_ID}_MTL.json").write_text(json.dumps(metadata), encoding="utf-8")
    return root, metadata


def test_local_resolution_exact_native_files_preserves_provenance_and_qa_zero(unpacked):
    root, metadata = unpacked
    item = scene()
    before = deepcopy(item)
    config = {"mode": "local", "directory": str(root)}
    keys = ("blue", "pan", "lwir11", "qa_radsat")
    resolved = resolve_item_assets(item, config, asset_keys=keys)
    assert item == before
    assert all(Path(resolved[key]["href"]).is_file() for key in (*keys, "MTL.json"))
    assert resolved["VAA"] == item["assets"]["VAA"]  # Not selected by this writer profile.
    assert read_metadata({**item, "assets": resolved}, config) == metadata
    with raster_access(resolved["qa_radsat"], config) as href:
        with rasterio.open(href) as source:
            assert source.read(1).dtype == np.dtype("uint16")
            assert np.count_nonzero(source.read(1)) == 0
    with raster_access(resolved["pan"], config) as href:
        with rasterio.open(href) as source:
            assert source.transform.a == 15


def test_already_resolved_local_metadata_does_not_enumerate_again(unpacked, monkeypatch):
    root, metadata = unpacked
    config = {"mode": "local", "directory": str(root)}
    item = scene()
    resolved = resolve_item_assets(item, config, asset_keys=("blue",))
    monkeypatch.setattr(
        access, "_local_index",
        lambda root: pytest.fail("Resolved local files must not trigger a second directory scan."),
    )
    assert read_metadata({**item, "assets": resolved}, config) == metadata
    with raster_access(resolved["blue"], config) as href:
        assert Path(href).is_file()


def test_local_metadata_can_resolve_original_item_without_loading_other_bands(unpacked):
    root, metadata = unpacked
    assert read_metadata(scene(), {"mode": "local", "directory": str(root)}) == metadata


def test_local_default_resolution_rejects_missing_unselected_by_profile_angle(unpacked):
    root, _ = unpacked
    with pytest.raises(AssetAccessError, match="VAA.TIF"):
        resolve_item_assets(scene(), {"mode": "local", "directory": str(root)})


def test_local_duplicates_and_wrong_processing_version_rejected(unpacked):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    duplicate = root / "duplicate"
    duplicate.mkdir()
    filename = f"{PRODUCT_ID}_B2.TIF"
    (duplicate / filename).write_bytes(b"duplicate")
    with pytest.raises(AssetAccessError, match="Duplicate"):
        resolve_item_assets(scene(), config, asset_keys=("blue",))
    item = scene()
    item["assets"]["blue"]["href"] = item["assets"]["blue"]["href"].replace(
        "_20251208_02_T1_B2", "_20251209_02_T1_B2"
    )
    with pytest.raises(AssetAccessError, match="Missing"):
        resolve_item_assets(item, config, asset_keys=("blue",))


def test_local_config_requires_absolute_directory():
    with pytest.raises(ValueError, match="absolute"):
        resolve_item_assets(scene(), {"mode": "local", "directory": "relative"})


def test_local_enumeration_error_is_not_treated_as_missing(unpacked, monkeypatch):
    root, _ = unpacked
    def failed_walk(root, *, followlinks, onerror):
        onerror(PermissionError("unreadable directory"))
        return iter(())
    monkeypatch.setattr(access.os, "walk", failed_walk)
    with pytest.raises(AssetAccessError, match="enumerate"):
        resolve_item_assets(
            scene(), {"mode": "local", "directory": str(root)}, asset_keys=("blue",)
        )


def test_public_local_files_need_no_aws_configuration(unpacked):
    root, metadata = unpacked
    local_item = {
        "assets": {
            "mtl.json": {"href": str(root / PRODUCT_ID / f"{PRODUCT_ID}_MTL.json")}
        }
    }
    assert read_metadata(local_item) == metadata
    path = root / PRODUCT_ID / f"{PRODUCT_ID}_B2.TIF"
    with raster_access({"href": str(path)}) as href:
        assert href == str(path)


def test_pc_raster_signing_context_does_not_mutate_asset(monkeypatch):
    original = {"href": "https://example.blob.core.windows.net/container/band.TIF"}
    monkeypatch.setattr(access, "sign_href", lambda href: href + "?sig=short-lived")
    with raster_access(original) as href:
        assert href.endswith("?sig=short-lived")
    assert "sig=" not in original["href"]


def test_aws_raster_uses_explicit_session_and_alternate_inside_env(monkeypatch):
    events = []
    fake_boto = object()
    monkeypatch.setattr(access, "_boto_session", lambda options: fake_boto)
    class FakeAWS:
        def __init__(self, **kwargs):
            events.append(("aws", kwargs))
    class FakeEnv:
        def __init__(self, **kwargs):
            events.append(("env", kwargs))
        def __enter__(self):
            events.append("enter")
        def __exit__(self, *args):
            events.append("exit")
    monkeypatch.setattr(access, "AWSSession", FakeAWS)
    monkeypatch.setattr(access.rasterio, "Env", FakeEnv)
    source = asset("B8.TIF")
    before = deepcopy(source)
    with raster_access(source, {"mode": "aws-requester-pays", "aws_profile": "research"}) as href:
        assert href == source["alternate"]["s3"]["href"]
        assert events[-1] == "enter"
    assert events[-1] == "exit"
    assert events[0][1] == {
        "session": fake_boto, "aws_unsigned": False, "requester_pays": True,
    }
    assert source == before


def test_aws_metadata_get_object_declares_request_payer_and_closes_body(monkeypatch):
    metadata = {"LANDSAT_METADATA_FILE": {
        "PRODUCT_CONTENTS": {"LANDSAT_PRODUCT_ID": PRODUCT_ID},
        "RADIOMETRIC": {"MULT": 0.002},
        "source": "https://example.org/MTL.json?X-Amz-Signature=secret&X-Amz-Credential=key",
    }}
    body = BytesIO(json.dumps(metadata).encode())
    calls = []
    class Client:
        def get_object(self, **kwargs):
            calls.append(kwargs)
            return {"Body": body}
        def close(self):
            calls.append("closed")
    class Boto:
        def client(self, name):
            assert name == "s3"
            return Client()
    monkeypatch.setattr(access, "_boto_session", lambda options: Boto())
    item = scene()
    original = deepcopy(item)
    result = read_metadata(item, {"mode": "aws-requester-pays"})
    assert calls[0] == {
        "Bucket": "usgs-landsat",
        "Key": f"{BASE}/{PRODUCT_ID}_MTL.json",
        "RequestPayer": "requester",
    }
    assert calls[-1] == "closed"
    assert body.closed
    assert result["LANDSAT_METADATA_FILE"]["RADIOMETRIC"] == {"MULT": 0.002}
    assert result["LANDSAT_METADATA_FILE"]["source"] == "https://example.org/MTL.json"
    assert item == original


def test_aws_dependency_missing_has_actionable_error_without_network(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(AssetAccessError, match="optional boto3"):
        read_metadata(scene(), {"mode": "aws-requester-pays"})


@pytest.mark.parametrize("document, message", [
    ({}, "lacks"),
    ({"LANDSAT_METADATA_FILE": {"PRODUCT_CONTENTS": {
        "LANDSAT_PRODUCT_ID": "LC09_DIFFERENT_PRODUCT",
    }}}, "does not match"),
])
def test_usgs_rejects_unidentified_or_wrong_mtl_product(unpacked, document, message):
    root, _ = unpacked
    (root / PRODUCT_ID / f"{PRODUCT_ID}_MTL.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
    with pytest.raises(AssetAccessError, match=message):
        read_metadata(scene(), {"mode": "local", "directory": str(root)})


def test_usgs_missing_mtl_asset_rejected_but_legacy_absence_supported():
    item = scene()
    del item["assets"]["MTL.json"]
    with pytest.raises(AssetAccessError, match="require an MTL"):
        read_metadata(item)
    assert read_metadata({"collection": "landsat-c2-l1", "assets": {}}) == {}


def test_aws_rejects_missing_alternate_and_inline_credentials():
    with pytest.raises(AssetAccessError, match="alternate"):
        with raster_access(
            {"href": "https://landsatlook.usgs.gov/data/B1.TIF"},
            {"mode": "aws-requester-pays"},
        ):
            pass
    with pytest.raises(ValueError, match="credential chain"):
        resolve_item_assets(scene(), {
            "mode": "aws-requester-pays", "aws_access_key_id": "do-not-persist",
        })


@pytest.mark.parametrize("instruments", [("OLI-2", "TIRS-2"), ("OLI_TIRS",), None])
def test_instrument_filter_reuses_product_identity_rules(instruments):
    item = scene()
    if instruments is None:
        item["properties"].pop("instruments")
    else:
        item["properties"]["instruments"] = list(instruments)
    original = deepcopy(item)
    assert search(Session(page([item])), instruments=("OLI", "TIRS"), limit=1) == [original]
    assert item == original


def test_unknown_or_contradictory_instruments_are_not_silently_omitted():
    missing = scene("not-a-full-product-id")
    missing["properties"].pop("instruments")
    contradictory = scene(instruments=("TM",))
    for item in (missing, contradictory):
        with pytest.raises(CatalogError, match="instruments"):
            search(Session(page([item])), instruments=("OLI", "TIRS"))


@pytest.mark.parametrize("href", [
    "/vsis3/usgs-landsat/scene_B1.TIF",
    "/vsicurl/https://landsatlook.usgs.gov/data/scene_B1.TIF",
    "/vsizip/vsis3/usgs-landsat/archive.zip/scene_B1.TIF",
])
def test_gdal_wrappers_cannot_bypass_explicit_access(href, monkeypatch):
    monkeypatch.setattr(
        access.rasterio, "Env", lambda **kwargs: pytest.fail("Must reject before GDAL opens.")
    )
    with pytest.raises(AssetAccessError, match="virtual filesystem"):
        with raster_access({"href": href}):
            pass


def test_local_job_indexes_once_and_rebuilds_for_next_job(unpacked, monkeypatch):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    original_walk = access.os.walk
    scans = []
    def tracked_walk(*args, **kwargs):
        scans.append(args[0])
        return original_walk(*args, **kwargs)
    monkeypatch.setattr(access.os, "walk", tracked_walk)
    with local_asset_index(config):
        resolve_item_assets(scene(), config, asset_keys=("blue",))
        resolve_item_assets(scene(), config, asset_keys=("pan",))
    assert len(scans) == 1
    with local_asset_index(config):
        resolve_item_assets(scene(), config, asset_keys=("blue",))
    assert len(scans) == 2
    resolve_item_assets(scene(), config, asset_keys=("blue",))
    assert len(scans) == 3


def test_local_job_rejects_new_duplicates_before_success_and_discards_cache(unpacked):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    with pytest.raises(AssetAccessError, match="directory changed"):
        with local_asset_index(config):
            resolve_item_assets(scene(), config, asset_keys=("blue",))
            duplicate = root / "new-duplicate"
            duplicate.mkdir()
            (duplicate / f"{PRODUCT_ID}_B2.TIF").write_bytes(b"duplicate")
    with local_asset_index(config):
        with pytest.raises(AssetAccessError, match="Duplicate"):
            resolve_item_assets(scene(), config, asset_keys=("blue",))


def test_local_job_detects_changed_used_file_and_clears_context(unpacked):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    with pytest.raises(AssetAccessError, match="file changed"):
        with local_asset_index(config):
            resolved = resolve_item_assets(scene(), config, asset_keys=("blue",))
            Path(resolved["blue"]["href"]).write_bytes(b"changed source")
    assert access._LOCAL_JOB.get() is None


def test_local_job_is_lazy_for_fully_committed_resume(unpacked, monkeypatch):
    root, _ = unpacked
    monkeypatch.setattr(
        access.os, "walk", lambda *args, **kwargs: pytest.fail("No source access was needed.")
    )
    with local_asset_index({"mode": "local", "directory": str(root)}):
        pass


@pytest.mark.parametrize("explicit_local", [False, True])
def test_local_raster_change_fails_before_caller_can_commit(unpacked, explicit_local):
    root, _ = unpacked
    path = root / PRODUCT_ID / f"{PRODUCT_ID}_B2.TIF"
    config = {"mode": "local", "directory": str(root)} if explicit_local else None
    reached_commit = False
    with pytest.raises(AssetAccessError, match="changed during raster read"):
        with raster_access({"href": str(path)}, config):
            with path.open("ab") as stream:
                stream.write(b"changed")
        reached_commit = True
    assert not reached_commit


def test_changed_local_mtl_is_rejected_at_read_boundary(unpacked, monkeypatch):
    root, _ = unpacked
    path = root / PRODUCT_ID / f"{PRODUCT_ID}_MTL.json"
    original_read = Path.read_text
    def changed_read(self, *args, **kwargs):
        result = original_read(self, *args, **kwargs)
        if self == path:
            self.write_text(result + " ", encoding="utf-8")
        return result
    monkeypatch.setattr(Path, "read_text", changed_read)
    with pytest.raises(AssetAccessError, match="changed during metadata read"):
        read_metadata(scene(), {"mode": "local", "directory": str(root)})


def test_changed_raster_rolls_back_scene_in_active_local_job(unpacked, tmp_path, monkeypatch):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    path = root / PRODUCT_ID / f"{PRODUCT_ID}_B2.TIF"
    grid = GridSpec(
        "50RKV", "EPSG:32650", Affine(30, 0, 200000, 0, -30, 3500000),
        3, 2, 30, mapping(box(114, 31, 115, 32)), "synthetic.parquet", "a" * 64,
    )
    product = PRODUCTS["oli-l2"]
    item = {
        "id": "local-change", "collection": product.collection, "geometry": GEOMETRY,
        "properties": {"datetime": "2025-12-08T02:50:27Z", "platform": "landsat-9"},
        "assets": {band.asset_key: {"href": str(path)} for band in product.optical_bands},
    }
    store = SceneStore(tmp_path / "archive.zarr", product, grid, chunk=16, access=config)
    original_open = access.rasterio.open
    def opened_then_changed(href, *args, **kwargs):
        result = original_open(href, *args, **kwargs)
        with path.open("ab") as stream:
            stream.write(b"changed after raster open")
        return result
    monkeypatch.setattr(access.rasterio, "open", opened_then_changed)
    with pytest.raises(AssetAccessError, match="changed during raster read"):
        with local_asset_index(config):
            store.append(item)
    assert read_json(store.commits_path) == []
    assert store.audit()["scenes"] == 0


@pytest.mark.parametrize("mode", ["public", "aws-requester-pays"])
def test_raster_error_does_not_expose_signed_url(monkeypatch, mode):
    signed = "https://example.blob.core.windows.net/band.TIF?sig=secret-token"
    monkeypatch.setattr(access, "sign_href", lambda href: signed)
    if mode == "aws-requester-pays":
        monkeypatch.setattr(access, "_boto_session", lambda options: object())
        monkeypatch.setattr(access, "AWSSession", lambda **kwargs: None)
    source = asset("B2.TIF") if mode == "aws-requester-pays" else {"href": signed}
    with pytest.raises(AssetAccessError, match="Raster access failed") as error:
        with raster_access(source, {"mode": mode}):
            raise rasterio.errors.RasterioIOError(f"Could not read {signed}")
    assert "secret-token" not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_raster_access_preserves_business_validation_errors(monkeypatch):
    monkeypatch.setattr(access, "sign_href", lambda href: href)
    failure = ValueError("Unexpected source nodata")
    with pytest.raises(ValueError) as error:
        with raster_access({"href": "https://example.org/band.TIF"}):
            raise failure
    assert error.value is failure


def test_local_snapshot_includes_mtl_and_ignores_unused_assets(unpacked):
    root, _ = unpacked
    config = {"mode": "local", "directory": str(root)}
    assets = resolve_item_assets(scene(), config, asset_keys=("blue",))
    snapshot = snapshot_local_assets(assets, asset_keys=["blue", "missing"])
    assert set(snapshot) == {
        root / PRODUCT_ID / f"{PRODUCT_ID}_{suffix}" for suffix in ("B2.TIF", "MTL.json")
    }
    validate_local_snapshot(snapshot)
    with (root / PRODUCT_ID / f"{PRODUCT_ID}_MTL.json").open("a", encoding="utf-8") as stream:
        stream.write(" ")
    with pytest.raises(AssetAccessError, match="changed before scene commit"):
        validate_local_snapshot(snapshot)
    assert snapshot_local_assets({"remote": asset("B2.TIF")}) == {}
