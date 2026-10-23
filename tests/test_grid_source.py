import hashlib
import io
import zipfile

import geopandas as gpd
import pytest

from landsat_grits.cli import main
from landsat_grits.grid import load_grid
from landsat_grits import grid_source


def kml(name="31NEA", coordinates="3,0 3.01,0 3.01,0.01 3,0.01 3,0"):
    return f'''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
    <name>{name}</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>{coordinates}</coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark></Document></kml>'''.encode()


@pytest.mark.parametrize("zipped", [False, True])
def test_local_grid_provenance_and_no_overwrite(tmp_path, zipped):
    source = tmp_path / ("grid.zip" if zipped else "grid.kml")
    if zipped:
        with zipfile.ZipFile(source, "w") as z:
            z.writestr("nested/grid.kml", kml())
    else:
        source.write_bytes(kml())
    output = tmp_path / "mgrs.parquet"
    result = grid_source.prepare_grid(output, ["31nea"], source=source)
    table = gpd.read_parquet(output)
    assert table.iloc[0].source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["tiles"] == ["31NEA"]
    assert load_grid(output, "31NEA", 30).crs == "EPSG:32631"
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        grid_source.prepare_grid(output, ["31NEA"], source=source)
    assert output.read_bytes() == before


@pytest.mark.parametrize("payload,match", [
    (kml("31NEB"), "absent"),
    (kml().replace(b"</Document>", kml().split(b"<Document>")[1].split(b"</Document>")[0]
                  + b"</Document>"), "Duplicate"),
    (b'<!DOCTYPE kml [<!ENTITY test "x">]>' + kml(), "DTD"),
    (kml(coordinates="179,0 -179,0 -179,1 179,1 179,0"), "antimeridian"),
    (kml(coordinates="3,0 3.01,0.01 3.01,0 3,0.01 3,0"), "invalid"),
])
def test_bad_kml_leaves_no_grid(tmp_path, payload, match):
    source = tmp_path / "grid.kml"
    source.write_bytes(payload)
    with pytest.raises(ValueError, match=match):
        grid_source.prepare_grid(tmp_path / "out.parquet", ["31NEA"], source=source)
    assert not (tmp_path / "out.parquet").exists()


def test_ambiguous_zip_rejected():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("a.kml", kml())
        z.writestr("b.kml", kml())
    with pytest.raises(ValueError, match="one KML"):
        grid_source._polygons(raw.getvalue(), {"31NEA"})


def test_official_precise_wkt_is_used_instead_of_rounded_display_vertices():
    precise = "MULTIPOLYGON(((3.0000000001 0,3.01 0,3.01 0.01,3.0000000001 0.01,3.0000000001 0)))"
    description = f'<description><![CDATA[<b>LL_WKT</b></font></td><td><font>{precise}</font>]]></description>'
    raw = kml().replace(b"<Polygon>", description.encode() + b"<Polygon>")
    assert grid_source._polygons(raw, {"31NEA"})["31NEA"].bounds[0] == 3.0000000001


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
def test_alternate_encoding_cannot_bypass_entity_rejection(encoding):
    document = '<!DOCTYPE kml [<!ENTITY injected "31NEA">]>' + kml().decode()
    document = document.replace("<name>31NEA</name>", "<name>&injected;</name>")
    with pytest.raises(ValueError, match="UTF-8"):
        grid_source._polygons(document.encode(encoding), {"31NEA"})


def test_remote_grid_checksum_and_cli(monkeypatch, tmp_path, capsys):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield kml()

    monkeypatch.setattr(grid_source.requests, "get", lambda *a, **kw: Response())
    output = tmp_path / "grid.parquet"
    assert main(["grid", "--tiles", "31NEA", "--output", str(output)]) == 1
    assert "checksum" in capsys.readouterr().err
    assert not output.exists()
    monkeypatch.setattr(grid_source, "GRID_SHA256", hashlib.sha256(kml()).hexdigest())
    assert main(["grid", "--tiles", "31NEA", "--output", str(output)]) == 0
    assert gpd.read_parquet(output).iloc[0].source_url == grid_source.GRID_URL
