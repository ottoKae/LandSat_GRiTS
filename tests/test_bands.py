"""Scientific schema checks that prevent silent cross-sensor mislabelling."""

import pytest

from landsat_grits.bands import PRODUCTS, QA_BANDS, product_for_item, source_band_for_item


def item(collection, platform, **properties):
    return {
        "collection": collection,
        "properties": {"platform": platform, **properties},
        "assets": {},
    }


@pytest.mark.parametrize("platform", ["landsat-4", "landsat-5"])
def test_landsat_4_and_5_distinguish_mss_from_tm(platform):
    mss = product_for_item(item("landsat-c2-l1", platform))
    tm = product_for_item(item("landsat-c2-l2", platform))
    assert mss.key == "mss-l1"
    assert tm.key == "tm-l2"
    assert all(band.kind == "dn" for band in mss.optical_bands)
    assert all(band.kind == "surface_reflectance" for band in tm.optical_bands)
    assert {band.name for band in mss.optical_bands} == {
        "green", "red", "nir08", "nir09"
    }
    assert "nir09" not in {band.name for band in tm.optical_bands}


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("landsat-1", ("B4", "B5", "B6", "B7")),
        ("landsat-3", ("B4", "B5", "B6", "B7")),
        ("LANDSAT_5", ("B1", "B2", "B3", "B4")),
    ],
)
def test_mss_original_band_numbers_survive_sensor_normalization(platform, expected):
    scene = item("landsat-c2-l1", platform)
    assert tuple(
        source_band_for_item(band, scene)
        for band in product_for_item(scene).optical_bands
    ) == expected


@pytest.mark.parametrize(
    "collection,platform",
    [
        ("landsat-c2-l2", "landsat-1"),
        ("landsat-c2-l1", "landsat-9"),
        ("landsat-c2l1", "landsat-9"),
        ("unrelated-collection", "landsat-5"),
        (None, "landsat-5"),
    ],
)
def test_unsupported_processing_levels_are_not_relabelled(collection, platform):
    with pytest.raises(ValueError, match="Unsupported"):
        product_for_item(item(collection, platform))


def test_landsat_6_and_missing_platform_report_clear_failures():
    with pytest.raises(ValueError, match="no imagery"):
        product_for_item(item("landsat-c2-l2", "landsat-6"))
    with pytest.raises(ValueError, match="properties.platform"):
        product_for_item({"collection": "landsat-c2-l2"})


def test_l2sr_without_temperature_is_still_a_valid_scene():
    scene = item("landsat-c2-l2", "landsat-9", **{"landsat:correction": "L2SR"})
    scene["assets"] = {
        band.asset_key: {} for band in PRODUCTS["oli-l2"].optical_bands
    }
    product = product_for_item(scene)
    assert product.key == "oli-l2"
    assert product.thermal_bands[0].asset_key == "lwir11"
    assert "lwir11" not in scene["assets"]


def test_landsat_7_late_mission_is_not_rejected_by_stale_date_limits():
    scene = item(
        "landsat-c2-l2", "landsat-7", datetime="2024-01-19T01:39:58Z"
    )
    assert product_for_item(scene).key == "etm-l2"


def test_reflectance_temperature_and_qa_have_distinct_semantics():
    product = PRODUCTS["oli-l2"]
    red = next(band for band in product.optical_bands if band.name == "red")
    temperature = product.thermal_bands[0]
    uncertainty = next(
        band for band in product.ancillary_bands if band.asset_key == "qa"
    )
    assert 10000 * red.scale + red.offset == pytest.approx(0.075)
    assert 45000 * temperature.scale + temperature.offset == pytest.approx(302.8109)
    assert uncertainty.kind == "ancillary"
    assert uncertainty.unit == "K"
    assert 150 * uncertainty.scale == pytest.approx(1.5)
    assert "qa" not in product.qa_assets
    assert QA_BANDS["qa_radsat"].nodata is None  # Zero means unsaturated.


def test_ancillary_signed_fill_and_sensor_specific_qa_are_preserved():
    for product in PRODUCTS.values():
        assert all(
            band.dtype == "int16" and band.nodata == -9999
            for band in product.ancillary_bands
        )
        assert not set(product.qa_assets).intersection(
            band.asset_key for band in product.bands
        )
    assert "cloud_qa" in PRODUCTS["tm-l2"].qa_assets
    assert "cloud_qa" not in PRODUCTS["oli-l2"].qa_assets
    assert "qa_aerosol" in PRODUCTS["oli-l2"].qa_assets
    assert "qa_aerosol" not in PRODUCTS["mss-l1"].qa_assets


def test_native_source_band_does_not_confuse_common_red_name():
    mss_red = next(
        band for band in PRODUCTS["mss-l1"].optical_bands if band.name == "red"
    )
    oli_red = next(
        band for band in PRODUCTS["oli-l2"].optical_bands if band.name == "red"
    )
    scene = item("landsat-c2-l2", "landsat-8")
    assert source_band_for_item(oli_red, scene) == "SR_B4"
    with pytest.raises(ValueError, match="does not belong"):
        source_band_for_item(mss_red, scene)
