"""
run_experiments.py — Optimizer experiments for Maritime Hackathon 2026.

Runs the full data pipeline (stages 1–5) to produce vessel-level costs,
then applies MILP optimization to explore the Pareto frontier of
cost vs safety.

METHODOLOGY LOCK: This script does NOT modify any emissions, cost, or
fuel calculations. It only replaces the fleet selection step.

All outputs go to output/experiments/ — official outputs are NOT touched.

Usage:
    python run_experiments.py
"""

import os
import sys
import time
import logging
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pipeline.config import (
    OUTPUT_DIR, MONTHLY_CARGO_REQUIREMENT, MIN_SAFETY_SCORE,
)
from pipeline.data_ingestion import load_all_data
from pipeline.ais_behavior import run_ais_pipeline
from pipeline.engine_fuel import run_engine_fuel_pipeline
from pipeline.emissions import run_emissions_pipeline
from pipeline.cost_model import run_cost_pipeline
from pipeline.optimizer_milp import (
    solve_milp, pareto_sweep, check_other_team_claim,
    validate_fleet_standalone,
)
from pipeline.fleet_selection import select_fleet


def setup_logging():
    os.makedirs(os.path.join(OUTPUT_DIR, "experiments"), exist_ok=True)
    log_path = os.path.join(OUTPUT_DIR, "experiments", "experiments.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, mode="w"),
        ],
    )


def build_vessel_data() -> pd.DataFrame:
    """Run stages 1–5 of the pipeline to produce vessel-level cost data."""
    logger = logging.getLogger("experiments")
    logger.info("Building vessel data (stages 1–5, methodology-compliant)...")

    data = load_all_data()
    ais_df = data["movements"]
    cf_table = data["cf_table"]
    reference_lcv = data["reference_lcv"]
    fuel_cost_tbl = data["fuel_cost_table"]
    ship_cost_info = data["ship_cost_info"]
    safety_adj = data["safety_adjustment"]
    llaf_table = data["llaf_table"]
    carbon_cost_v = data["carbon_cost"]

    ais_df = run_ais_pipeline(ais_df)
    ais_df, vessel_fuel = run_engine_fuel_pipeline(ais_df, cf_table, reference_lcv)
    ais_df, vessel_emis = run_emissions_pipeline(ais_df, cf_table, llaf_table)

    vessel_df = vessel_fuel.merge(
        vessel_emis[["vessel_id", "emis_CO2_total", "emis_N2O_total", "emis_CH4_total",
                      "co2eq_total", "co2eq_per_fuel_tonne", "co2eq_per_dwt"]],
        on="vessel_id", how="left",
    )
    vessel_df = run_cost_pipeline(
        vessel_df, fuel_cost_tbl, carbon_cost_v, ship_cost_info, safety_adj,
    )

    logger.info("Vessel data ready: %d vessels.", len(vessel_df))
    return vessel_df


def run_greedy_baseline(vessel_df, min_safety):
    """Run the existing greedy heuristic for comparison."""
    fleet_df, _, report = select_fleet(
        vessel_df,
        cargo_requirement=MONTHLY_CARGO_REQUIREMENT,
        min_safety=min_safety,
    )
    return fleet_df, report


