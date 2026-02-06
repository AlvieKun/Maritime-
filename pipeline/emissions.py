"""
emissions.py — LLAF lookup, per-machinery GHG emissions, and CO₂-equivalent aggregation.

Methodology Steps 5a–5b:
    1. Convert LF to %LF (× 100), round to nearest integer.
       If %LF > 20 → LLAF = 1 for all gases.
       If %LF < 2 and mode is transit/maneuver → default to 2%.
    2. For each machinery and each gas:
           Emission_pqr = LLAF_pqr × Cf_pqr × fuel_consumption_xy
    3. Total per gas = sum over machineries.
    4. CO₂eq = Σ(GWP_pqr × Total_Emission_pqr)
       GWP: CO₂=1, N₂O=265, CH₄=28.

Only transit and maneuver records are in scope.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import GWP, LLAF_THRESHOLD_PCT, LLAF_MIN_PCT

logger = logging.getLogger(__name__)

GASES = ["CO2", "N2O", "CH4"]


# ─────────────────── LLAF LOOKUP ───────────────────────────────────

def lookup_llaf(load_factor: pd.Series, in_scope: pd.Series,
                llaf_table: pd.DataFrame) -> pd.DataFrame:
    """
    Return a DataFrame with columns LLAF_CO2, LLAF_N2O, LLAF_CH4
    aligned with the input index.

    Steps:
        1. %LF = load_factor × 100
        2. Round %LF to nearest integer (standard arithmetic rounding)
        3. Floor at 2% for in-scope records
        4. If %LF > 20 → LLAF = 1.0
        5. Else → look up from LLAF table
    """
    pct_lf = (load_factor * 100).round(0).astype(int)

    # Floor at 2% for in-scope records (transit + maneuver)
    low_mask = in_scope & (pct_lf < LLAF_MIN_PCT)
    n_low = low_mask.sum()
    if n_low > 0:
        logger.info("LLAF: floored %d records from <%d%% to %d%%.", n_low, LLAF_MIN_PCT, LLAF_MIN_PCT)
    pct_lf = pct_lf.where(~low_mask, LLAF_MIN_PCT)

    # Build output
    result = pd.DataFrame(index=load_factor.index)
    for gas in GASES:
        col = f"LLAF_{gas}"
        # Default: LLAF = 1.0 (applies when %LF > 20 or out of scope)
        result[col] = 1.0
        # For in-scope records with %LF <= 20, look up the table
        needs_lookup = in_scope & (pct_lf <= LLAF_THRESHOLD_PCT)
        if needs_lookup.any():
            lookup_vals = pct_lf[needs_lookup].map(llaf_table[gas])
            result.loc[needs_lookup, col] = lookup_vals.values

    return result


# ──────────────── PER-RECORD EMISSIONS ─────────────────────────────

def compute_record_emissions(df: pd.DataFrame, cf_table: pd.DataFrame,
                              llaf_table: pd.DataFrame) -> pd.DataFrame:
    """
    For each in-scope AIS record, compute emissions per machinery per gas.
    Emission_pqr_xy = LLAF_pqr × Cf_pqr(fuel_xy) × fuel_xy

    Then sum across machineries per gas and compute CO₂-equivalent.
    """
    # Step 1: Get LLAF for each record
    llaf_df = lookup_llaf(df["load_factor"], df["in_scope"], llaf_table)
    for col in llaf_df.columns:
        df[col] = llaf_df[col].values

    # Step 2: Map Cf values for each machinery's fuel type
    for gas in GASES:
        cf_col = f"Cf_{gas}"
        for mach, fuel_col in [("me", "main_engine_fuel_type"),
                                ("ae", "aux_engine_fuel_type"),
                                ("blr", "boil_engine_fuel_type")]:
            cf_key = cf_col  # Column name in cf_table
            df[f"Cf_{gas}_{mach}"] = df[fuel_col].map(cf_table[cf_key])

    # Step 3: Compute emissions per machinery per gas
    for gas in GASES:
        for mach in ["me", "ae", "blr"]:
            emis_col = f"emis_{gas}_{mach}"
            df[emis_col] = (
                df[f"LLAF_{gas}"]
                * df[f"Cf_{gas}_{mach}"]
                * df[f"fuel_{mach}"]
            )
            # Zero for out-of-scope records (should already be 0 via fuel, but be safe)
            df.loc[~df["in_scope"], emis_col] = 0.0

    # Step 4: Total emissions per gas = sum across machineries
    for gas in GASES:
        df[f"emis_{gas}_total"] = (
            df[f"emis_{gas}_me"] + df[f"emis_{gas}_ae"] + df[f"emis_{gas}_blr"]
        )

    # Step 5: CO₂-equivalent per record
    df["co2eq"] = sum(GWP[gas] * df[f"emis_{gas}_total"] for gas in GASES)

    total_co2eq = df["co2eq"].sum()
    logger.info("Total CO2-eq across all records: %.2f tonnes.", total_co2eq)
    return df


# ─────────────── PER-VESSEL EMISSIONS AGGREGATION ──────────────────

def aggregate_vessel_emissions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate emissions to vessel level.
    Returns DataFrame with vessel_id and:
        - emis_CO2, emis_N2O, emis_CH4 (total per gas across all machineries)
        - co2eq_total
        - co2eq_per_fuel_tonne  (intensity metric)
        - co2eq_per_dwt         (intensity metric)
    """
    agg_cols = {f"emis_{gas}_total": "sum" for gas in GASES}
    agg_cols["co2eq"] = "sum"
    agg_cols["fuel_total"] = "sum"
    agg_cols["dwt"] = "first"

    vessel_emis = df.groupby("vessel_id").agg(agg_cols).reset_index()
    vessel_emis.rename(columns={
        "co2eq": "co2eq_total",
        "fuel_total": "fuel_total_check",
    }, inplace=True)

    # Intensity metrics
    vessel_emis["co2eq_per_fuel_tonne"] = (
        vessel_emis["co2eq_total"] / vessel_emis["fuel_total_check"].replace(0, np.nan)
    )
    vessel_emis["co2eq_per_dwt"] = vessel_emis["co2eq_total"] / vessel_emis["dwt"]

    logger.info(
        "Vessel emissions aggregated: %d vessels, total CO2-eq: %.1f t.",
        len(vessel_emis), vessel_emis["co2eq_total"].sum(),
    )
    return vessel_emis


def run_emissions_pipeline(df: pd.DataFrame, cf_table: pd.DataFrame,
                            llaf_table: pd.DataFrame) -> tuple:
    """
    Full emissions pipeline:
        1. Compute per-record emissions (with LLAF)
        2. Aggregate to vessel level
    Returns (enriched_ais_df, vessel_emissions_df).
    """
    df = compute_record_emissions(df, cf_table, llaf_table)
    vessel_emis = aggregate_vessel_emissions(df)
    return df, vessel_emis
