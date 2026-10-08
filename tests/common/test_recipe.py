from pathlib import Path

import pytest

from common.recipe import ParamWindow, Recipe, SpecLimit, load_recipe

ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = ROOT / "bench" / "recipes" / "PN-4L-001.yaml"


def test_param_window_rejects_target_outside_range():
    with pytest.raises(ValueError):
        ParamWindow(min=1.0, max=2.0, target=2.5)


def test_param_window_contains_is_closed_interval():
    w = ParamWindow(min=1.0, max=2.0, target=1.5)
    assert w.contains(1.0)
    assert w.contains(2.0)
    assert not w.contains(2.0001)


def test_param_window_clamp():
    w = ParamWindow(min=1.0, max=2.0, target=1.5)
    assert w.clamp(0.5) == 1.0
    assert w.clamp(3.0) == 2.0
    assert w.clamp(1.2) == 1.2


def test_spec_limit_one_sided():
    s = SpecLimit(max=25.0, target=15.0)
    assert s.contains(-100.0)
    assert s.contains(25.0)
    assert not s.contains(25.1)


def test_violations_formats_numpy_scalars_as_float():
    recipe = load_recipe(RECIPE_PATH)
    np = pytest.importorskip("numpy")
    msgs = recipe.violations("plating", {"additive_ml_l": np.float64(1.31)})
    assert msgs == ["plating.additive_ml_l=1.31 outside [3.0, 6.0]"]


def test_violations_reports_only_defined_params():
    recipe = load_recipe(RECIPE_PATH)
    assert recipe.violations("etch", {"sg": 1.31, "unknown": 1}) == [
        "etch.sg=1.31 outside [1.26, 1.3]"
    ]
    assert recipe.violations("etch", {"sg": 1.28}) == []


def test_load_recipe_values():
    recipe = load_recipe(str(RECIPE_PATH))
    assert isinstance(recipe, Recipe)
    assert recipe.part_no == "PN-4L-001"
    assert recipe.lot_size == 12
    assert recipe.window("etch", "sg").target == 1.28
    assert recipe.constants["hits_per_lot"] == 600
    assert recipe.specs["hole_roughness_um"].min is None


def test_window_missing_raises_key_error():
    recipe = load_recipe(RECIPE_PATH)
    with pytest.raises(KeyError):
        recipe.window("etch", "nope")
    with pytest.raises(KeyError):
        recipe.window("nope", "sg")
