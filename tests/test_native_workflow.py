"""Local native L1 files through the real plan/run/verify CLI and resume path."""

from copy import deepcopy
import json

from affine import Affine
import geopandas as gpd
import numpy as np
import pytest
import rasterio
from shapely.geometry import box, mapping
import yaml
import zarr

from landsat_grits import workflow
from landsat_grits.cli import main
from landsat_grits.storage import open_group


# Independent expectations: do not build source fixtures from the product registry.
_TM_OPTICAL = (
    ("blue", "B1"), ("green", "B2"), ("red", "B3"),
    ("nir08", "B4"), ("swir16", "B5"), ("swir22", "B7"),
)
_CASES = {
    "tm-l1": {
        "mission": 5, "prefix": "LT05", "year": 2000, "instruments": ["TM"],
        "dtype": "uint8",
        "groups": {"optical": _TM_OPTICAL, "thermal": (("lwir", "B6"),)},
    },
    "etm-l1": {
        "mission": 7, "prefix": "LE07", "year": 2000, "instruments": ["ETM"],
        "dtype": "uint8",
        "groups": {
            "optical": _TM_OPTICAL,
            "thermal": (("lwir", "B6_VCID_1"), ("lwir_high", "B6_VCID_2")),
            "pan": (("pan", "B8"),),
        },
    },
    "oli-l1": {
        "mission": 8, "prefix": "LC08", "year": 2020, "instruments": ["OLI", "TIRS"],
        "dtype": "uint16",
        "groups": {
            "optical": (
                ("coastal", "B1"), ("blue", "B2"), ("green", "B3"), ("red", "B4"),
                ("nir08", "B5"), ("swir16", "B6"), ("swir22", "B7"), ("cirrus", "B9"),
            ),
            "thermal": (("lwir11", "B10"), ("lwir12", "B11")),
            "pan": (("pan", "B8"),),
        },
    },
}
_QA = (("qa_pixel", "QA_PIXEL"), ("qa_radsat", "QA_RADSAT"))
# The polygon below snaps to these independently defined, non-square grids.
_GRIDS = {
    30: (2, 4, Affine(30, 0, 500100, 0, -30, 210)),
    15: (4, 7, Affine(15, 0, 500100, 0, -15, 195)),
}


def _cli_json(capsys, *arguments):
    status = main(list(map(str, arguments)))
    captured = capsys.readouterr()
    assert status == 0, captured.err or captured.out
    assert not captured.err
    return json.loads(captured.out)


