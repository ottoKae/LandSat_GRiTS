"""The annual pilot verifier must not mistake old failures for a resumed failure."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def test_resume_clears_all_old_failures_before_launch(tmp_path, monkeypatch):
    path = (Path(__file__).resolve().parents[1]
            / "tooling/diagnostics/20261022-yearly-sensors/download.py")
    spec = importlib.util.spec_from_file_location("yearly_download", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EVIDENCE", tmp_path)
    monkeypatch.setattr(module, "HERE", tmp_path)
    jobs = [{"name": f"mss-l1-q{q}", "product": "mss-l1", "quarter": q}
            for q in (1, 2)]
    module.atomic_json(tmp_path / "jobs.json", jobs)
    for job in jobs:
        module.atomic_json(tmp_path / f"{job['name']}-status.json", {"state": "failed"})
    module.atomic_json(tmp_path / "download-status.json", {"state": "failed"})
    calls = []

    def fake_run(command, **kwargs):
        for job in jobs:
            assert json.loads((tmp_path / f"{job['name']}-status.json").read_text())["state"] == "queued"
        assert json.loads((tmp_path / "download-status.json").read_text())["state"] == "running"
        calls.append(command[-1])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.run()
    assert sorted(calls) == ["mss-l1-q1", "mss-l1-q2"]
    assert json.loads((tmp_path / "download-status.json").read_text())["state"] == "complete"
