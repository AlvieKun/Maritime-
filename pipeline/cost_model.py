"""
cost_model.py — Full cost decomposition for each vessel.

Cost components (per Methodology Steps 6a–6f):
    1. Fuel cost       = Total fuel × Cost-per-tonne(main_engine_fuel_type)
    2. Carbon cost     = CO₂-eq × carbon price
    3. Ownership cost  = amortised monthly CAPEX (CRF-based)
    4. Total monthly   = fuel + carbon + ownership
    5. Risk premium    = total monthly × safety adjustment rate
    6. Adjusted cost   = total monthly + risk premium

Each cost component is retained separately for trade-off analysis.
Derived metrics: cost_per_dwt, carbon_cost_share, risk_premium_pct.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import (
    DEPRECIATION_RATE, SHIP_LIFE_YEARS, SALVAGE_FRACTION,
    DWT_COST_BRACKETS_FALLBACK, FUEL_TYPE_COST_MULTIPLIER_FALLBACK,
    SAFETY_ADJUSTMENT_FALLBACK, CARBON_PRICE_USD_PER_TONNE_FALLBACK,
)

logger = logging.getLogger(__name__)


# ───────────────── FUEL COST ───────────────────────────────────────

def compute_fuel_cost(vessel_df: pd.DataFrame, fuel_cost_table: pd.DataFrame) -> pd.DataFrame:
    """
    Per methodology: Cost per tonne = Cost_per_GJ × LCV (of main_engine_fuel_type).
    LCV in MJ/kg is numerically equal to GJ/tonne.
    Total fuel cost = total fuel consumed (all machineries) × cost per tonne.

    NOTE: The methodology prices ALL machinery fuel at the main-engine fuel type rate.
    This is a stated simplification — we follow the rules exactly.
    """
    # Build a lookup: fuel_type → cost per tonne (USD)
    cost_per_tonne = (fuel_cost_table["cost_per_gj"] * fuel_cost_table["lcv"]).to_dict()

    vessel_df["fuel_cost_per_tonne"] = vessel_df["main_engine_fuel_type"].map(cost_per_tonne)
    vessel_df["fuel_cost"] = vessel_df["fuel_total"] * vessel_df["fuel_cost_per_tonne"]

    unmapped = vessel_df["fuel_cost_per_tonne"].isna().sum()
    if unmapped > 0:
        logger.warning("%d vessels have unmapped fuel cost. Check fuel type names.", unmapped)

    logger.info("Total fuel cost across fleet: $%.2f M.", vessel_df["fuel_cost"].sum() / 1e6)
    return vessel_df


# ────────────────── CARBON COST ────────────────────────────────────

def compute_carbon_cost(vessel_df: pd.DataFrame,
                        carbon_price: float = CARBON_PRICE_USD_PER_TONNE_FALLBACK) -> pd.DataFrame:
    """
    Carbon cost = CO2-eq x carbon price per tonne.
    Provenance: carbon_price is passed in at runtime from Excel "Cost of Carbon".
    The default CARBON_PRICE_USD_PER_TONNE_FALLBACK is used only if Excel load failed.
    """
    vessel_df["carbon_cost"] = vessel_df["co2eq_total"] * carbon_price

    logger.info("Total carbon cost: $%.2f M at $%.0f/tCO2.",
                vessel_df["carbon_cost"].sum() / 1e6, carbon_price)
    return vessel_df


# ──────────────── SHIP OWNERSHIP COST ──────────────────────────────

def _get_base_ship_cost(dwt: float, brackets: list = DWT_COST_BRACKETS_FALLBACK) -> float:
    """
    Look up base cost (million USD) for a Distillate-fuel ship by DWT.
    Provenance: brackets list is from Excel "Cost of ship" sheet (primary).
    Fallback DWT_COST_BRACKETS_FALLBACK used only if Excel load failed.
    Brackets: (lower_exclusive, upper_inclusive].
    """
    for low, high, base_cost in brackets:
        if low < dwt <= high:
            return base_cost
    # Fallback — should not happen with well-formed data
    logger.warning("DWT %.0f does not fit any bracket; using last bracket.", dwt)
    return brackets[-1][2]


def _capital_recovery_factor(r: float = DEPRECIATION_RATE,
                              n: int = SHIP_LIFE_YEARS) -> float:
    """CRF = r(1+r)^N / ((1+r)^N - 1)."""
    return (r * (1 + r) ** n) / (((1 + r) ** n) - 1)


def compute_ownership_cost(vessel_df: pd.DataFrame,
                           ship_cost_info: dict) -> pd.DataFrame:
    """
    Per methodology Step 6c:
        Ship cost (P) = base_cost(DWT) × M(fuel_type)   (million USD → USD)
        Salvage (S)   = 10% × P
        CRF           = r(1+r)^N / ((1+r)^N - 1)
        Annual cost   = (P - S) × CRF + r × S
        Monthly cost  = Annual / 12
    """
    crf = _capital_recovery_factor()
    r = DEPRECIATION_RATE

    brackets    = ship_cost_info["base_costs"]
    multipliers = ship_cost_info["multipliers"]

    purchase_prices = []
    for _, row in vessel_df.iterrows():
        base_m = _get_base_ship_cost(row["dwt"], brackets)
        fuel_type = row["main_engine_fuel_type"]
        m = multipliers.get(fuel_type, 1.0)
        if fuel_type not in multipliers:
            logger.warning("No cost multiplier for fuel type '%s'; using 1.0.", fuel_type)
        purchase_usd = base_m * m * 1e6  # Convert million USD → USD
        purchase_prices.append(purchase_usd)

    vessel_df["ship_purchase_price"] = purchase_prices
    vessel_df["ship_salvage_value"]  = SALVAGE_FRACTION * vessel_df["ship_purchase_price"]

    # Amortised annual ownership cost
    vessel_df["annual_ownership_cost"] = (
        (vessel_df["ship_purchase_price"] - vessel_df["ship_salvage_value"]) * crf
        + r * vessel_df["ship_salvage_value"]
    )
    # Monthly cost (per problem statement: cargo scope is 1 month)
    vessel_df["monthly_ownership_cost"] = vessel_df["annual_ownership_cost"] / 12.0

    logger.info(
        "CRF = %.6f. Ship ownership costs: mean $%.2f M/month.",
        crf, vessel_df["monthly_ownership_cost"].mean() / 1e6,
    )
    return vessel_df


# ──────────────── TOTAL + RISK PREMIUM ─────────────────────────────

def compute_total_and_risk(vessel_df: pd.DataFrame,
                           safety_adj: dict = None) -> pd.DataFrame:
    """
    Step 6d: Total monthly cost = fuel_cost + carbon_cost + monthly_ownership_cost
    Step 6e: Risk premium = total_monthly x adjustment_rate(safety_score)
    Step 6f: Adjusted cost = total_monthly + risk_premium
    Provenance: safety_adj is from Excel "Safety score adjustment" (primary).
    Falls back to SAFETY_ADJUSTMENT_FALLBACK only if Excel load failed.
    """
    if safety_adj is None:
        logger.warning("FALLBACK: safety_adj not provided; using config fallback rates.")
        safety_adj = SAFETY_ADJUSTMENT_FALLBACK

    vessel_df["total_monthly_cost"] = (
        vessel_df["fuel_cost"]
        + vessel_df["carbon_cost"]
        + vessel_df["monthly_ownership_cost"]
    )

    vessel_df["safety_adj_rate"] = vessel_df["safety_score"].map(safety_adj)
    vessel_df["risk_premium"] = vessel_df["total_monthly_cost"] * vessel_df["safety_adj_rate"]
    vessel_df["adjusted_cost"] = vessel_df["total_monthly_cost"] + vessel_df["risk_premium"]

    logger.info(
        "Adjusted costs: min=$%.2f M, max=$%.2f M, total=$%.2f M.",
        vessel_df["adjusted_cost"].min() / 1e6,
        vessel_df["adjusted_cost"].max() / 1e6,
        vessel_df["adjusted_cost"].sum() / 1e6,
    )
    return vessel_df


# ────────────────── DERIVED COST METRICS ───────────────────────────

def compute_cost_metrics(vessel_df: pd.DataFrame) -> pd.DataFrame:
    """
    Trade-off analysis metrics:
        - cost_per_dwt:      How much each tonne of capacity costs
        - carbon_cost_share: What fraction of total cost is carbon
        - risk_premium_pct:  Magnitude of risk adjustment
        - marginal_value:    DWT delivered per dollar of adjusted cost
    """
    vessel_df["cost_per_dwt"] = vessel_df["adjusted_cost"] / vessel_df["dwt"]
    vessel_df["carbon_cost_share"] = (
        vessel_df["carbon_cost"] / vessel_df["total_monthly_cost"].replace(0, np.nan)
    )
    vessel_df["risk_premium_pct"] = (
        vessel_df["risk_premium"].abs() / vessel_df["total_monthly_cost"].replace(0, np.nan)
    )
    vessel_df["marginal_value"] = vessel_df["dwt"] / vessel_df["adjusted_cost"].replace(0, np.nan)

    return vessel_df


# ────────────────── FULL COST PIPELINE ─────────────────────────────

def run_cost_pipeline(vessel_df: pd.DataFrame,
                      fuel_cost_table: pd.DataFrame,
                      carbon_price: float,
                      ship_cost_info: dict,
                      safety_adj: dict) -> pd.DataFrame:
    """
    Complete cost decomposition:
        1. Fuel cost
        2. Carbon cost
        3. Ownership cost
        4. Total + risk premium
        5. Derived metrics
    Returns enriched vessel DataFrame.
    """
    vessel_df = compute_fuel_cost(vessel_df, fuel_cost_table)
    vessel_df = compute_carbon_cost(vessel_df, carbon_price)
    vessel_df = compute_ownership_cost(vessel_df, ship_cost_info)
    vessel_df = compute_total_and_risk(vessel_df, safety_adj)
    vessel_df = compute_cost_metrics(vessel_df)
    return vessel_df
