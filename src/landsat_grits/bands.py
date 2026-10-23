"""Landsat Collection 2 band semantics for Planetary Computer and USGS.

The archive keeps sensor families and processing levels separate.  In particular,
MSS digital numbers are not surface reflectance, and its two near-infrared bands
are not interchangeable with the TM/ETM+/OLI near-infrared band.

Scale and offset describe conversion *after* masking nodata; ingestion preserves
the integer source values.  MSS calibration is scene- and band-dependent and is
kept in the original MTL metadata, not represented by the identity factors below.

Sources, checked 2026-10-20:
https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l1
https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2
https://landsatlook.usgs.gov/stac-server/collections/landsat-c2l1
https://www.usgs.gov/faqs/what-are-band-designations-landsat-satellites
https://www.usgs.gov/landsat-missions/using-usgs-landsat-level-1-data-product
https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands
https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


BandKind = Literal[
    "dn", "thermal_dn", "panchromatic_dn",
    "surface_reflectance", "surface_temperature", "ancillary", "qa",
]


@dataclass(frozen=True)
class BandSpec:
    name: str
    asset_key: str
    kind: BandKind
    dtype: str
    nodata: int | float | None
    scale: float
    offset: float
    unit: str
    source_band: str


@dataclass(frozen=True)
class ProductSpec:
    key: str
    collection: str
    sensor: str
    platforms: tuple[str, ...]
    resolution: float
    bands: tuple[BandSpec, ...]
    qa_assets: tuple[str, ...]
    source: str = "planetary-computer"

    @property
    def optical_bands(self) -> tuple[BandSpec, ...]:
        return tuple(
            band
            for band in self.bands
            if band.kind in ("dn", "surface_reflectance")
        )

    @property
    def thermal_bands(self) -> tuple[BandSpec, ...]:
        return tuple(
            band for band in self.bands
            if band.kind in ("surface_temperature", "thermal_dn")
        )

    @property
    def panchromatic_bands(self) -> tuple[BandSpec, ...]:
        return tuple(band for band in self.bands if band.kind == "panchromatic_dn")

    @property
    def ancillary_bands(self) -> tuple[BandSpec, ...]:
        return tuple(band for band in self.bands if band.kind == "ancillary")


def _reflectance(name: str, number: int) -> BandSpec:
    return BandSpec(
        name, name, "surface_reflectance", "uint16", 0,
        0.0000275, -0.2, "1", f"SR_B{number}",
    )


def _temperature(asset: str, number: int) -> BandSpec:
    return BandSpec(
        "surface_temperature", asset, "surface_temperature", "uint16", 0,
        0.00341802, 149.0, "K", f"ST_B{number}",
    )


def _ancillary(
    name: str, asset: str, scale: float, unit: str, source_band: str
) -> BandSpec:
    return BandSpec(
        name, asset, "ancillary", "int16", -9999,
        scale, 0.0, unit, source_band,
    )


# ST_QA is continuous temperature uncertainty, not a packed QA bit field.
_ST_ANCILLARY = (
    _ancillary("temperature_uncertainty", "qa", 0.01, "K", "ST_QA"),
    _ancillary(
        "thermal_radiance", "trad", 0.001, "W m-2 sr-1 um-1", "ST_TRAD"
    ),
    _ancillary(
        "upwelling_radiance", "urad", 0.001, "W m-2 sr-1 um-1", "ST_URAD"
    ),
    _ancillary(
        "downwelling_radiance", "drad", 0.001, "W m-2 sr-1 um-1", "ST_DRAD"
    ),
    _ancillary("atmospheric_transmittance", "atran", 0.0001, "1", "ST_ATRAN"),
    _ancillary("emissivity", "emis", 0.0001, "1", "ST_EMIS"),
    _ancillary("emissivity_stddev", "emsd", 0.0001, "1", "ST_EMSD"),
    _ancillary("cloud_distance", "cdist", 0.01, "km", "ST_CDIST"),
)
_ATMOS_OPACITY = _ancillary(
    "atmospheric_opacity", "atmos_opacity", 0.001, "1", "SR_ATMOS_OPACITY"
)

_TM_OPTICAL = tuple(
    _reflectance(name, number)
    for name, number in (
        ("blue", 1), ("green", 2), ("red", 3),
        ("nir08", 4), ("swir16", 5), ("swir22", 7),
    )
)
_OLI_OPTICAL = tuple(
    _reflectance(name, number)
    for name, number in (
        ("coastal", 1), ("blue", 2), ("green", 3), ("red", 4),
        ("nir08", 5), ("swir16", 6), ("swir22", 7),
    )
)


def _dn(
    asset: str, source_band: str, dtype: str, kind: BandKind = "dn"
) -> BandSpec:
    """Preserve L1 DN; physical calibration remains in scene MTL metadata."""
    return BandSpec(asset, asset, kind, dtype, 0, 1.0, 0.0, "DN", source_band)


_TM_L1_OPTICAL = tuple(
    _dn(band.asset_key, band.source_band.removeprefix("SR_"), "uint8")
    for band in _TM_OPTICAL
)
_OLI_L1_OPTICAL = (
    *(
        _dn(band.asset_key, band.source_band.removeprefix("SR_"), "uint16")
        for band in _OLI_OPTICAL
    ),
    _dn("cirrus", "B9", "uint16"),
)

QA_BANDS: dict[str, BandSpec] = {
    "qa_pixel": BandSpec(
        "qa_pixel", "qa_pixel", "qa", "uint16", 1,
        1.0, 0.0, "bit field", "QA_PIXEL",
    ),
    "qa_radsat": BandSpec(
        "qa_radsat", "qa_radsat", "qa", "uint16", None,
        1.0, 0.0, "bit field", "QA_RADSAT",
    ),
    "cloud_qa": BandSpec(
        "cloud_qa", "cloud_qa", "qa", "uint8", None,
        1.0, 0.0, "bit field", "SR_CLOUD_QA",
    ),
    "qa_aerosol": BandSpec(
        "qa_aerosol", "qa_aerosol", "qa", "uint8", 1,
        1.0, 0.0, "bit field", "SR_QA_AEROSOL",
    ),
}

PRODUCTS: dict[str, ProductSpec] = {
    "mss-l1": ProductSpec(
        key="mss-l1",
        collection="landsat-c2-l1",
        sensor="mss",
        platforms=tuple(f"landsat-{number}" for number in range(1, 6)),
        resolution=60.0,
        bands=tuple(
            BandSpec(
                name, name, "dn", "uint8", 0, 1.0, 0.0, "DN", f"B{number}"
            )
            for number, name in enumerate(
                ("green", "red", "nir08", "nir09"), start=1
            )
        ),
        qa_assets=("qa_pixel", "qa_radsat"),
    ),
    "tm-l2": ProductSpec(
        key="tm-l2",
        collection="landsat-c2-l2",
        sensor="tm",
        platforms=("landsat-4", "landsat-5"),
        resolution=30.0,
        bands=(
            *_TM_OPTICAL, _temperature("lwir", 6),
            *_ST_ANCILLARY, _ATMOS_OPACITY,
        ),
        qa_assets=("qa_pixel", "qa_radsat", "cloud_qa"),
    ),
    "etm-l2": ProductSpec(
        key="etm-l2",
        collection="landsat-c2-l2",
        sensor="etm+",
        platforms=("landsat-7",),
        resolution=30.0,
        bands=(
            *_TM_OPTICAL, _temperature("lwir", 6),
            *_ST_ANCILLARY, _ATMOS_OPACITY,
        ),
        qa_assets=("qa_pixel", "qa_radsat", "cloud_qa"),
    ),
    "oli-l2": ProductSpec(
        key="oli-l2",
        collection="landsat-c2-l2",
        sensor="oli",
        platforms=("landsat-8", "landsat-9"),
        resolution=30.0,
        bands=(*_OLI_OPTICAL, _temperature("lwir11", 10), *_ST_ANCILLARY),
        qa_assets=("qa_pixel", "qa_radsat", "qa_aerosol"),
    ),
    "tm-l1": ProductSpec(
        key="tm-l1",
        collection="landsat-c2l1",
        sensor="tm",
        platforms=("landsat-4", "landsat-5"),
        resolution=30.0,
        bands=(*_TM_L1_OPTICAL, _dn("lwir", "B6", "uint8", "thermal_dn")),
        qa_assets=("qa_pixel", "qa_radsat"),
        source="usgs",
    ),
    "etm-l1": ProductSpec(
        key="etm-l1",
        collection="landsat-c2l1",
        sensor="etm+",
        platforms=("landsat-7",),
        resolution=30.0,
        bands=(
            *_TM_L1_OPTICAL,
            _dn("lwir", "B6_VCID_1", "uint8", "thermal_dn"),
            _dn("lwir_high", "B6_VCID_2", "uint8", "thermal_dn"),
            _dn("pan", "B8", "uint8", "panchromatic_dn"),
        ),
        qa_assets=("qa_pixel", "qa_radsat"),
        source="usgs",
    ),
    "oli-l1": ProductSpec(
        key="oli-l1",
        collection="landsat-c2l1",
        sensor="oli",
        platforms=("landsat-8", "landsat-9"),
        resolution=30.0,
        bands=(
            *_OLI_L1_OPTICAL,
            _dn("lwir11", "B10", "uint16", "thermal_dn"),
            _dn("lwir12", "B11", "uint16", "thermal_dn"),
            _dn("pan", "B8", "uint16", "panchromatic_dn"),
        ),
        qa_assets=("qa_pixel", "qa_radsat"),
        source="usgs",
    ),
}


def group_resolutions(product: ProductSpec) -> dict[str, float]:
    """Return delivered pixel spacing for each populated array group.

    L1 thermal products are delivered at 30 m: native detector sampling is
    120 m (TM), 60 m (ETM+), or 100 m (TIRS).  The delivered panchromatic band
    retains its 15 m sampling.  MSS products are delivered at 60 m.
    """
    groups: dict[str, float] = {}
    if product.optical_bands:
        groups["optical"] = product.resolution
    if product.thermal_bands:
        groups["thermal"] = product.resolution
    if product.panchromatic_bands:
        groups["pan"] = 15.0
    if product.ancillary_bands:
        groups["ancillary"] = product.resolution
    if product.qa_assets:
        groups["qa"] = product.resolution
    return groups


def _platform(item: dict) -> str:
    properties = item.get("properties") or {}
    platform = properties.get("platform")
    if not isinstance(platform, str) or not platform:
        raise ValueError("STAC item must provide properties.platform.")
    return platform.lower().replace("_", "-")


def _usgs_instruments(item: dict, platform: str) -> frozenset[str] | None:
    """Resolve exact instruments, cross-checking metadata against a valid C2 ID.

    OLI-only and TIRS-only are intentionally distinct from combined acquisitions.
    When instruments are absent, only a complete Collection 2 product identifier
    can establish which measurements were acquired.
    """
    properties = item.get("properties") or {}
    instruments = properties.get("instruments") or []
    if isinstance(instruments, str):
        instruments = [instruments]
    if not isinstance(instruments, (list, tuple)):
        return None
    resolved = set()
    aliases = {
        "mss": ("mss",), "tm": ("tm",), "etm": ("etm+",), "etm+": ("etm+",),
        "oli": ("oli",), "tirs": ("tirs",),
        "oli-2": ("oli",), "tirs-2": ("tirs",),
        "oli_tirs": ("oli", "tirs"), "oli-tirs": ("oli", "tirs"),
    }
    for instrument in instruments:
        canonical = aliases.get(str(instrument).lower())
        if canonical is None:
            return None
        resolved.update(canonical)
    allowed = (
        {"mss"}, {"tm"}, {"etm+"}, {"oli"}, {"tirs"}, {"oli", "tirs"}
    )
    if resolved and resolved not in allowed:
        return None

    # Product IDs are used only when their format and spacecraft are explicit.
    product_id = item.get("id", "")
    match = re.fullmatch(
        r"L([MTECO])0([1-9])_L1(?:TP|GT|GS)_\d{6}_\d{8}_\d{8}_02_(?:T1|T2|RT)",
        product_id if isinstance(product_id, str) else "",
    )
    if match:
        instrument_code, spacecraft = match.groups()
        if platform != f"landsat-{spacecraft}":
            return None
        id_instruments = {
            "M": {"mss"}, "E": {"etm+"}, "C": {"oli", "tirs"}, "O": {"oli"},
            "T": {"tirs"} if spacecraft in ("8", "9") else {"tm"},
        }[instrument_code]
        if resolved and resolved != id_instruments:
            return None
        return frozenset(id_instruments)
    return frozenset(resolved) if resolved else None


def _usgs_sensor(item: dict, platform: str) -> str | None:
    instruments = _usgs_instruments(item, platform)
    if instruments and instruments <= {"oli", "tirs"}:
        return "oli"
    return next(iter(instruments), None) if instruments else None


def product_for_item(item: dict) -> ProductSpec:
    """Resolve the sensor/product using collection and platform, not band number.

    An L2SR item remains a valid Level-2 item when temperature assets are absent.
    Actual asset presence is checked by ingestion, not by product classification.
    The shared USGS L1 collection also requires sensor identity so Landsat 4-5
    MSS cannot be mistaken for TM.  USGS MSS is deliberately not mapped to the
    Planetary Computer MSS product.
    """
    platform = _platform(item)
    if platform == "landsat-6":
        raise ValueError("Landsat 6 did not achieve orbit and has no imagery.")
    collection = item.get("collection")
    usgs_sensor = _usgs_sensor(item, platform) if collection == "landsat-c2l1" else None
    for product in PRODUCTS.values():
        if collection == product.collection and platform in product.platforms:
            if product.source == "usgs" and product.sensor != usgs_sensor:
                continue
            return product
    raise ValueError(
        f"Unsupported Landsat collection/platform: {collection!r}/{platform!r}."
    )


def required_bands_for_item(item: dict) -> tuple[BandSpec, ...]:
    """Return measurements acquired by this scene that ingestion must retain.

    USGS TM/ETM+ require all native bands, including both ETM+ thermal gains.
    Landsat 8-9 combined scenes require eleven bands; OLI-only scenes require
    eight optical bands and pan, while TIRS-only scenes require B10 and B11.
    Missing acquired assets are errors. Unacquired measurements may instead be
    filled with nodata and recorded as absent in the fixed family schema.

    Planetary Computer MSS/L2 retain the existing optical requirement; L2
    temperature and ancillary products remain optional.
    """
    product = product_for_item(item)
    if product.source != "usgs":
        return product.optical_bands
    if product.key != "oli-l1":
        return product.bands
    instruments = _usgs_instruments(item, _platform(item))
    if instruments == {"oli"}:
        return tuple(
            band for band in product.bands
            if band.kind in ("dn", "panchromatic_dn")
        )
    if instruments == {"tirs"}:
        return product.thermal_bands
    return product.bands


def source_band_for_item(band: BandSpec, item: dict) -> str:
    """Return the native file band identifier, accounting for MSS renumbering.

    MSS green/red/NIR1/NIR2 use B4/B5/B6/B7 on Landsat 1-3 and B1/B2/B3/B4
    on Landsat 4-5.  ``BandSpec.source_band`` records the latter convention.
    Other products use their original Collection 2 SR/ST/QA identifiers.
    """
    product = product_for_item(item)
    available = (*product.bands, *(QA_BANDS[key] for key in product.qa_assets))
    if band not in available:
        raise ValueError(
            f"Band {band.name!r} does not belong to product {product.key!r}."
        )
    if product.key == "mss-l1" and band.kind == "dn":
        number = int(band.source_band[1:])
        if _platform(item) in ("landsat-1", "landsat-2", "landsat-3"):
            number += 3
        return f"B{number}"
    return band.source_band
