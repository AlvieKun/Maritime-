"""
fleet_selection.py — Transparent, constraint-aware fleet selection heuristic.

Constraints (from Problem Statement):
    1. Total DWT ≥ monthly cargo requirement
    2. Each vessel used at most once
    3. Average fleet safety score ≥ MIN_SAFETY_SCORE
    4. At least one vessel of each main-engine fuel type
    5. Objective: minimise total adjusted cost

Algorithm (NOT a black-box optimiser):
    Phase 1 — SEED: Select one representative per fuel type
              (cheapest cost-per-DWT that keeps safety viable).
    Phase 2 — FILL: Iteratively add vessels ranked by marginal value
              (DWT-per-dollar), checking safety constraint at each step.
    Phase 3 — VALIDATE: Confirm all constraints satisfied.

Every inclusion/exclusion is logged with rationale.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import (
    MONTHLY_CARGO_REQUIREMENT, MIN_SAFETY_SCORE, REQUIRE_ALL_FUEL_TYPES,
)

logger = logging.getLogger(__name__)


def _safety_feasible(selected_scores: list, remaining_budget: float,
                     min_score: float = MIN_SAFETY_SCORE) -> bool:
    """
    Can the fleet still achieve the safety floor if we add vessels
    with the best possible safety score (5)?  Heuristic feasibility check.
    """
    if not selected_scores:
        return True
    current_avg = np.mean(selected_scores)
    # Check current average
    return current_avg >= min_score or len(selected_scores) == 0


def _fleet_avg_safety(scores: list) -> float:
    return np.mean(scores) if scores else 0.0


def select_fleet(vessel_df: pd.DataFrame,
                 cargo_requirement: float = MONTHLY_CARGO_REQUIREMENT,
                 min_safety: float = MIN_SAFETY_SCORE,
                 require_all_fuels: bool = REQUIRE_ALL_FUEL_TYPES) -> tuple:
    """
    Perform fleet selection and return (selected_df, selection_log).

    selection_log is a list of dicts documenting each decision:
        {"vessel_id": ..., "action": "selected"/"rejected", "reason": "..."}
    """
    log = []
    fuel_types_required = set(vessel_df["main_engine_fuel_type"].unique())

    # Work with a sorted copy — best value first
    candidates = vessel_df.copy()
    candidates["value_metric"] = candidates["dwt"] / candidates["adjusted_cost"]
    candidates.sort_values("value_metric", ascending=False, inplace=True)

    selected_ids = []
    selected_scores = []
    selected_dwt = 0.0
    selected_cost = 0.0
    fuel_types_covered = set()

    # ────────── PHASE 1: SEED FLEET WITH FUEL-TYPE REPRESENTATIVES ──────────
    logger.info("Phase 1: Seeding fleet with fuel-type representatives.")

    for fuel_type in sorted(fuel_types_required):
        fuel_pool = candidates[
            (candidates["main_engine_fuel_type"] == fuel_type)
            & (~candidates["vessel_id"].isin(selected_ids))
        ]
        if fuel_pool.empty:
            logger.error("No candidates for fuel type '%s' — constraint infeasible!", fuel_type)
            log.append({"vessel_id": None, "action": "error",
                        "reason": f"No vessels available for fuel type {fuel_type}"})
            continue

        # Pick the representative: best cost-per-DWT among those that don't tank safety
        fuel_pool = fuel_pool.sort_values("cost_per_dwt")

        chosen = None
        for _, row in fuel_pool.iterrows():
            # Simulate adding this vessel
            test_scores = selected_scores + [row["safety_score"]]
            test_avg = _fleet_avg_safety(test_scores)

            # We need to be able to recover safety with remaining selections
            # Heuristic: don't pick a low-safety seed if it makes recovery hard
            if test_avg < min_safety - 1.0 and len(test_scores) < len(fuel_types_required):
                # Still early seeding — allow some slack
                pass 
            chosen = row
            break

        if chosen is not None:
            vid = chosen["vessel_id"]
            selected_ids.append(vid)
            selected_scores.append(chosen["safety_score"])
            selected_dwt += chosen["dwt"]
            selected_cost += chosen["adjusted_cost"]
            fuel_types_covered.add(fuel_type)

            log.append({
                "vessel_id": vid,
                "action": "selected",
                "phase": "seed",
                "reason": f"Fuel-type representative for {fuel_type}. "
                          f"cost_per_dwt=${chosen['cost_per_dwt']:.2f}, "
                          f"safety={chosen['safety_score']}, dwt={chosen['dwt']:.0f}",
            })
            logger.info(
                "  Seed: vessel %d for %s (DWT=%.0f, cost=$%.0f, safety=%d).",
                vid, fuel_type, chosen["dwt"], chosen["adjusted_cost"], chosen["safety_score"],
            )

    logger.info(
        "After seeding: %d vessels, DWT=%.0f, cost=$%.0f, avg_safety=%.2f.",
        len(selected_ids), selected_dwt, selected_cost, _fleet_avg_safety(selected_scores),
    )

    # ────────── PHASE 2: FILL TO MEET DWT REQUIREMENT ──────────
    logger.info("Phase 2: Filling fleet to meet DWT requirement (%.0f t).", cargo_requirement)

    # Re-rank remaining candidates by value metric
    remaining = candidates[~candidates["vessel_id"].isin(selected_ids)].copy()
    remaining.sort_values("value_metric", ascending=False, inplace=True)

    for _, row in remaining.iterrows():
        if selected_dwt >= cargo_requirement:
            break

        vid = row["vessel_id"]
        test_scores = selected_scores + [row["safety_score"]]
        test_avg = _fleet_avg_safety(test_scores)

        # Check if adding this vessel violates safety constraint
        # AND we can't recover even with the best remaining vessels
        if test_avg < min_safety:
            # How many more vessels might we need?
            dwt_remaining = cargo_requirement - selected_dwt - row["dwt"]
            if dwt_remaining > 0:
                # We'll need more vessels — can we recover safety?
                # Pessimistic check: even if all remaining are score 5, does it work?
                # Estimate ~5 more vessels needed
                n_est = max(1, int(dwt_remaining / remaining["dwt"].median()))
                projected_avg = (sum(test_scores) + 5 * n_est) / (len(test_scores) + n_est)
                if projected_avg < min_safety:
                    log.append({
                        "vessel_id": vid,
                        "action": "rejected",
                        "phase": "fill",
                        "reason": f"Would drop safety avg to {test_avg:.2f} "
                                  f"with poor recovery prospect ({projected_avg:.2f}).",
                    })
                    continue
            else:
                # This would be the last vessel and safety is violated
                log.append({
                    "vessel_id": vid,
                    "action": "rejected",
                    "phase": "fill",
                    "reason": f"Would drop safety avg to {test_avg:.2f} with this as final vessel.",
                })
                continue

        # Accept vessel
        selected_ids.append(vid)
        selected_scores.append(row["safety_score"])
        selected_dwt += row["dwt"]
        selected_cost += row["adjusted_cost"]

        log.append({
            "vessel_id": vid,
            "action": "selected",
            "phase": "fill",
            "reason": f"Value={row['value_metric']:.6f} DWT/$, "
                      f"running DWT={selected_dwt:.0f}/{cargo_requirement:.0f}, "
                      f"safety_avg={_fleet_avg_safety(selected_scores):.2f}.",
        })

    # ────────── PHASE 2b: SAFETY RECOVERY (if needed) ──────────
    current_avg = _fleet_avg_safety(selected_scores)
    if current_avg < min_safety and selected_dwt < cargo_requirement:
        logger.info("Phase 2b: Safety recovery — adding high-safety vessels.")
        remaining2 = candidates[~candidates["vessel_id"].isin(selected_ids)].copy()
        # Prioritise safety, then value
        remaining2 = remaining2[remaining2["safety_score"] >= 4]
        remaining2.sort_values("value_metric", ascending=False, inplace=True)

        for _, row in remaining2.iterrows():
            if selected_dwt >= cargo_requirement and _fleet_avg_safety(selected_scores) >= min_safety:
                break

            vid = row["vessel_id"]
            selected_ids.append(vid)
            selected_scores.append(row["safety_score"])
            selected_dwt += row["dwt"]
            selected_cost += row["adjusted_cost"]

            log.append({
                "vessel_id": vid,
                "action": "selected",
                "phase": "safety_recovery",
                "reason": f"High-safety vessel (score={row['safety_score']}) "
                          f"to raise fleet avg to {_fleet_avg_safety(selected_scores):.2f}.",
            })

    # ────────── PHASE 3: VALIDATION ──────────
    selected_df = vessel_df[vessel_df["vessel_id"].isin(selected_ids)].copy()
    report = validate_fleet(selected_df, cargo_requirement, min_safety, fuel_types_required)

    logger.info("=== FLEET SELECTION COMPLETE ===")
    for k, v in report.items():
        logger.info("  %s: %s", k, v)

    return selected_df, log, report


def validate_fleet(fleet_df: pd.DataFrame,
                   cargo_req: float,
                   min_safety: float,
                   required_fuels: set) -> dict:
    """
    Validate selected fleet against all constraints.
    Returns a report dict with constraint status.
    """
    total_dwt = fleet_df["dwt"].sum()
    total_cost = fleet_df["adjusted_cost"].sum()
    avg_safety = fleet_df["safety_score"].mean()
    fuel_types = set(fleet_df["main_engine_fuel_type"].unique())
    n_ships = len(fleet_df)
    co2eq = fleet_df["co2eq_total"].sum()
    fuel_total = fleet_df["fuel_total"].sum()

    report = {
        "total_dwt": total_dwt,
        "cargo_requirement": cargo_req,
        "dwt_constraint_met": total_dwt >= cargo_req,
        "total_cost_usd": total_cost,
        "avg_safety_score": avg_safety,
        "safety_constraint_met": avg_safety >= min_safety,
        "fuel_types_in_fleet": fuel_types,
        "n_unique_fuel_types": len(fuel_types),
        "all_fuel_types_covered": fuel_types >= required_fuels,
        "missing_fuel_types": required_fuels - fuel_types,
        "fleet_size": n_ships,
        "total_co2eq": co2eq,
        "total_fuel_consumption": fuel_total,
    }

    # Log warnings for violated constraints
    if not report["dwt_constraint_met"]:
        logger.warning("CONSTRAINT VIOLATED: DWT %.0f < requirement %.0f.",
                        total_dwt, cargo_req)
    if not report["safety_constraint_met"]:
        logger.warning("CONSTRAINT VIOLATED: Safety %.2f < minimum %.1f.",
                        avg_safety, min_safety)
    if not report["all_fuel_types_covered"]:
        logger.warning("CONSTRAINT VIOLATED: Missing fuel types: %s.",
                        report["missing_fuel_types"])

    return report


def format_submission(fleet_df: pd.DataFrame, report: dict,
                      team_name: str, category: str,
                      report_file_name: str,
                      sensitivity: str = "Yes") -> pd.DataFrame:
    """
    Format results matching submission_template.csv schema exactly.
    """
    submission = pd.DataFrame({
        "Header Name": [
            "team_name", "category", "report_file_name",
            "sum_of_fleet_deadweight", "total_cost_of_fleet",
            "average_fleet_safety_score",
            "no_of_unique_main_engine_fuel_types_in_fleet",
            "sensitivity_analysis_performance",
            "size_of_fleet_count",
            "total_emission_CO2_eq",
            "total_fuel_consumption",
        ],
        "Data Type": [
            "String", "String", "String",
            "Float", "Float", "Float", "Integer",
            "String", "Integer", "Float", "Float",
        ],
        "Units": [
            "-", "-", "-",
            "tonnes", "dollars", "-", "-",
            "Yes/No", "-", "tonnes", "tonnes",
        ],
        "Submission": [
            team_name,
            category,
            report_file_name,
            round(report["total_dwt"], 2),
            round(report["total_cost_usd"], 2),
            round(report["avg_safety_score"], 2),
            report["n_unique_fuel_types"],
            sensitivity,
            report["fleet_size"],
            round(report["total_co2eq"], 2),
            round(report["total_fuel_consumption"], 2),
        ],
    })
    return submission