@pytest.mark.parametrize("product_key", ["tm-l1", "etm-l1", "oli-l1"])
def test_native_local_files_plan_run_verify_and_idempotent_resume(
    tmp_path, monkeypatch, capsys, product_key,
):
    case = _CASES[product_key]
    groups = {**case["groups"], "qa": _QA}
    day = f"{case['year']}0601"
    identifier = f"{case['prefix']}_L1TP_190056_{day}_{day}_02_T1"
    source_root = tmp_path / "unpacked-products"
    source_dir = source_root / identifier
    source_dir.mkdir(parents=True)
    output = tmp_path / "archive"
    assert not output.is_relative_to(source_root)
    assert not source_root.is_relative_to(output)

    table = gpd.GeoDataFrame(
        {"mgrs_tile_id": ["31NEA"], "utm_epsg": [32631]},
        geometry=[box(500101, 101, 500159, 189)], crs="EPSG:32631",
    ).to_crs("EPSG:4326")
    table.to_parquet(tmp_path / "mgrs.parquet", index=False)
    geometry = mapping(table.geometry.iloc[0])
    assets, expected, product_contents = {}, {}, {"LANDSAT_PRODUCT_ID": identifier}
    band_index = 0
    for group_name, bands in groups.items():
        width, height, transform = _GRIDS[15 if group_name == "pan" else 30]
        for asset, native_band in bands:
            band_index += 1
            dtype = "uint16" if group_name == "qa" else case["dtype"]
            values = (
                np.arange(width * height).reshape(height, width) + 11 * band_index
            ).astype(dtype)
            nodata = 0
            if asset == "qa_pixel":
                values[:] = 8  # A non-fill QA code is retained, not cloud-masked.
                nodata = 1
            elif asset == "qa_radsat":
                values[:] = 0  # Zero QA remains valid, even if its chunk is omitted.
                nodata = None
            filename = f"{identifier}_{native_band}.TIF"
            with rasterio.open(
                source_dir / filename, "w", driver="GTiff",
                width=width, height=height, count=1, dtype=dtype,
                crs="EPSG:32631", transform=transform, nodata=nodata,
            ) as dataset:
                dataset.write(values, 1)
            # Remote identity exercises exact native-basename local resolution.
            assets[asset] = {
                "href": f"https://landsatlook.usgs.gov/data/synthetic/{filename}",
                "type": "image/tiff; application=geotiff",
            }
            expected[asset] = values
            product_contents[f"FILE_NAME_BAND_{native_band.removeprefix('B')}"] = filename
    mtl = {
        "LANDSAT_METADATA_FILE": {
            "PRODUCT_CONTENTS": product_contents,
            "IMAGE_ATTRIBUTES": {"SPACECRAFT_ID": f"LANDSAT_{case['mission']}"},
            "LEVEL1_RADIOMETRIC_RESCALING": {"RADIANCE_MULT_BAND_1": 0.125},
        }
    }
    mtl_name = f"{identifier}_MTL.json"
    (source_dir / mtl_name).write_text(json.dumps(mtl), encoding="utf-8")
    assets["mtl.json"] = {
        "href": f"https://landsatlook.usgs.gov/data/synthetic/{mtl_name}",
        "type": "application/json",
    }
    item = {
        "type": "Feature", "stac_version": "1.0.0", "collection": "landsat-c2l1",
        "id": identifier, "geometry": geometry, "assets": assets,
        "properties": {
            "datetime": f"{case['year']}-06-01T10:00:00Z",
            "platform": f"LANDSAT_{case['mission']}",
            "instruments": case["instruments"], "eo:cloud_cover": 0,
        },
    }
    searches = []

    def search(collection, queried_geometry, start, end, platforms, **options):
        searches.append(options)
        assert collection == "landsat-c2l1"
        assert queried_geometry == geometry
        assert platforms == (f"landsat-{case['mission']}",)
        assert options["source"] == "usgs"
        assert set(options["instruments"]) == set(case["instruments"])
        assert start.startswith(f"{case['year']}-06-01")
        assert end.startswith(f"{case['year']}-06-01")
        return [deepcopy(item)]

    # Only catalog discovery is mocked; grids, files, MTL, warping and Zarr are real.
    monkeypatch.setattr(workflow, "search_items", search)
    config = tmp_path / "native.yaml"
    config.write_text(yaml.safe_dump({
        "mgrs_path": "mgrs.parquet", "output": "archive", "tiles": ["31NEA"],
        "start": f"{case['year']}-06-01", "end": f"{case['year']}-06-01",
        "products": [product_key], "missions": [case["mission"]], "chunk": 16,
        "access": {"mode": "local", "directory": "unpacked-products"},
    }), encoding="utf-8")
    manifest_path = tmp_path / "frozen.json"
    planned = _cli_json(capsys, "plan", config, "--manifest", manifest_path)
    assert planned["search_complete"] is True
    assert not output.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tile = manifest["tiles"][0]
    assert tile["scene_counts"] == {product_key: 1}
    assert tile["items"] == [json.loads(json.dumps(item))]
    assert len(searches) == 1
    for group_name, grid in tile["group_grids"][product_key].items():
        resolution = 15 if group_name == "pan" else 30
        width, height, transform = _GRIDS[resolution]
        assert (grid["width"], grid["height"], grid["resolution"]) == (
            width, height, resolution,
        )
        assert grid["transform"] == list(transform)[:6]

    result = _cli_json(capsys, "run", manifest_path)
    assert result["manifest_satisfied"] is True
    assert (result["written"], result["already_committed"]) == (1, 0)
    receipt_path = output / f"receipt-{manifest['manifest_sha256'][:16]}.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == result
    verified = _cli_json(capsys, "verify", manifest_path)
    assert verified["manifest_satisfied"] is True
    assert verified["missing_scenes"] == []
    assert [store["scenes"] for store in verified["stores"]] == [1]

    store_path = output / "31NEA" / product_key / "30m.zarr"
    root = zarr.open_group(str(store_path), mode="r")
    assert set(root.group_keys()) == set(groups)
    for group_name, bands in groups.items():
        group = root[group_name]
        resolution = 15 if group_name == "pan" else 30
        width, height, transform = _GRIDS[resolution]
        assert group["data"].shape == (1, len(bands), width, height)
        assert group["data"].attrs["_ARRAY_DIMENSIONS"] == ["time", "bands", "x", "y"]
        assert group["bands"][:].tolist() == [asset for asset, _ in bands]
        assert group["source_band"][0].tolist() == [native for _, native in bands]
        dtype = "uint16" if group_name == "qa" else case["dtype"]
        assert group["data"].dtype == np.dtype(dtype)
        assert group["source_dtype"][0].tolist() == [dtype] * len(bands)
        assert group["present"][:].all()
        assert group["valid"][:].all()
        np.testing.assert_array_equal(group["scale"][:], np.ones((1, len(bands))))
        np.testing.assert_array_equal(group["offset"][:], np.zeros((1, len(bands))))
        np.testing.assert_array_equal(
            group["x"][:], transform.c + (np.arange(width) + 0.5) * resolution,
        )
        np.testing.assert_array_equal(
            group["y"][:], transform.f - (np.arange(height) + 0.5) * resolution,
        )
        for index, (asset, _) in enumerate(bands):
            np.testing.assert_array_equal(group["data"][0, index], expected[asset].T)
        with open_group(store_path, group=group_name, chunks=None) as dataset:
            assert dataset.data.dims == ("time", "bands", "x", "y")
            assert dataset.sizes["time"] == 1
            assert float(dataset.x[1] - dataset.x[0]) == resolution
    if product_key == "etm-l1":
        assert not np.array_equal(root["thermal/data"][0, 0], root["thermal/data"][0, 1])
    if "pan" in groups:
        assert root["pan/data"].shape[2:] == (4, 7)
        assert root["optical/data"].shape[2:] == (2, 4)

    ledger_path = store_path.with_name("30m.zarr.commits.json")
    ledger_before = ledger_path.read_bytes()
    commits = json.loads(ledger_before)
    assert len(commits) == 1
    provenance_path = store_path.with_name("30m.zarr.provenance") / commits[0]["provenance"]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["mtl"] == mtl
    assert provenance["item"] == json.loads(json.dumps(item))
    assert set(provenance["rasters"]) == set(expected)

    repeated = _cli_json(capsys, "run", manifest_path)
    assert repeated["manifest_satisfied"] is True
    assert (repeated["written"], repeated["already_committed"]) == (0, 1)
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == repeated
    assert [store["scenes"] for store in repeated["stores"]] == [1]
    assert ledger_path.read_bytes() == ledger_before
    assert len(searches) == 1
    resumed = zarr.open_group(str(store_path), mode="r")
    for group_name, bands in groups.items():
        assert resumed[group_name]["data"].shape[0] == 1
        for index, (asset, _) in enumerate(bands):
            np.testing.assert_array_equal(
                resumed[group_name]["data"][0, index], expected[asset].T,
            )
