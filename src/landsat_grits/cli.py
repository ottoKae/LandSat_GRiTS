from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

from filelock import FileLock

from .bands import PRODUCTS
from .storage import atomic_json
from .workflow import ingest, load_config, load_manifest, plan, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description="Landsat scenes → fixed MGRS grids → Zarr")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bands", help="Show supported products, assets and source-DN scales")
    grid = commands.add_parser("grid", help="Prepare selected MGRS extents from official KML")
    grid.add_argument("--tiles", nargs="+", required=True, help="MGRS IDs, e.g. 49QHE 50RKV")
    grid.add_argument("--output", required=True, help="New GeoParquet file; never overwrites")
    grid.add_argument("--source", help="Local KML/ZIP; omit to fetch the pinned official grid")
    planner = commands.add_parser("plan", help="Search all pages; freeze sources without reading pixels")
    planner.add_argument("config")
    planner.add_argument("--manifest", required=True)
    planner.add_argument("--limit-per-search", type=int,
                         help="Pilot only: stop after this many matching scenes per tile/collection")
    runner = commands.add_parser("run", help="Download/regrid the frozen selection; resume safely")
    runner.add_argument("manifest")
    auditor = commands.add_parser("verify", help="Compare committed stores with the frozen selection")
    auditor.add_argument("manifest")
    viewer = commands.add_parser("gui", help="Browse local grids, sensors and all bands in a web UI")
    viewer.add_argument("root", help="Archive directory containing the downloaded Zarr stores")
    viewer.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    args = parser.parse_args(argv)
    try:
        if args.command == "grid":
            from .grid_source import prepare_grid

            result = prepare_grid(args.output, args.tiles, source=args.source)
        elif args.command == "gui":
            from .viewer import serve

            try:
                serve(Path(args.root), port=args.port)
            except KeyboardInterrupt:
                pass
            return 0
        elif args.command == "bands":
            result = {key: asdict(value) for key, value in PRODUCTS.items()}
        elif args.command == "plan":
            destination = Path(args.manifest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Hold the destination through discovery so concurrent planners cannot overwrite it.
            with FileLock(str(destination) + ".lock", timeout=0):
                if destination.exists():
                    raise ValueError("Manifest already exists; choose a new filename to preserve provenance")
                result = plan(load_config(args.config), limit_per_search=args.limit_per_search)
                atomic_json(destination, result)
            result = {k: v for k, v in result.items() if k not in ("config", "tiles")}
            result["manifest"] = str(destination.resolve())
        else:
            manifest = load_manifest(args.manifest)
            result = ingest(manifest) if args.command == "run" else verify(manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("manifest_satisfied", True) else 2
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
