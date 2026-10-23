"""Deterministic scene selection and independent full-tile audit helpers, offline."""

from copy import deepcopy
import importlib.util
from pathlib import Path

from affine import Affine
import numpy as np
from pyproj import Transformer
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from shapely.geometry import box, mapping
from shapely.ops import transform
import zarr

from landsat_grits.bands import PRODUCTS, QA_BANDS
from landsat_grits.grid import GridSpec
from landsat_grits.workflow import manifest_hash


def diagnostic(name):
    path = Path(__file__).parents[1] / "tooling/diagnostics/20261021_17mpu" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate_manifest(tmp_path):
    project = Transformer.from_crs("EPSG:32717", "EPSG:4326", always_xy=True).transform
    left, top, width, height = 600000, 9980000, 40, 30

    def footprint(fraction):
        return mapping(transform(project, box(
            left, top - height * 60, left + width * 60 * fraction, top
        )))

    grid = GridSpec(
        tile_id="17MPU", crs="EPSG:32717", transform=Affine(60, 0, left, 0, -60, top),
        width=width, height=height, resolution=60, geometry=footprint(1),
        source_path=str(tmp_path / "unused-grid.parquet"), source_sha256="a" * 64,
    )

    def item(identifier, coverage, cloud, *, complete=True):
        names = ["green", "red", "nir08", "nir09", "qa_pixel", "qa_radsat"]
        if not complete:
            names.remove("qa_radsat")
        return {
            "id": identifier, "collection": "landsat-c2-l1", "geometry": footprint(coverage),
            "properties": {"eo:cloud_cover": cloud, "platform": "landsat-5"},
            "assets": {name: {"href": f"https://example.invalid/{identifier}_{name}.tif"}
                       for name in names},
        }

    result = {
        "schema_version": 1, "config": {"products": ["mss-l1"], "chunk": 16},
        "selection": "all_matching_catalog_items", "search_complete": True,
        "estimated_uncompressed_array_bytes": 999999,
        "estimated_data_and_valid_chunks_upper_bound": 999999,
        "tiles": [{"tile": "17MPU", "grids": {"mss-l1": grid.to_dict()},
                   "scene_counts": {"mss-l1": 4}, "items": [
                       item("incomplete-full", 1, 0, complete=False),
                       item("less-coverage-clear", 0.95, 0),
                       item("high-coverage-cloudy", 0.98, 25),
                       item("high-coverage-clearer", 0.98004, 5),
                   ]}],
    }
    result["manifest_sha256"] = manifest_hash(result)
    return result


def test_full_tile_selection_ranks_complete_assets_coverage_then_cloud_and_preserves_input(
    candidate_manifest,
):
    prepare = diagnostic("prepare_pilot")
    before = deepcopy(candidate_manifest)
    result = prepare.select_one(candidate_manifest, "mss-l1")
    assert candidate_manifest == before
    assert result["tiles"][0]["items"][0]["id"] == "high-coverage-clearer"
    assert result["tiles"][0]["scene_counts"] == {"mss-l1": 1}
    assert result["pilot_selection"]["complete_band_candidates"] == 3
    assert result["pilot_selection"]["scene_footprint_fraction_of_output_rectangle"] == pytest.approx(
        0.98004, abs=1e-8
    )
    # Four uint8 optical + two uint16 QA bands, each with one byte of validity.
    assert result["estimated_uncompressed_array_bytes"] == 40 * 30 * (4 * 2 + 2 * 3)
    # 3 x 2 spatial chunks, six bands, data + valid.
    assert result["estimated_data_and_valid_chunks_upper_bound"] == 3 * 2 * 6 * 2
    assert result["selection"] == "pilot" and result["search_complete"] is False
    assert result["manifest_sha256"] == manifest_hash(result)
    assert result["manifest_sha256"] != before["manifest_sha256"]
    result["tiles"][0]["items"][0]["assets"]["green"]["href"] = "changed"
    assert candidate_manifest == before


@pytest.mark.parametrize("empty", [False, True])
def test_full_tile_selection_rejects_no_complete_candidate(candidate_manifest, empty):
    candidate_manifest["tiles"][0]["items"] = (
        [] if empty else candidate_manifest["tiles"][0]["items"][:1]
    )
    with pytest.raises(ValueError, match="No complete-band scene"):
        diagnostic("prepare_pilot").select_one(candidate_manifest, "mss-l1")


