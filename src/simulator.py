"""
Deterministic Physics Simulation Engine.
Computes mass, kinetic energy (in Megatons of TNT), and Lunar Distance ratios
strictly using classical mechanics in Python to prevent LLM arithmetic hallucinations.
"""

import math
from dataclasses import dataclass

# Physical Constants
LUNAR_DISTANCE_KM: float = 384400.0          # Average Earth-Moon distance (km)
ASTEROID_DENSITY_KG_M3: float = 3000.0       # Standard stony-iron asteroid density (kg/m^3)
JOULES_PER_MEGATON_TNT: float = 4.184e15     # 1 Megaton of TNT in Joules


@dataclass(frozen=True)
class SimulationMetrics:
    """Immutable container for deterministic orbital and impact metrics."""

    estimated_mass_kg: float
    kinetic_energy_mt: float
    lunar_distance_ratio: float
    baseline_risk_tier: str


def run_physics_simulation(
    diameter_max_m: float,
    velocity_kps: float,
    miss_distance_km: float,
    is_hazardous: bool,
) -> SimulationMetrics:
    """
    Calculates physical properties of an approaching NEO assuming a spherical body.

    Args:
        diameter_max_m: Maximum estimated diameter in meters.
        velocity_kps: Relative velocity in kilometers per second.
        miss_distance_km: Closest approach distance to Earth in kilometers.
        is_hazardous: NASA's official potentially hazardous flag.

    Returns:
        SimulationMetrics object with rounded, verified numbers.
    """
    if diameter_max_m < 0 or velocity_kps < 0 or miss_distance_km < 0:
        raise ValueError("Orbital parameters (diameter, velocity, distance) must be non-negative.")

    # 1. Volume of a sphere: V = (4/3) * pi * r^3
    radius_m = diameter_max_m / 2.0
    volume_m3 = (4.0 / 3.0) * math.pi * (radius_m ** 3)

    # 2. Mass: m = rho * V
    mass_kg = ASTEROID_DENSITY_KG_M3 * volume_m3

    # 3. Kinetic Energy: Ek = 0.5 * m * v^2 (convert km/s to m/s)
    velocity_mps = velocity_kps * 1000.0
    kinetic_energy_joules = 0.5 * mass_kg * (velocity_mps ** 2)
    kinetic_energy_mt = kinetic_energy_joules / JOULES_PER_MEGATON_TNT

    # 4. Lunar Distance Ratio (LD)
    lunar_distance_ratio = miss_distance_km / LUNAR_DISTANCE_KM

    # 5. Deterministic Baseline Risk Tier (used as guardrail & fallback)
    baseline_risk = _classify_baseline_risk(
        kinetic_energy_mt=kinetic_energy_mt,
        lunar_distance_ratio=lunar_distance_ratio,
        is_hazardous=is_hazardous,
    )

    return SimulationMetrics(
        estimated_mass_kg=round(mass_kg, 2),
        kinetic_energy_mt=round(kinetic_energy_mt, 4),
        lunar_distance_ratio=round(lunar_distance_ratio, 2),
        baseline_risk_tier=baseline_risk,
    )


def _classify_baseline_risk(
    kinetic_energy_mt: float,
    lunar_distance_ratio: float,
    is_hazardous: bool,
) -> str:
    """Rule-based risk classification independent of the LLM."""
    if is_hazardous and lunar_distance_ratio <= 2.0 and kinetic_energy_mt >= 50.0:
        return "Critical"
    if is_hazardous and (lunar_distance_ratio <= 5.0 or kinetic_energy_mt >= 20.0):
        return "High"
    if is_hazardous or lunar_distance_ratio <= 10.0 or kinetic_energy_mt >= 5.0:
        return "Medium"
    return "Low"