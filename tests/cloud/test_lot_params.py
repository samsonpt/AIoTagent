from pathlib import Path

from cloud.graph import _lot_params
from common.recipe import load_recipe

RECIPE = load_recipe(Path(__file__).resolve().parents[2] / "bench" / "recipes" / "PN-4L-001.yaml")


def test_lot_params_plating_from_recipe_defaults():
    params = _lot_params({"lot_id": "L1", "plating": {}}, "plating", RECIPE)
    assert params["asd"] == RECIPE.window("plating", "current_density_asd").target
    assert params["additive_ml_l"] == RECIPE.window("plating", "additive_ml_l").target
    assert "time_min" in params


def test_lot_params_plating_maps_current_density_asd():
    params = _lot_params(
        {"lot_id": "L1", "plating": {"current_density_asd": 2.4, "additive_ml_l": 5.0}},
        "plating",
        RECIPE,
    )
    assert params["asd"] == 2.4
    assert params["additive_ml_l"] == 5.0


def test_lot_params_etch_maps_aliases():
    params = _lot_params(
        {
            "lot_id": "L1",
            "plating": {"thickness_zones": [24.0, 26.0]},
            "etch": {"conveyor_speed_m_min": 1.6, "etch_temp_c": 52.0},
        },
        "etch",
        RECIPE,
    )
    assert params["speed_m_min"] == 1.6
    assert params["temp_c"] == 52.0
    assert params["thickness_um"] == 25.0
