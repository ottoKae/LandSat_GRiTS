"""Prevent L1 band loss, gain merging, and 15 m panchromatic downsampling."""

import pytest

from landsat_grits.bands import (
    PRODUCTS,
    group_resolutions,
    product_for_item,
    required_bands_for_item,
    source_band_for_item,
)


def usgs_item(platform, instruments=None, product_id=None):
    properties = {"platform": platform}
    if instruments is not None:
        properties["instruments"] = instruments
    result = {"collection": "landsat-c2l1", "properties": properties}
    if product_id is not None:
        result["id"] = product_id
    return result


@pytest.mark.parametrize(
    "platform,instruments,expected",
    [
        ("LANDSAT_4", ["TM"], "tm-l1"),
        ("LANDSAT_5", ["TM"], "tm-l1"),
        ("LANDSAT_7", ["ETM"], "etm-l1"),
        ("landsat-7", ["etm+"], "etm-l1"),
        ("LANDSAT_8", ["OLI", "TIRS"], "oli-l1"),
        ("LANDSAT_9", ["OLI", "TIRS"], "oli-l1"),
    ],
)
def test_usgs_native_platform_and_instrument_names(platform, instruments, expected):
    product = product_for_item(usgs_item(platform, instruments))
    assert product.key == expected
    assert product.source == "usgs"


@pytest.mark.parametrize("platform", ["LANDSAT_4", "LANDSAT_5"])
def test_usgs_mss_cannot_be_mistaken_for_tm(platform):
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(usgs_item(platform, ["MSS"]))
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(usgs_item(platform))
    pc_scene = {"collection": "landsat-c2-l1", "properties": {"platform": platform}}
    assert product_for_item(pc_scene).key == "mss-l1"


def test_product_id_fallback_is_explicit_and_checks_conflicting_metadata():
    tm_id = "LT05_L1TP_045033_20001231_20200906_02_T1"
    assert product_for_item(usgs_item("LANDSAT_5", product_id=tm_id)).key == "tm-l1"
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(usgs_item("LANDSAT_4", product_id=tm_id))
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(usgs_item("LANDSAT_5", ["MSS"], tm_id))
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(
            usgs_item(
                "LANDSAT_5", product_id="LM05_L1GS_039039_20130107_20200820_02_T2"
            )
        )


def test_etm_retains_both_thermal_gain_assets_and_original_file_identifiers():
    scene = usgs_item(
        "LANDSAT_7", ["ETM"], "LE07_L1TP_135047_20240119_20240213_02_T1"
    )
    product = product_for_item(scene)
    assert {
        band.asset_key: source_band_for_item(band, scene)
        for band in product.thermal_bands
    } == {"lwir": "B6_VCID_1", "lwir_high": "B6_VCID_2"}
    assert len(product.optical_bands) == 6
    assert len(product.bands) == 9  # B6 has two retained gain measurements.
    assert len({band.source_band for band in product.bands}) == 9


def test_oli_retains_cirrus_pan_and_both_tirs_bands():
    product = PRODUCTS["oli-l1"]
    assert {band.source_band for band in product.bands} == {
        f"B{number}" for number in range(1, 12)
    }
    assert {band.asset_key for band in product.optical_bands} == {
        "coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "cirrus"
    }
    assert {band.asset_key for band in product.thermal_bands} == {"lwir11", "lwir12"}
    assert [band.asset_key for band in product.panchromatic_bands] == ["pan"]
    assert all(band.kind == "thermal_dn" for band in product.thermal_bands)


@pytest.mark.parametrize(
    "product_key,expected_dtype", [("tm-l1", "uint8"), ("etm-l1", "uint8"), ("oli-l1", "uint16")]
)
def test_l1_pixels_preserve_dn_instead_of_applying_l2_calibration(product_key, expected_dtype):
    product = PRODUCTS[product_key]
    for band in product.bands:
        assert band.dtype == expected_dtype
        assert band.scale == 1.0
        assert band.offset == 0.0
        assert band.unit == "DN"
        assert band.nodata == 0
        assert not band.source_band.startswith(("SR_", "ST_"))
    assert product.qa_assets == ("qa_pixel", "qa_radsat")
    assert not product.ancillary_bands


@pytest.mark.parametrize("product_key", ["etm-l1", "oli-l1"])
def test_pan_keeps_fifteen_meter_spacing_separate_from_delivered_thermal(product_key):
    assert group_resolutions(PRODUCTS[product_key]) == {
        "optical": 30.0, "thermal": 30.0, "pan": 15.0, "qa": 30.0
    }


def test_mss_and_tm_grid_groups_do_not_invent_pan_or_native_detector_grid():
    assert group_resolutions(PRODUCTS["mss-l1"]) == {"optical": 60.0, "qa": 60.0}
    assert group_resolutions(PRODUCTS["tm-l1"]) == {
        "optical": 30.0, "thermal": 30.0, "qa": 30.0
    }


