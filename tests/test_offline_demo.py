import importlib.util
from pathlib import Path

import pytest
import requests


def test_demo_is_standalone_offline_and_never_replaces_existing_output(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Offline example must not use the network")

    monkeypatch.setattr(requests.Session, "request", no_network)
    path = Path(__file__).resolve().parents[1] / "examples/offline_demo.py"
    spec = importlib.util.spec_from_file_location("offline_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.create_demo(tmp_path / "demo")
    assert result["synthetic"] and result["scenes"] == 2
    assert (tmp_path / "demo/resolver.json").is_file()
    with pytest.raises(FileExistsError):
        module.create_demo(tmp_path / "demo")
