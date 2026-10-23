"""Assert that a release includes runtime assets and excludes private pilot data."""
import argparse
from pathlib import Path
import tarfile
import zipfile


def check(root):
    wheels, sources = list(root.glob("*.whl")), list(root.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Use a directory containing exactly one release"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        assert all(f"landsat_grits/web/{name}" in names for name in ("app.js", "style.css", "index.html"))
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        assert "landsat_grits/grid_source.py" in names
    with tarfile.open(sources[0]) as archive:
        names = [name.split("/", 1)[-1] for name in archive.getnames()]
        for required in ("LICENSE", "NOTICE.md", "examples/offline_demo.py",
                         "tests/test_offline_demo.py", "tooling/release/smoke_installed.py",
                         "tooling/diagnostics/20261022-yearly-sensors/download.py"):
            assert required in names, required
        assert not any(name.startswith(("outputs/", "docs/evidence/", ".venv/"))
                       or name.endswith(".log") for name in names)
    print("Release content checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    check(parser.parse_args().directory)
