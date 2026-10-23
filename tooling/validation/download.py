"""Resume the authorized full-year pilot in independent quarterly scene stores."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from landsat_grits.access import AssetAccessError
from landsat_grits.bands import PRODUCTS
from landsat_grits.catalog import CatalogError, scene_fingerprint, scene_key
from landsat_grits.storage import atomic_json
from landsat_grits.workflow import load_manifest, manifest_hash, store_for, verify

ROOT = Path(__file__).resolve().parents[2]
RUN = "yearly-sensors-20261022"
# Frozen inputs and worker resume state must stay at one path across midnight.
EVIDENCE = ROOT / "logs/2026-10-22/yearly-sensors"
RESOLVER = ROOT.parent / "GRiTS-Resolver"
PRODUCTS_ORDER = ["mss-l1", "tm-l2", "etm-l2", "oli-l2"]


def now():
    return datetime.now(timezone.utc).isoformat()


def freeze(path, value):
    if path.exists():
        assert json.loads(path.read_text(encoding="utf-8")) == value, f"Existing frozen file differs: {path}"
    else:
        atomic_json(path, value)


def prepare():
    source_config = RESOLVER / "examples/shenzhen_landsat_monthly_sensors.json"
    document = json.loads(source_config.read_text(encoding="utf-8"))
    document["cache_dir"] = "../outputs/viewer-cache/yearly-full-49QHE"
    jobs, reused_records = [], {}
    for product, archive in zip(PRODUCTS_ORDER, document["archives"], strict=True):
        complete = load_manifest(EVIDENCE / f"{product}-all-year-candidates.json")
        assert complete["search_complete"] and complete["config"]["tiles"] == ["49QHE"]
        tile = complete["tiles"][0]
        expected = {scene_key(i): scene_fingerprint(i) for i in tile["items"]}
        reused = {}
        for relative in archive["stores"]:
            store = (source_config.parent / relative).resolve()
            commits = json.loads(store.with_name(store.name + ".commits.json").read_text(encoding="utf-8"))
            for commit in commits:
                assert commit["key"] in expected, "Unexpected source outside frozen annual inventory"
                assert commit["fingerprint"] == expected[commit["key"]], "Reused source fingerprint differs"
                assert commit["key"] not in reused
                reused[commit["key"]] = {"store": str(store), **commit}
        reused_records[product] = reused
        missing = [i for i in tile["items"] if scene_key(i) not in reused]
        for quarter in range(1, 5):
            items = [i for i in missing if (int(i["properties"]["datetime"][5:7]) - 1) // 3 + 1 == quarter]
            if not items:
                continue
            name = f"{product}-q{quarter}"
            manifest = deepcopy(complete)
            manifest["config"]["output"] = str(ROOT / "outputs/pilots" / RUN / f"q{quarter}")
            manifest["tiles"][0]["items"] = items
            manifest["tiles"][0]["scene_counts"] = {product: len(items)}
            manifest.update(selection="annual_supplement_shard", search_complete=False,
                            parent_manifest_sha256=complete["manifest_sha256"], quarter=quarter,
                            reused_scene_keys=sorted(reused))
            for field in ("estimated_uncompressed_array_bytes", "estimated_data_and_valid_chunks_upper_bound"):
                manifest[field] = complete[field] // len(tile["items"]) * len(items)
            manifest["manifest_sha256"] = manifest_hash(manifest)
            path = EVIDENCE / f"{name}-manifest.json"
            freeze(path, manifest)
            store = store_for(manifest, manifest["tiles"][0], PRODUCTS[product])
            archive["stores"].append(str(store.path.relative_to(ROOT.parent)).replace("\\", "/"))
            archive["stores"][-1] = "../../" + archive["stores"][-1]
            jobs.append({"name": name, "product": product, "quarter": quarter,
                         "manifest": str(path), "scenes": len(items), "store": str(store.path)})
        archive.update(default_period="year", default_year=complete["config"]["start"][:4])
        archive["label"] += " · 全年目录"
    assert sum(j["scenes"] for j in jobs) == 233
    assert sum(map(len, reused_records.values())) == 14
    freeze(EVIDENCE / "reuse.json", reused_records)
    freeze(EVIDENCE / "jobs.json", jobs)
    freeze(RESOLVER / "examples/shenzhen_landsat_yearly_full.json", document)
    print({"jobs": len(jobs), "new_scenes": 233, "reused_scenes": 14}, flush=True)


def worker(name):
    manifest = load_manifest(EVIDENCE / f"{name}-manifest.json")
    tile = manifest["tiles"][0]
    product = next(iter(tile["scene_counts"]))
    store = store_for(manifest, tile, PRODUCTS[product])
    status_file = EVIDENCE / f"{name}-status.json"
    for index, item in enumerate(tile["items"]):
        for attempt in range(1, 4):
            atomic_json(status_file, {"state": "running", "updated_at": now(), "job": name,
                                     "item": scene_key(item), "ordinal": index + 1,
                                     "total": len(tile["items"]), "attempt": attempt})
            try:
                written = store.append(item)
                print(f"{now()} {name} {index+1}/{len(tile['items'])} {'written' if written else 'already committed'} {scene_key(item)}", flush=True)
                break
            except (AssetAccessError, CatalogError) as error:
                print(f"{now()} {name} access retry {attempt}/3: {type(error).__name__}", flush=True)
                if attempt == 3:
                    atomic_json(status_file, {"state": "failed", "updated_at": now(), "job": name,
                                             "item": scene_key(item), "error_type": type(error).__name__})
                    raise
                time.sleep(5 * attempt)
    receipt = verify(manifest)
    assert receipt["manifest_satisfied"] and not receipt["missing_scenes"]
    atomic_json(EVIDENCE / f"{name}-receipt.json", receipt)
    atomic_json(status_file, {"state": "complete", "updated_at": now(), "job": name,
                             "scenes": len(tile["items"]), "manifest_satisfied": True})
    print(f"{now()} {name} complete", flush=True)


def run():
    jobs = json.loads((EVIDENCE / "jobs.json").read_text(encoding="utf-8"))
    # Interleave products so the eight independent writers share time fairly.
    jobs.sort(key=lambda job: (job["quarter"], PRODUCTS_ORDER.index(job["product"])))
    logs = EVIDENCE / "workers"
    logs.mkdir(exist_ok=True)

    def launch(job):
        with (logs / f"{job['name']}.log").open("a", encoding="utf-8") as log:
            result = subprocess.run([sys.executable, "-u", str(Path(__file__).resolve()), "worker", job["name"]],
                                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
        return job["name"], result.returncode

    results = {}
    # Clear previous failures before the verifier starts observing this resumed run.
    for job in jobs:
        atomic_json(EVIDENCE / f"{job['name']}-status.json", {
            "state": "queued", "updated_at": now(), "job": job["name"],
        })
    atomic_json(EVIDENCE / "download-status.json", {"state": "running", "started_at": now(), "jobs": len(jobs)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in as_completed([pool.submit(launch, job) for job in jobs]):
            name, code = future.result()
            results[name] = code
            atomic_json(EVIDENCE / "download-status.json", {"state": "running", "updated_at": now(), "finished": results, "jobs": len(jobs)})
            print(f"{now()} {name} exit={code}; {len(results)}/{len(jobs)} jobs finished", flush=True)
    complete = all(code == 0 for code in results.values())
    atomic_json(EVIDENCE / "download-status.json", {"state": "complete" if complete else "failed", "updated_at": now(), "finished": results})
    if not complete:
        raise RuntimeError("Some quarterly jobs failed; inspect their logs and resume the same frozen manifests")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "worker", "run"])
    parser.add_argument("job", nargs="?")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "worker":
        if not args.job:
            parser.error("worker requires a job name")
        worker(args.job)
    else:
        run()
