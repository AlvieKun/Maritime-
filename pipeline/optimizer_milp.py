"""
optimizer_milp.py — MILP-based fleet optimizer using PuLP.

Replaces the greedy heuristic in fleet_selection.py with an exact
Integer Linear Programming formulation.

METHODOLOGY LOCK: This module ONLY changes the fleet selection algorithm.
It does NOT modify any emissions, cost, or fuel calculations.

Formulation:
    Decision variables:  x_i ∈ {0, 1}  for each vessel i
    Objective:           minimize Σ(x_i × adjusted_cost_i)
    Constraints:
        1. Σ(x_i × dwt_i) ≥ required_dwt           (cargo demand)
        2. Σ(x_i × safety_i) ≥ min_safety × Σ(x_i) (avg safety floor)
           → linearized: Σ(x_i × (safety_i − min_safety)) ≥ 0
        3. ∀ fuel_type f: Σ(x_i | fuel_i = f) ≥ 1   (fuel diversity)
        4. (optional) Σ(x_i) = fixed_fleet_size      (fleet size lock)
"""

import time
import logging
import pandas as pd
import numpy as np
import pulp

logger = logging.getLogger(__name__)


def validate_fleet_standalone(fleet_df: pd.DataFrame,
                              cargo_requirement: float,
                              min_safety: float,
                              required_fuel_types: set) -> dict:
    """
    Standalone fleet validator — checks all competition constraints.
    Returns dict with pass/fail for each constraint and computed metrics.
    """
    n = len(fleet_df)
    total_dwt = fleet_df["dwt"].sum()
    total_cost = fleet_df["adjusted_cost"].sum()
    avg_safety = fleet_df["safety_score"].mean() if n > 0 else 0.0
    fuel_types = set(fleet_df["main_engine_fuel_type"].unique())
    unique_ids = fleet_df["vessel_id"].nunique()
    co2eq = fleet_df["co2eq_total"].sum() if "co2eq_total" in fleet_df.columns else 0.0
    fuel_total = fleet_df["fuel_total"].sum() if "fuel_total" in fleet_df.columns else 0.0

    no_duplicates = (unique_ids == n)
    dwt_ok = (total_dwt >= cargo_requirement)
    safety_ok = (avg_safety >= min_safety - 1e-9)  # small tolerance for float
    fuels_ok = fuel_types >= required_fuel_types

    return {
        "fleet_size": n,
        "total_dwt": total_dwt,
        "cargo_requirement": cargo_requirement,
        "dwt_met": dwt_ok,
        "total_cost": total_cost,
        "avg_safety": avg_safety,
        "min_safety_required": min_safety,
        "safety_met": safety_ok,
        "fuel_types_present": fuel_types,
        "n_fuel_types": len(fuel_types),
        "fuel_diversity_met": fuels_ok,
        "missing_fuels": required_fuel_types - fuel_types,
        "no_duplicates": no_duplicates,
        "all_constraints_met": dwt_ok and safety_ok and fuels_ok and no_duplicates,
        "total_co2eq": co2eq,
        "total_fuel": fuel_total,
    }


