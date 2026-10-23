# Changelog

## 0.2.0b1 — 2026-10-23

First public beta release.

- Collection 2 MSS, TM, ETM+, OLI/TIRS scene archives, separated by sensor family
  and product level; fixed MGRS extent and `(time,bands,x,y)` Zarr groups.
- Public MSS L1 / modern L2 COG access; modern L1 local import and explicit AWS
  Requester Pays access. No automatic EarthExplorer login/download.
- Commit-ledger recovery, provenance, track/scene selection and local single-scene GUI.
- MIT license, standalone grid preparation, offline demo, portable examples,
  explicit optional Resolver integration, build checks and CI.

Known limits: single-machine local storage; no historical insertion; no verified
global completeness or universal cloud-free output. Modern L1 paid access has
not been accepted against real paid downloads. Daily/monthly/yearly composition
belongs to the separately installed GRiTS-Resolver.