def test_full_tile_scan_and_independent_source_samples_detect_pixel_damage(tmp_path):
    verify = diagnostic("verify_full_tile")
    band = PRODUCTS["mss-l1"].bands[0]
    transform = Affine(60, 0, 600000, 0, -60, 9980000)
    values = np.arange(1, 91, dtype="uint8").reshape(9, 10)
    values[:, 0] = 0
    source_path = tmp_path / "source.tif"
    with rasterio.open(source_path, "w", driver="GTiff", width=10, height=9, count=1,
                       dtype="uint8", crs="EPSG:32717", transform=transform, nodata=0) as dst:
        dst.write(values, 1)
    group = zarr.open_group(str(tmp_path / "tiny.zarr"), mode="w", zarr_format=2)
    group.create_array("data", data=values.T[None, None], chunks=(1, 1, 4, 3), fill_value=0)
    group.create_array("valid", data=(values.T != 0)[None, None], chunks=(1, 1, 4, 3))
    group.create_array("present", data=np.asarray([[True]]))
    record = verify.scan_band(group, 0, band)
    assert (record["pixels_decoded"], record["valid_pixels"], record["chunk_positions_decoded"]) == (
        90, 81, 9
    )
    grid = type("Grid", (), {"width": 10, "height": 9, "crs": "EPSG:32717",
                             "transform": transform})()
    comparison = verify.compare_samples(
        group, 0, band, grid, {"href": str(source_path)}, None, record
    )
    assert comparison["all_compared_values_and_masks_equal"]
    assert comparison["compared_valid_pixels"] > 0
    group["data"][0, 0, 5, 4] += 1
    with pytest.raises(AssertionError, match="Independent source sample differs"):
        verify.compare_samples(group, 0, band, grid, {"href": str(source_path)}, None, record)
    group["data"][0, 0, 0, 0] = 5
    with pytest.raises(AssertionError, match="fill value"):
        verify.scan_band(group, 0, band)


def test_full_tile_scan_preserves_qa_zero_and_missing_band_fill(tmp_path):
    verify = diagnostic("verify_full_tile")
    group = zarr.open_group(str(tmp_path / "qa.zarr"), mode="w", zarr_format=2)
    group.create_array("data", shape=(1, 1, 4, 3), chunks=(1, 1, 2, 2),
                       dtype="uint16", fill_value=0)
    group.create_array("valid", data=np.ones((1, 1, 4, 3), dtype=bool), chunks=(1, 1, 2, 2))
    group.create_array("present", data=np.asarray([[True]]))
    record = verify.scan_band(group, 0, QA_BANDS["qa_pixel"])
    assert record["valid_pixels"] == record["valid_zero_pixels"] == 12
    group["valid"][:] = False
    group["present"][:] = False
    record = verify.scan_band(group, 0, QA_BANDS["qa_pixel"])
    assert record["valid_pixels"] == 0 and record["present"] is False
    group["valid"][0, 0, 0, 0] = True
    with pytest.raises(AssertionError, match="absent band"):
        verify.scan_band(group, 0, QA_BANDS["qa_pixel"])


def test_gdal_nearest_containing_pixel_epsilon_and_raster_edges():
    nearest = diagnostic("verify_full_tile").nearest_source_index
    for coordinate, expected in (
        (-1e-12, None), (0, 0), (1.99999999995, 2), (1.999999995, 1),
        (2, 2), (2.00000000005, 2), (9.5, 9), (9.99999999995, None), (10, None),
    ):
        assert nearest(coordinate, 10) == expected


def test_cross_hemisphere_utm_integer_boundaries_match_independent_gdal_and_detect_wrong_neighbor(
    tmp_path,
):
    verify = diagnostic("verify_full_tile")
    band = PRODUCTS["mss-l1"].bands[0]
    # Same physical UTM zone, north CRS stores southern coordinates as negatives.
    # All target x centres lie exactly on source pixel edges, as in the real MSS.
    source_transform = Affine(60, 0, 599910, 0, -60, -99870)
    target_transform = Affine(60, 0, 600000, 0, -60, 9900060)
    source_values = np.arange(1, 169, dtype="uint8").reshape(12, 14)
    source_values[4, 5] = 0
    expected = source_values[1:10, 2:12]
    independently_warped = np.zeros((9, 10), dtype="uint8")
    reproject(
        source_values, independently_warped,
        src_transform=source_transform, src_crs="EPSG:32617", src_nodata=0,
        dst_transform=target_transform, dst_crs="EPSG:32717", dst_nodata=0,
        resampling=Resampling.nearest,
    )
    np.testing.assert_array_equal(independently_warped, expected)
    path = tmp_path / "north_crs_southern_pixels.tif"
    with rasterio.open(path, "w", driver="GTiff", width=14, height=12, count=1,
                       dtype="uint8", crs="EPSG:32617", transform=source_transform,
                       nodata=0) as destination:
        destination.write(source_values, 1)
    group = zarr.open_group(str(tmp_path / "cross_crs.zarr"), mode="w", zarr_format=2)
    group.create_array("data", data=expected.T[None, None], chunks=(1, 1, 4, 3), fill_value=0)
    group.create_array("valid", data=(expected.T != 0)[None, None], chunks=(1, 1, 4, 3))
    group.create_array("present", data=np.asarray([[True]]))
    grid = type("Grid", (), {"width": 10, "height": 9, "crs": "EPSG:32717",
                             "transform": target_transform})()
    record = verify.scan_band(group, 0, band)
    comparison = verify.compare_samples(group, 0, band, grid, {"href": str(path)}, None, record)
    assert comparison["compared_pixels"] > 0
    assert comparison["source_pixel_boundary_samples_checked"] == comparison["compared_pixels"]
    assert comparison["source_samples_excluded"] == 0
    assert comparison["all_compared_values_and_masks_equal"]
    # Accepting either adjacent pixel would conceal a real spatial shift.
    group["data"][0, 0, 0, 0] = source_values[1, 1]
    with pytest.raises(AssertionError, match="Independent source sample differs"):
        verify.compare_samples(group, 0, band, grid, {"href": str(path)}, None, record)