def main():
    setup_logging()
    logger = logging.getLogger("experiments")
    exp_dir = os.path.join(OUTPUT_DIR, "experiments")
    os.makedirs(exp_dir, exist_ok=True)

    total_start = time.time()

    # ── STAGE 1: Build vessel data (methodology-compliant, read-only) ──
    vessel_df = build_vessel_data()

    required_fuels = set(vessel_df["main_engine_fuel_type"].unique())
    cargo_req = MONTHLY_CARGO_REQUIREMENT

    # ══════════════════════════════════════════════════════════════════
    # EXPERIMENT 1: Greedy Baseline vs MILP at safety >= 3.0
    # ══════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("EXPERIMENT 1: Greedy vs MILP at safety >= 3.0")
    logger.info("=" * 70)

    # Greedy baseline
    greedy_fleet, greedy_report = run_greedy_baseline(vessel_df, 3.0)
    greedy_val = validate_fleet_standalone(greedy_fleet, cargo_req, 3.0, required_fuels)

    # MILP: unconstrained fleet size
    milp_free = solve_milp(vessel_df, cargo_req, min_safety=3.0, label="milp_free_s3")

    # MILP: fleet size fixed at 22 (same as greedy)
    milp_22 = solve_milp(vessel_df, cargo_req, min_safety=3.0, fixed_fleet_size=22,
                         label="milp_fixed22_s3")

    # MILP: try fleet sizes 18–26
    fleet_size_results = []
    for fs in range(18, 27):
        res = solve_milp(vessel_df, cargo_req, min_safety=3.0, fixed_fleet_size=fs,
                         label=f"milp_fs{fs}_s3")
        if res["status"] == "Optimal":
            v = res["validation"]
            fleet_size_results.append({
                "fleet_size": fs, "total_cost": v["total_cost"],
                "avg_safety": v["avg_safety"], "total_dwt": v["total_dwt"],
                "emissions": v["total_co2eq"],
            })

    fs_df = pd.DataFrame(fleet_size_results)
    fs_df.to_csv(os.path.join(exp_dir, "fleet_size_sweep_s3.csv"), index=False)

    # ══════════════════════════════════════════════════════════════════
    # EXPERIMENT 2: Can MILP beat greedy on BOTH cost AND safety?
    # ══════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("EXPERIMENT 2: Can MILP find lower cost AND higher safety?")
    logger.info("=" * 70)

    greedy_cost = greedy_val["total_cost"]
    greedy_safety = greedy_val["avg_safety"]

    # Try MILP with safety > greedy_safety AND cost ceiling at greedy_cost
    # Search for highest feasible safety with cost <= greedy baseline
    domination_results = []
    for target_safety in [round(greedy_safety + 0.1 * i, 2) for i in range(1, 20)]:
        if target_safety > 5.0:
            break
        res = solve_milp(
            vessel_df, cargo_req, min_safety=target_safety,
            max_cost=greedy_cost,
            label=f"dominate_s{target_safety:.1f}",
        )
        if res["status"] == "Optimal":
            v = res["validation"]
            domination_results.append({
                "target_safety": target_safety,
                "total_cost": v["total_cost"],
                "avg_safety": v["avg_safety"],
                "fleet_size": v["fleet_size"],
                "total_dwt": v["total_dwt"],
                "feasible": True,
            })
        else:
            domination_results.append({
                "target_safety": target_safety,
                "total_cost": None, "avg_safety": None,
                "fleet_size": None, "total_dwt": None,
                "feasible": False,
            })
            # Once we hit infeasible, higher targets will also be infeasible
            # (but continue to confirm)

    dom_df = pd.DataFrame(domination_results)
    dom_df.to_csv(os.path.join(exp_dir, "domination_search.csv"), index=False)

    # ══════════════════════════════════════════════════════════════════
    # EXPERIMENT 3: Pareto Frontier (min cost at each safety level)
    # ══════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("EXPERIMENT 3: Pareto Frontier Sweep")
    logger.info("=" * 70)

    safety_levels = [3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2, 4.4, 4.6, 4.8, 5.0]

    # Unconstrained fleet size
    pareto_free = pareto_sweep(vessel_df, cargo_req, safety_levels)
    pareto_free["fleet_constraint"] = "unconstrained"

    # Fleet size = 22
    pareto_22 = pareto_sweep(vessel_df, cargo_req, safety_levels, fixed_fleet_size=22)
    pareto_22["fleet_constraint"] = "fixed_22"

    pareto_all = pd.concat([pareto_free, pareto_22], ignore_index=True)
    pareto_all.to_csv(os.path.join(exp_dir, "pareto.csv"), index=False)

    # ══════════════════════════════════════════════════════════════════
    # EXPERIMENT 4: Other Team Claim Check
    # ══════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("EXPERIMENT 4: Other Team Claim Feasibility Check")
    logger.info("=" * 70)

    claim = check_other_team_claim(
        vessel_df, cargo_req,
        target_fleet_size=22,
        target_safety=4.0,
        target_cost=20_300_000.0,
    )

    # ══════════════════════════════════════════════════════════════════
    # SAVE RESULTS SUMMARY
    # ══════════════════════════════════════════════════════════════════
    total_time = time.time() - total_start

    # Save MILP-optimal fleet for base scenario
    if milp_free["fleet_df"] is not None:
        milp_free["fleet_df"].to_csv(os.path.join(exp_dir, "milp_optimal_fleet_s3.csv"), index=False)
    if milp_22["fleet_df"] is not None:
        milp_22["fleet_df"].to_csv(os.path.join(exp_dir, "milp_optimal_fleet_s3_fs22.csv"), index=False)

    # Save the best fleet at safety>=4.0
    res_s4 = solve_milp(vessel_df, cargo_req, min_safety=4.0, label="best_s4")
    if res_s4["fleet_df"] is not None:
        res_s4["fleet_df"].to_csv(os.path.join(exp_dir, "milp_optimal_fleet_s4.csv"), index=False)

    res_s4_22 = solve_milp(vessel_df, cargo_req, min_safety=4.0,
                            fixed_fleet_size=22, label="best_s4_fs22")
    if res_s4_22["fleet_df"] is not None:
        res_s4_22["fleet_df"].to_csv(os.path.join(exp_dir, "milp_optimal_fleet_s4_fs22.csv"), index=False)

    # ── Print comprehensive summary ──
    logger.info("\n" + "=" * 70)
    logger.info("EXPERIMENT RESULTS SUMMARY")
    logger.info("=" * 70)

    logger.info("\n--- Greedy Baseline (safety >= 3.0) ---")
    logger.info(f"  Fleet: {greedy_val['fleet_size']} ships, Cost: ${greedy_val['total_cost']:,.2f}, Safety: {greedy_val['avg_safety']:.2f}, DWT: {greedy_val['total_dwt']:,.0f}")

    if milp_free["validation"]:
        v = milp_free["validation"]
        logger.info("\n--- MILP Optimal (safety >= 3.0, unconstrained size) ---")
        logger.info(f"  Fleet: {v['fleet_size']} ships, Cost: ${v['total_cost']:,.2f}, Safety: {v['avg_safety']:.2f}, DWT: {v['total_dwt']:,.0f}")
        savings = greedy_val["total_cost"] - v["total_cost"]
        logger.info(f"  Savings vs greedy: ${savings:,.2f} ({savings / greedy_val['total_cost'] * 100:.2f}%)")

    if milp_22["validation"]:
        v = milp_22["validation"]
        logger.info("\n--- MILP Optimal (safety >= 3.0, fleet=22) ---")
        logger.info(f"  Fleet: {v['fleet_size']} ships, Cost: ${v['total_cost']:,.2f}, Safety: {v['avg_safety']:.2f}, DWT: {v['total_dwt']:,.0f}")
        savings = greedy_val["total_cost"] - v["total_cost"]
        logger.info(f"  Savings vs greedy: ${savings:,.2f} ({savings / greedy_val['total_cost'] * 100:.2f}%)")

    # Domination check
    feasible_dom = dom_df[dom_df["feasible"]]
    if len(feasible_dom) > 0:
        best_dom = feasible_dom.iloc[-1]
        logger.info("\n--- Best Dominating Fleet (lower cost AND higher safety) ---")
        logger.info(f"  Safety: {best_dom['avg_safety']:.2f} (greedy was {greedy_safety:.2f}), Cost: ${best_dom['total_cost']:,.2f} (greedy was ${greedy_cost:,.2f})")
        logger.info("  [YES] MILP can dominate greedy on both cost AND safety!")
    else:
        logger.info("\n--- No dominating fleet found (greedy is Pareto-efficient) ---")

    # Claim check
    logger.info("\n--- Other Team Claim Check ---")
    logger.info("  Claim: fleet=22, safety>=4.0, cost<=$20.3M")
    logger.info("  Exact claim feasible: %s", claim["exact_claim_feasible"])
    if claim.get("best_cost_at_target"):
        logger.info(f"  Best cost at safety>=4.0, fleet=22: ${claim['best_cost_at_target']:,.2f}")
        logger.info(f"  Gap to claim: ${claim['gap_to_claim']:,.2f} ({claim['gap_pct']:.1f}%)")
    if claim.get("min_cost_any_size"):
        logger.info(f"  Absolute min cost at safety>=4.0 (any fleet size): ${claim['min_cost_any_size']:,.2f} (fleet={claim['fleet_size_at_min_cost']})")

    logger.info("\n--- Pareto Frontier ---")
    feasible_pareto = pareto_all[pareto_all["status"] == "feasible"]
    for _, row in feasible_pareto.iterrows():
        logger.info(f"  [{row['fleet_constraint']}] safety>={row['min_safety_target']:.1f} -> cost=${row['total_cost']:,.2f}, fleet={row['fleet_size']}, DWT={row['total_dwt']:,.0f}")

    logger.info("\nTotal experiment runtime: %.1f seconds.", total_time)

    # ── Save claim check results ──
    claim_df = pd.DataFrame([claim])
    claim_df.to_csv(os.path.join(exp_dir, "claim_check.csv"), index=False)

    # ── Return everything for report generation ──
    return {
        "greedy_val": greedy_val,
        "milp_free": milp_free,
        "milp_22": milp_22,
        "pareto_all": pareto_all,
        "domination_df": dom_df,
        "claim": claim,
        "fleet_size_df": fs_df,
        "total_time": total_time,
        "res_s4": res_s4,
        "res_s4_22": res_s4_22,
    }


if __name__ == "__main__":
    results = main()
