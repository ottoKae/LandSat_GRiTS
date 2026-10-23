"""Run with the newly installed wheel's Python, using -I to isolate source paths."""
import argparse
import importlib.util
from pathlib import Path
import threading
from urllib.request import urlopen

import landsat_grits
from landsat_grits.viewer import create_server


def main(output):
    repo = Path(__file__).resolve().parents[2]
    assert not Path(landsat_grits.__file__).resolve().is_relative_to(repo / "src")
    spec = importlib.util.spec_from_file_location("offline_demo", repo / "examples/offline_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    result = demo.create_demo(output)
    assert result["validation"]["manifest_satisfied"]
    with create_server(output / "archive", port=0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for route in ("/", "/app.js", "/style.css", "/api/catalog"):
                with urlopen(f"http://127.0.0.1:{server.server_port}{route}", timeout=30) as response:
                    assert response.status == 200 and response.read()
        finally:
            server.shutdown()
            thread.join()
    print(f"Installed {landsat_grits.__version__}: offline scenes and GUI passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args().output)