def solve_milp(vessel_df: pd.DataFrame,
               cargo_requirement: float,
               min_safety: float,
               require_all_fuels: bool = True,
               fixed_fleet_size: int = None,
               max_fleet_size: int = None,
               max_cost: float = None,
               time_limit: int = 120,
               label: str = "milp") -> dict:
    """
    Solve the fleet selection problem as a Mixed-Integer Linear Program.

    Parameters
    ----------
    vessel_df : DataFrame with columns: vessel_id, dwt, safety_score,
                main_engine_fuel_type, adjusted_cost, co2eq_total, fuel_total
    cargo_requirement : minimum total DWT
    min_safety : minimum average safety score
    require_all_fuels : require at least one vessel per fuel type
    fixed_fleet_size : if set, fleet must be exactly this size
    max_fleet_size : if set, fleet can be at most this size
    max_cost : if set, add a cost ceiling constraint
    time_limit : solver time limit in seconds
    label : scenario label

    Returns
    -------
    dict with: status, fleet_df, validation, solve_time, objective_value
    """
    start = time.time()
    n = len(vessel_df)
    vessels = vessel_df.reset_index(drop=True)
    indices = range(n)

    # ── Build MILP ──
    prob = pulp.LpProblem(f"FleetSelection_{label}", pulp.LpMinimize)

    # Decision variables: x_i ∈ {0, 1}
    x = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in indices]

    # Objective: minimize total adjusted cost
    prob += pulp.lpSum(x[i] * vessels.loc[i, "adjusted_cost"] for i in indices), "TotalCost"

    # Constraint 1: DWT >= cargo requirement
    prob += (
        pulp.lpSum(x[i] * vessels.loc[i, "dwt"] for i in indices) >= cargo_requirement,
        "DWT_Requirement",
    )

    # Constraint 2: Average safety >= min_safety
    # Linearized: Σ x_i * (safety_i - min_safety) >= 0
    prob += (
        pulp.lpSum(x[i] * (vessels.loc[i, "safety_score"] - min_safety) for i in indices) >= 0,
        "Safety_Floor",
    )

    # Constraint 3: Fuel diversity — at least 1 vessel per fuel type
    if require_all_fuels:
        fuel_types = vessels["main_engine_fuel_type"].unique()
        for ft in fuel_types:
            ft_indices = vessels.index[vessels["main_engine_fuel_type"] == ft].tolist()
            prob += (
                pulp.lpSum(x[i] for i in ft_indices) >= 1,
                f"FuelType_{ft.replace(' ', '_').replace('(', '').replace(')', '')}",
            )

    # Constraint 4 (optional): Fixed fleet size
    if fixed_fleet_size is not None:
        prob += (
            pulp.lpSum(x[i] for i in indices) == fixed_fleet_size,
            "FixedFleetSize",
        )

    # Constraint 5 (optional): Max fleet size
    if max_fleet_size is not None and fixed_fleet_size is None:
        prob += (
            pulp.lpSum(x[i] for i in indices) <= max_fleet_size,
            "MaxFleetSize",
        )

    # Constraint 6 (optional): Cost ceiling
    if max_cost is not None:
        prob += (
            pulp.lpSum(x[i] * vessels.loc[i, "adjusted_cost"] for i in indices) <= max_cost,
            "CostCeiling",
        )

    # ── Solve ──
    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit)
    prob.solve(solver)

    solve_time = time.time() - start
    status = pulp.LpStatus[prob.status]

    if status != "Optimal":
        logger.warning("MILP %s: status=%s (not optimal). Solve time: %.2fs.", label, status, solve_time)
        return {
            "status": status,
            "fleet_df": None,
            "validation": None,
            "solve_time": solve_time,
            "objective_value": None,
            "label": label,
        }

    # ── Extract solution ──
    selected_mask = [pulp.value(x[i]) > 0.5 for i in indices]
    fleet_df = vessels[selected_mask].copy()
    obj_val = pulp.value(prob.objective)

    # Validate
    required_fuels = set(vessels["main_engine_fuel_type"].unique()) if require_all_fuels else set()
    validation = validate_fleet_standalone(fleet_df, cargo_requirement, min_safety, required_fuels)

    logger.info(
        "MILP %s: status=%s, cost=$%.2f, safety=%.2f, fleet=%d, DWT=%.0f, time=%.2fs.",
        label, status, obj_val, validation["avg_safety"],
        validation["fleet_size"], validation["total_dwt"], solve_time,
    )

    return {
        "status": status,
        "fleet_df": fleet_df,
        "validation": validation,
        "solve_time": solve_time,
        "objective_value": obj_val,
        "label": label,
    }


