"""
Unit tests for the deterministic physics simulation engine.
Verifies kinetic energy, lunar distance ratios, and baseline risk tiers.
"""

import pytest
from src.simulator import run_physics_simulation


def test_lunar_distance_and_kinetic_energy() -> None:
    """Verifies exact physics calculation for a 100m asteroid at 20 km/s."""
    result = run_physics_simulation(
        diameter_max_m=100.0,
        velocity_kps=20.0,
        miss_distance_km=384400.0,
        is_hazardous=True,
    )
    assert result.lunar_distance_ratio == 1.0
    assert result.kinetic_energy_mt > 0
    assert result.baseline_risk_tier in ("High", "Critical")


def test_low_risk_small_distant_object() -> None:
    """Verifies small, distant non-hazardous objects classify as Low risk."""
    result = run_physics_simulation(
        diameter_max_m=15.0,
        velocity_kps=8.0,
        miss_distance_km=15_000_000.0,
        is_hazardous=False,
    )
    assert result.baseline_risk_tier == "Low"


def test_negative_inputs_raise_value_error() -> None:
    """Ensures invalid negative physical parameters raise ValueError."""
    with pytest.raises(ValueError):
        run_physics_simulation(
            diameter_max_m=-10.0,
            velocity_kps=10.0,
            miss_distance_km=500000.0,
            is_hazardous=False,
        )