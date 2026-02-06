"""
main.py — Pipeline orchestrator for Maritime Hackathon 2026.

Executes the full analysis pipeline in sequence:
    1. Data ingestion & validation
    2. AIS behaviour modelling
    3. Engine load & fuel consumption
    4. Emissions accounting
    5. Cost decomposition
    6. Fleet selection
    7. Visualisation & output

Designed so that sensitivity analysis (e.g., changing MIN_SAFETY_SCORE
or CARBON_PRICE) requires edits in config.py only, then re-run.

Usage:
    python main.py                        # Default run
    python main.py --sensitivity          # Run with safety=4 comparison
"""

import os
import sys
import argparse
import logging
import pandas as pd

# ─── Ensure project root is on path ───
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pipeline.config import (
    OUTPUT_DIR, MONTHLY_CARGO_REQUIREMENT, MIN_SAFETY_SCORE,
    CARBON_PRICE_USD_PER_TONNE_FALLBACK, TEAM_NAME, CATEGORY, REPORT_FILE_NAME,
)
from pipeline.data_ingestion import load_all_data
from pipeline.ais_behavior import run_ais_pipeline, aggregate_vessel_hours
from pipeline.engine_fuel import run_engine_fuel_pipeline
from pipeline.emissions import run_emissions_pipeline
from pipeline.cost_model import run_cost_pipeline
from pipeline.fleet_selection import select_fleet, format_submission
from pipeline.visualization import generate_all_plots, plot_sensitivity_comparison


