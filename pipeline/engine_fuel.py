"""
engine_fuel.py — Engine load factor calculation, SFC adjustment, and fuel consumption.

Formulas from Methodology Document:
    Maximum Speed:  MS = 1.066 × Vref
    Load Factor:    LF = (AS / MS)³   (rounded 2 d.p.; floor 0.02 for transit/maneuver)
    SFC Adjustment: sfc_adjusted_xy = sfc_xy × (42.7 / LCV_of_fuel_type_xy)
    Fuel (ME):      LF × MEP × sfc_adjusted_me × A / 1,000,000   (tonnes)
    Fuel (AE):      AEL × sfc_adjusted_ae × A / 1,000,000         (tonnes)
    Fuel (AB):      ABL × sfc_adjusted_blr × A / 1,000,000        (tonnes)

Only in-scope records (transit + maneuver) contribute to fuel consumption.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import (
    VREF_MULTIPLIER, MIN_LOAD_FACTOR, LF_DECIMAL_PLACES, REFERENCE_LCV_FALLBACK,
)

logger = logging.getLogger(__name__)


# ───────────────────── LOAD FACTOR ─────────────────────────────────

def compute_max_speed(df: pd.DataFrame) -> pd.DataFrame:
    """MS = 1.066 × Vref  (per methodology Step 3a)."""
    df["max_speed"] = VREF_MULTIPLIER * df["vref"]
    return df


def compute_load_factor(df: pd.DataFrame) -> pd.DataFrame:
    """
    LF = (actual_speed / max_speed)³, rounded to 2 d.p.
    If LF < 0.02 and mode is transit or maneuver → floor at 0.02.
    """
    df["load_factor_raw"] = (df["speed_knots"] / df["max_speed"]) ** 3

    # Standard rounding to 2 decimal places
    df["load_factor"] = df["load_factor_raw"].round(LF_DECIMAL_PLACES)

    # Apply floor for in-scope modes
    in_scope_and_low = df["in_scope"] & (df["load_factor"] < MIN_LOAD_FACTOR)
    n_floored = in_scope_and_low.sum()
    if n_floored > 0:
        logger.info(
            "Floored load factor to %.2f for %d in-scope records.",
            MIN_LOAD_FACTOR, n_floored,
        )
    df.loc[in_scope_and_low, "load_factor"] = MIN_LOAD_FACTOR

    return df


# ──────────────── SFC ADJUSTMENT ───────────────────────────────────

def compute_adjusted_sfc(df: pd.DataFrame, cf_table: pd.DataFrame,
                         reference_lcv: float = REFERENCE_LCV_FALLBACK) -> pd.DataFrame:
    """
    Adjust SFC from default (Distillate fuel) to actual fuel type:
        sfc_adjusted_xy = sfc_xy × (reference_lcv / LCV_of_fuel_xy)

    Provenance: reference_lcv is the Distillate fuel LCV from the Cf table
    (primary source), passed in at runtime. Fallback to config constant
    REFERENCE_LCV_FALLBACK (42.7) only if Cf table lookup failed upstream.

    Each machinery uses its own fuel type's LCV for the adjustment.
    WHY: The dataset's SFC values assume Distillate fuel. Converting via
    energy-content ratio gives the effective SFC for the actual fuel burned.
    """
    lcv_lookup = cf_table["lcv"].to_dict()

    # Map each machinery's fuel type to its LCV
    df["lcv_me"]  = df["main_engine_fuel_type"].map(lcv_lookup)
    df["lcv_ae"]  = df["aux_engine_fuel_type"].map(lcv_lookup)
    df["lcv_blr"] = df["boil_engine_fuel_type"].map(lcv_lookup)

    # Flag any unmapped fuel types
    for col in ["lcv_me", "lcv_ae", "lcv_blr"]:
        n_miss = df[col].isna().sum()
        if n_miss > 0:
            logger.warning("Could not map LCV for %d records in '%s'.", n_miss, col)

    # Compute adjusted SFC for each machinery (g/kWh → g/kWh, different fuel basis)
    # Provenance: reference_lcv from Cf table "Distillate fuel" row (Step 4a)
    df["sfc_adjusted_me"]  = df["sfc_me"]  * (reference_lcv / df["lcv_me"])
    df["sfc_adjusted_ae"]  = df["sfc_ae"]  * (reference_lcv / df["lcv_ae"])
    df["sfc_adjusted_blr"] = df["sfc_ab"]  * (reference_lcv / df["lcv_blr"])

    return df


# ──────────────── FUEL CONSUMPTION ─────────────────────────────────

def compute_fuel_consumption(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-record fuel consumption (tonnes) for each machinery.
    Only computed where in_scope == True (transit + maneuver).

    Main Engine:  LF × MEP × sfc_adjusted_me × A / 1e6
    Aux Engine:   AEL × sfc_adjusted_ae × A / 1e6
    Aux Boiler:   ABL × sfc_adjusted_blr × A / 1e6

    WHY divide by 1e6:
        MEP in kW, SFC in g/kWh, A in hours → grams; /1e6 → tonnes.
    """
    # Initialise to zero for all records
    df["fuel_me"]  = 0.0
    df["fuel_ae"]  = 0.0
    df["fuel_blr"] = 0.0

    scope = df["in_scope"]

    df.loc[scope, "fuel_me"] = (
        df.loc[scope, "load_factor"]
        * df.loc[scope, "mep"]
        * df.loc[scope, "sfc_adjusted_me"]
        * df.loc[scope, "activity_hours"]
        / 1e6
    )

    df.loc[scope, "fuel_ae"] = (
        df.loc[scope, "ael"]
        * df.loc[scope, "sfc_adjusted_ae"]
        * df.loc[scope, "activity_hours"]
        / 1e6
    )

    df.loc[scope, "fuel_blr"] = (
        df.loc[scope, "abl"]
        * df.loc[scope, "sfc_adjusted_blr"]
        * df.loc[scope, "activity_hours"]
        / 1e6
    )

    df["fuel_total"] = df["fuel_me"] + df["fuel_ae"] + df["fuel_blr"]

    logger.info(
        "Fuel consumption computed: ME=%.1f t, AE=%.1f t, BLR=%.1f t, Total=%.1f t.",
        df["fuel_me"].sum(), df["fuel_ae"].sum(),
        df["fuel_blr"].sum(), df["fuel_total"].sum(),
    )
    return df


