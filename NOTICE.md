# Sources and licensing

LandSat_GRiTS code and original documentation are distributed under the MIT license
in `LICENSE`. Copyright is attributed to the project contributors.

Satellite products and third-party grid files are separate inputs. The project's
MIT license does not relicense those inputs or their providers' materials.

- Landsat Collection 2 products: USGS; public catalog and COG access also provided
  by Microsoft Planetary Computer. See `docs/SOURCES.md` for product references.
- The optional `landsat-grits grid` command downloads the Sentinel-2 tiling-grid
  metadata published by Copernicus, then converts only the requested polygons.
  Source: https://sentiwiki.copernicus.eu/web/s2-products . The ZIP is not bundled
  in the software. Each generated row records the source filename, URL (when
  downloaded by the command), and SHA-256. Official grid checksum:
  `67e222547cfea235f69d1dd35317babb0296c8c61556c198fc9f1e2dec7bcf29`.
- S1-GRiTS informed the fixed-grid organization. Its software and grid file are
  not included in this distribution. Historical local validation used a separate
  S1-GRiTS grid; those records do not make it a runtime dependency.
- GRiTS-Resolver is a separate, optional consumer of the scene archives. It has
  its own distribution and licensing; it is not bundled or relicensed here.

The offline demo generates artificial arrays and a small artificial spatial
window. They are not observations or an authoritative MGRS tile boundary.
