"""Regression checks for the actual two-scene acceptance scanner adapter."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "append_second_path",
    Path(__file__).resolve().parents[1]
    / "tooling/diagnostics/20261021_shenzhen/append_second_path.py",
)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_time_plane_reads_the_selected_scene_without_mixing():
    values = np.arange(2 * 3 * 4 * 5).reshape(2, 3, 4, 5)
    first, second = HELPER.TimePlane(values, 0), HELPER.TimePlane(values, 1)
    assert second.shape == (1, 3, 4, 5)
    np.testing.assert_array_equal(second[0, 2, :, 1:3], values[1, 2, :, 1:3])
    assert first[0, 0, 0, 0] != second[0, 0, 0, 0]
    with pytest.raises(ValueError, match="time index zero"):
        second[1, 0, 0, 0]
    with pytest.raises(IndexError):
        HELPER.TimePlane(values, 2)