def test_existing_l2_source_calibration_and_assets_remain_distinct():
    l2 = PRODUCTS["oli-l2"]
    assert l2.source == "planetary-computer"
    assert l2.collection == "landsat-c2-l2"
    assert not l2.panchromatic_bands
    assert l2.thermal_bands[0].source_band == "ST_B10"
    assert l2.thermal_bands[0].scale == 0.00341802
    assert all(band.scale == 0.0000275 for band in l2.optical_bands)
    assert "cirrus" not in {band.asset_key for band in l2.bands}


@pytest.mark.parametrize(
    "platform,instruments,product_id,expected",
    [
        (
            "LANDSAT_8", ["OLI"], "LO08_L1GT_041038_20230423_20230425_02_T2",
            {"coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "cirrus", "pan"},
        ),
        (
            "LANDSAT_9", ["OLI"], "LO09_L1GT_055118_20230228_20230307_02_T2",
            {"coastal", "blue", "green", "red", "nir08", "swir16", "swir22", "cirrus", "pan"},
        ),
        (
            "LANDSAT_8", ["TIRS"], "LT08_L1GT_137208_20261009_20261015_02_T2",
            {"lwir11", "lwir12"},
        ),
        (
            "LANDSAT_9", ["TIRS"], "LT09_L1GT_137208_20261017_20261017_02_T2",
            {"lwir11", "lwir12"},
        ),
    ],
)
def test_real_sensor_only_product_modes_require_only_acquired_bands(
    platform, instruments, product_id, expected
):
    # IDs and instrument declarations are from actual USGS STAC metadata.
    scene = usgs_item(platform, instruments, product_id)
    assert product_for_item(scene).key == "oli-l1"
    assert {band.asset_key for band in required_bands_for_item(scene)} == expected
    # A fixed family schema still records the unacquired bands as absent.
    assert len(product_for_item(scene).bands) == 11
    del scene["properties"]["instruments"]
    assert {band.asset_key for band in required_bands_for_item(scene)} == expected


@pytest.mark.parametrize(
    "instruments,expected_count",
    [(["OLI"], 9), (["TIRS"], 2), (["OLI", "TIRS"], 11), (["OLI_TIRS"], 11)],
)
def test_explicit_instruments_can_resolve_requirements_without_a_product_id(
    instruments, expected_count
):
    assert len(required_bands_for_item(usgs_item("LANDSAT_9", instruments))) == expected_count


@pytest.mark.parametrize(
    "product_id,instruments",
    [
        ("LC09_L1GT_030202_20261019_20261019_02_T2", ["OLI"]),
        ("LC09_L1GT_030202_20261019_20261019_02_T2", ["TIRS"]),
        ("LO09_L1GT_055118_20230228_20230307_02_T2", ["OLI", "TIRS"]),
        ("LO09_L1GT_055118_20230228_20230307_02_T2", ["TIRS"]),
        ("LT09_L1GT_137208_20261017_20261017_02_T2", ["OLI", "TIRS"]),
        ("LT09_L1GT_137208_20261017_20261017_02_T2", ["OLI"]),
    ],
)
def test_sensor_mode_contradictions_do_not_silently_relax_required_bands(
    product_id, instruments
):
    scene = usgs_item("LANDSAT_9", instruments, product_id)
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(scene)
    with pytest.raises(ValueError, match="Unsupported"):
        required_bands_for_item(scene)


@pytest.mark.parametrize(
    "product_id",
    [
        None,
        "LT09",
        "LT09_L1GT_137208",
        "LT09_L1GT_137208_20261017_20261017_01_T2",
    ],
)
def test_missing_instruments_cannot_use_partial_or_wrong_collection_ids(product_id):
    with pytest.raises(ValueError, match="Unsupported"):
        required_bands_for_item(usgs_item("LANDSAT_9", product_id=product_id))


def test_combined_scene_requires_both_sensors_and_single_sensor_products_do_not():
    combined = usgs_item(
        "LANDSAT_9", ["OLI", "TIRS"], "LC09_L1GT_030202_20261019_20261019_02_T2"
    )
    assert required_bands_for_item(combined) == PRODUCTS["oli-l1"].bands
    for platform, instruments, key in (
        ("LANDSAT_5", ["TM"], "tm-l1"),
        ("LANDSAT_7", ["ETM"], "etm-l1"),
    ):
        assert required_bands_for_item(usgs_item(platform, instruments)) == PRODUCTS[key].bands


@pytest.mark.parametrize("key", ["mss-l1", "tm-l2", "etm-l2", "oli-l2"])
def test_mss_and_l2_keep_optical_only_required_and_thermal_optional(key):
    product = PRODUCTS[key]
    scene = {
        "collection": product.collection,
        "properties": {"platform": product.platforms[0]},
    }
    assert required_bands_for_item(scene) == product.optical_bands