# ──────────────── PER-VESSEL AGGREGATION ───────────────────────────

def aggregate_vessel_fuel(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate fuel consumption and diagnostics per vessel.
    Returns one row per vessel with:
        - Total fuel by machinery type
        - Average load factor (in-scope only)
        - Share of fuel by machinery type
        - Total in-scope hours
    """
    # Static vessel attributes (take first per vessel)
    static_cols = [
        "vessel_id", "vessel_type_new", "dwt", "safety_score",
        "main_engine_fuel_type", "aux_engine_fuel_type", "boil_engine_fuel_type",
        "engine_type", "mep", "vref", "sfc_me", "sfc_ae", "sfc_ab", "ael", "abl",
    ]
    vessel_static = df.groupby("vessel_id")[static_cols[1:]].first().reset_index()

    # Fuel totals
    fuel_agg = df.groupby("vessel_id").agg(
        fuel_me_total=("fuel_me", "sum"),
        fuel_ae_total=("fuel_ae", "sum"),
        fuel_blr_total=("fuel_blr", "sum"),
        fuel_total=("fuel_total", "sum"),
    ).reset_index()

    # Average load factor (in-scope records only, weighted by activity hours)
    scope_df = df[df["in_scope"]].copy()
    if len(scope_df) > 0:
        lf_agg = (
            scope_df.groupby("vessel_id")
            .apply(
                lambda g: np.average(g["load_factor"], weights=g["activity_hours"])
                if g["activity_hours"].sum() > 0 else 0.0,
                include_groups=False,
            )
            .rename("avg_load_factor")
            .reset_index()
        )
        hours_agg = (
            scope_df.groupby("vessel_id")["activity_hours"]
            .sum()
            .rename("in_scope_hours")
            .reset_index()
        )
    else:
        lf_agg = pd.DataFrame(columns=["vessel_id", "avg_load_factor"])
        hours_agg = pd.DataFrame(columns=["vessel_id", "in_scope_hours"])

    # Merge everything
    vessel_df = vessel_static.merge(fuel_agg, on="vessel_id")
    vessel_df = vessel_df.merge(lf_agg, on="vessel_id", how="left")
    vessel_df = vessel_df.merge(hours_agg, on="vessel_id", how="left")
    vessel_df["avg_load_factor"] = vessel_df["avg_load_factor"].fillna(0.0)
    vessel_df["in_scope_hours"] = vessel_df["in_scope_hours"].fillna(0.0)

    # Diagnostic: fuel share by machinery type
    vessel_df["fuel_share_me"]  = vessel_df["fuel_me_total"]  / vessel_df["fuel_total"].replace(0, np.nan)
    vessel_df["fuel_share_ae"]  = vessel_df["fuel_ae_total"]  / vessel_df["fuel_total"].replace(0, np.nan)
    vessel_df["fuel_share_blr"] = vessel_df["fuel_blr_total"] / vessel_df["fuel_total"].replace(0, np.nan)

    logger.info("Aggregated fuel for %d vessels. Total fleet fuel: %.1f tonnes.",
                len(vessel_df), vessel_df["fuel_total"].sum())
    return vessel_df


def run_engine_fuel_pipeline(df: pd.DataFrame, cf_table: pd.DataFrame,
                             reference_lcv: float = REFERENCE_LCV_FALLBACK) -> tuple:
    """
    Full engine/fuel pipeline:
        1. Compute max speed
        2. Compute load factor
        3. Adjust SFC per fuel type (using reference_lcv from Cf table)
        4. Compute fuel consumption per record
        5. Aggregate per vessel
    Returns (enriched_ais_df, vessel_fuel_df).
    """
    df = compute_max_speed(df)
    df = compute_load_factor(df)
    df = compute_adjusted_sfc(df, cf_table, reference_lcv)
    df = compute_fuel_consumption(df)

    vessel_fuel = aggregate_vessel_fuel(df)
    return df, vessel_fuel