def pareto_sweep(vessel_df: pd.DataFrame,
                 cargo_requirement: float,
                 safety_levels: list,
                 fixed_fleet_size: int = None,
                 max_fleet_size: int = None) -> pd.DataFrame:
    """
    For each safety level, solve MILP to find minimum cost.
    Returns a DataFrame of Pareto frontier points.
    """
    results = []
    required_fuels = set(vessel_df["main_engine_fuel_type"].unique())

    for safety in safety_levels:
        res = solve_milp(
            vessel_df,
            cargo_requirement=cargo_requirement,
            min_safety=safety,
            require_all_fuels=True,
            fixed_fleet_size=fixed_fleet_size,
            max_fleet_size=max_fleet_size,
            label=f"pareto_safety_{safety:.1f}",
        )

        if res["status"] == "Optimal" and res["validation"] is not None:
            v = res["validation"]
            results.append({
                "scenario_label": res["label"],
                "min_safety_target": safety,
                "fleet_size": v["fleet_size"],
                "total_cost": v["total_cost"],
                "avg_safety": v["avg_safety"],
                "total_dwt": v["total_dwt"],
                "emissions_co2e": v["total_co2eq"],
                "total_fuel": v["total_fuel"],
                "fuel_types_present": len(v["fuel_types_present"]),
                "solve_time": res["solve_time"],
                "status": "feasible",
            })
        else:
            results.append({
                "scenario_label": f"pareto_safety_{safety:.1f}",
                "min_safety_target": safety,
                "fleet_size": None,
                "total_cost": None,
                "avg_safety": None,
                "total_dwt": None,
                "emissions_co2e": None,
                "total_fuel": None,
                "fuel_types_present": None,
                "solve_time": res["solve_time"],
                "status": res["status"],
            })

    return pd.DataFrame(results)


def check_other_team_claim(vessel_df: pd.DataFrame,
                           cargo_requirement: float,
                           target_fleet_size: int = 22,
                           target_safety: float = 4.0,
                           target_cost: float = 20_300_000.0) -> dict:
    """
    Task 5: Check if a fleet with the claimed parameters is feasible.
    Try exact fleet_size=22, safety>=4.0, cost<=20.3M.
    If infeasible, find the best achievable cost at safety>=4.0 with fleet_size=22.
    """
    logger.info("=" * 60)
    logger.info("CLAIM CHECK: fleet=%d, safety>=%.1f, cost<=$%.0f",
                target_fleet_size, target_safety, target_cost)
    logger.info("=" * 60)

    # Attempt 1: Try with cost ceiling
    res_constrained = solve_milp(
        vessel_df,
        cargo_requirement=cargo_requirement,
        min_safety=target_safety,
        require_all_fuels=True,
        fixed_fleet_size=target_fleet_size,
        max_cost=target_cost,
        label="claim_check_constrained",
    )

    # Attempt 2: Find minimum cost at this safety/fleet (no cost ceiling)
    res_best = solve_milp(
        vessel_df,
        cargo_requirement=cargo_requirement,
        min_safety=target_safety,
        require_all_fuels=True,
        fixed_fleet_size=target_fleet_size,
        label="claim_check_best",
    )

    # Attempt 3: Try with no fleet size constraint (find absolute minimum)
    res_any_size = solve_milp(
        vessel_df,
        cargo_requirement=cargo_requirement,
        min_safety=target_safety,
        require_all_fuels=True,
        label="claim_check_any_size",
    )

    claim_result = {
        "target_fleet_size": target_fleet_size,
        "target_safety": target_safety,
        "target_cost": target_cost,
        "exact_claim_feasible": res_constrained["status"] == "Optimal",
    }

    if res_best["status"] == "Optimal":
        v = res_best["validation"]
        claim_result["best_cost_at_target"] = v["total_cost"]
        claim_result["best_safety_at_target"] = v["avg_safety"]
        claim_result["best_fleet_size"] = v["fleet_size"]
        claim_result["gap_to_claim"] = v["total_cost"] - target_cost
        claim_result["gap_pct"] = (v["total_cost"] - target_cost) / target_cost * 100
    else:
        claim_result["best_cost_at_target"] = None
        claim_result["note"] = "Infeasible even without cost ceiling at fleet_size=22"

    if res_any_size["status"] == "Optimal":
        v2 = res_any_size["validation"]
        claim_result["min_cost_any_size"] = v2["total_cost"]
        claim_result["fleet_size_at_min_cost"] = v2["fleet_size"]
        claim_result["safety_at_min_cost"] = v2["avg_safety"]

    return claim_result