def setup_logging():
    """Configure logging to console and file."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log_path = os.path.join(OUTPUT_DIR, "pipeline.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, mode="w"),
        ],
    )


def run_pipeline(min_safety: float = MIN_SAFETY_SCORE,
                 carbon_price: float = None,
                 label: str = "base") -> dict:
    """
    Execute the full pipeline end-to-end and return summary results.

    Parameters are externalised so the same function supports sensitivity analysis.
    carbon_price=None means "use the Excel-sourced value" (primary source).
    An explicit carbon_price overrides the Excel value (for sensitivity analysis).
    """
    logger = logging.getLogger("pipeline")
    logger.info("=" * 70)
    logger.info("PIPELINE START -- scenario: %s (safety>=%.1f, carbon=%s)",
                label, min_safety,
                "from Excel" if carbon_price is None else "$%.0f/t" % carbon_price)
    logger.info("=" * 70)

    # ──── 1. DATA INGESTION ────
    logger.info("Stage 1: Data ingestion & validation")
    data = load_all_data()
    ais_df        = data["movements"]
    cf_table      = data["cf_table"]
    reference_lcv = data["reference_lcv"]   # Provenance: Cf table "Distillate fuel" LCV
    fuel_cost_tbl = data["fuel_cost_table"]
    ship_cost_info = data["ship_cost_info"]
    safety_adj    = data["safety_adjustment"]
    llaf_table    = data["llaf_table"]

    # Provenance: Carbon price primary source is Excel "Cost of Carbon" sheet.
    # CLI override (carbon_price param) takes precedence only when explicitly provided.
    excel_carbon_price = data["carbon_cost"]  # Always from Excel
    if carbon_price is not None:
        carbon_cost_v = carbon_price
        logger.info("Carbon price OVERRIDE: $%.1f/t (Excel value was $%.1f/t).",
                    carbon_cost_v, excel_carbon_price)
    else:
        carbon_cost_v = excel_carbon_price
        logger.info("Carbon price: $%.1f/t (from Excel 'Cost of Carbon' sheet).",
                    carbon_cost_v)

    # ──── 2. AIS BEHAVIOUR MODELLING ────
    logger.info("Stage 2: AIS behaviour modelling")
    ais_df = run_ais_pipeline(ais_df)
    vessel_hours = aggregate_vessel_hours(ais_df)

    # ──── 3. ENGINE LOAD & FUEL CONSUMPTION ────
    logger.info("Stage 3: Engine load & fuel consumption")
    ais_df, vessel_fuel = run_engine_fuel_pipeline(ais_df, cf_table, reference_lcv)

    # ──── 4. EMISSIONS ACCOUNTING ────
    logger.info("Stage 4: Emissions accounting")
    ais_df, vessel_emis = run_emissions_pipeline(ais_df, cf_table, llaf_table)

    # ──── 5. COST DECOMPOSITION ────
    logger.info("Stage 5: Cost decomposition")
    # Merge vessel-level fuel + emissions before cost calculation
    vessel_df = vessel_fuel.merge(
        vessel_emis[["vessel_id", "emis_CO2_total", "emis_N2O_total", "emis_CH4_total",
                      "co2eq_total", "co2eq_per_fuel_tonne", "co2eq_per_dwt"]],
        on="vessel_id",
        how="left",
    )
    vessel_df = run_cost_pipeline(
        vessel_df, fuel_cost_tbl, carbon_cost_v, ship_cost_info, safety_adj,
    )

    # ──── 6. FLEET SELECTION ────
    logger.info("Stage 6: Fleet selection (min_safety=%.1f)", min_safety)
    fleet_df, selection_log, report = select_fleet(
        vessel_df,
        cargo_requirement=MONTHLY_CARGO_REQUIREMENT,
        min_safety=min_safety,
    )

    # ──── 7. OUTPUT ────
    scenario_dir = os.path.join(OUTPUT_DIR, label)
    os.makedirs(scenario_dir, exist_ok=True)

    # Save detailed vessel-level results
    vessel_df.to_csv(os.path.join(scenario_dir, "all_vessels_detailed.csv"), index=False)
    fleet_df.to_csv(os.path.join(scenario_dir, "selected_fleet.csv"), index=False)

    # Save selection log
    log_df = pd.DataFrame(selection_log)
    log_df.to_csv(os.path.join(scenario_dir, "selection_log.csv"), index=False)

    # Save vessel hours summary
    vessel_hours.to_csv(os.path.join(scenario_dir, "vessel_hours_by_mode.csv"), index=False)

    # Submission file
    submission = format_submission(
        fleet_df, report, TEAM_NAME, CATEGORY, REPORT_FILE_NAME,
        sensitivity="Yes",
    )
    submission.to_csv(os.path.join(scenario_dir, f"{TEAM_NAME}_submission.csv"), index=False)

    # Generate plots
    logger.info("Stage 7: Generating visualizations")
    generate_all_plots(vessel_df, fleet_df, scenario_dir)

    # Print summary
    logger.info("=" * 70)
    logger.info("RESULTS SUMMARY — %s", label)
    logger.info("-" * 70)
    logger.info("  Fleet size:                   %d ships", report["fleet_size"])
    logger.info("  Total DWT:                    {:,.0f} t".format(report["total_dwt"]))
    logger.info("  Cargo requirement:            {:,.0f} t".format(report["cargo_requirement"]))
    logger.info("  DWT constraint met:           %s", report["dwt_constraint_met"])
    logger.info("  Total adjusted cost:          ${:,.2f}".format(report["total_cost_usd"]))
    logger.info("  Average safety score:         %.2f", report["avg_safety_score"])
    logger.info("  Safety constraint met:        %s", report["safety_constraint_met"])
    logger.info("  Unique ME fuel types:         %d / %d required",
                report["n_unique_fuel_types"], len(vessel_df["main_engine_fuel_type"].unique()))
    logger.info("  All fuel types covered:       %s", report["all_fuel_types_covered"])
    logger.info("  Total CO2-eq:                  {:,.2f} t".format(report["total_co2eq"]))
    logger.info("  Total fuel consumption:       {:,.2f} t".format(report["total_fuel_consumption"]))
    logger.info("=" * 70)

    return {
        "scenario_name": label,
        "min_safety": min_safety,
        "carbon_price": carbon_cost_v,  # Effective value used (Excel or override)
        "total_cost": report["total_cost_usd"],
        "avg_safety": report["avg_safety_score"],
        "total_co2eq": report["total_co2eq"],
        "fleet_size": report["fleet_size"],
        "total_dwt": report["total_dwt"],
        "total_fuel": report["total_fuel_consumption"],
        "vessel_df": vessel_df,     # Carry forward for sensitivity plots
        "fleet_df": fleet_df,
        "report": report,
    }


def main():
    parser = argparse.ArgumentParser(description="Maritime Hackathon 2026 — Fleet Selection Pipeline")
    parser.add_argument("--sensitivity", action="store_true",
                        help="Run sensitivity analysis (safety score = 4)")
    parser.add_argument("--safety", type=float, default=None,
                        help="Override minimum safety score")
    parser.add_argument("--carbon-price", type=float, default=None,
                        help="Override carbon price (USD/tonne)")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("main")

    # ── BASE SCENARIO ──
    base_safety = args.safety if args.safety else MIN_SAFETY_SCORE
    # Carbon price: CLI override if provided, else None (= use Excel value)
    base_carbon = args.carbon_price if args.carbon_price else None

    base_result = run_pipeline(
        min_safety=base_safety,
        carbon_price=base_carbon,
        label="base",
    )
    # Capture the actual carbon price used (from Excel or CLI) for sensitivity scaling
    effective_carbon = base_result["carbon_price"]

    # ── SENSITIVITY ANALYSIS ──
    all_results = [base_result]

    if args.sensitivity:
        logger.info("\n" + "=" * 70)
        logger.info("SENSITIVITY ANALYSIS: Raising safety floor to 4.0")
        logger.info("=" * 70)

        sensitivity_result = run_pipeline(
            min_safety=4.0,
            carbon_price=base_carbon,
            label="sensitivity_safety4",
        )
        all_results.append(sensitivity_result)

        # Additional scenario: higher carbon price (2x the effective base carbon price)
        logger.info("\n" + "=" * 70)
        logger.info("SENSITIVITY ANALYSIS: Carbon price doubled to $%.0f/t",
                     effective_carbon * 2)
        logger.info("=" * 70)

        carbon_result = run_pipeline(
            min_safety=base_safety,
            carbon_price=effective_carbon * 2,
            label="sensitivity_carbon_2x",
        )
        all_results.append(carbon_result)

        # Generate comparison plot
        comparison = [{k: v for k, v in r.items()
                       if k not in ("vessel_df", "fleet_df", "report")}
                      for r in all_results]
        plot_sensitivity_comparison(comparison, OUTPUT_DIR)

    logger.info("\nPipeline complete. All outputs saved to '%s'.", OUTPUT_DIR)


if __name__ == "__main__":
    main()
