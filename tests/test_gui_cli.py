from pathlib import Path
import sys
from types import SimpleNamespace

from landsat_grits.cli import main


def test_gui_dispatch_and_clean_interrupt(monkeypatch):
    calls = []

    def serve(root, *, port):
        calls.append((root, port))
        raise KeyboardInterrupt

    monkeypatch.setitem(sys.modules, "landsat_grits.viewer", SimpleNamespace(serve=serve))
    assert main(["gui", "outputs", "--port", "8766"]) == 0
    assert calls == [(Path("outputs"), 8766)]


def test_gui_failure_is_reported(monkeypatch, capsys):
    def serve(root, *, port):
        raise ValueError("Archive root does not exist")

    monkeypatch.setitem(sys.modules, "landsat_grits.viewer", SimpleNamespace(serve=serve))
    assert main(["gui", "missing"]) == 1
    assert "Archive root does not exist" in capsys.readouterr().err
