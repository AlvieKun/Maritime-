"""
data_ingestion.py — Load, validate, and normalise all input data.

Every downstream module receives clean, typed DataFrames from here.
Data-quality issues are logged explicitly — never silently dropped.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import (
    VESSEL_MOVEMENTS_PATH, CALC_FACTORS_PATH, LLAF_TABLE_PATH,
    FUEL_NAME_MAP, REFERENCE_LCV_FALLBACK, CARBON_PRICE_USD_PER_TONNE_FALLBACK,
)

logger = logging.getLogger(__name__)


# ────────────────────── VESSEL MOVEMENTS ───────────────────────────

def load_vessel_movements(path: str = VESSEL_MOVEMENTS_PATH) -> pd.DataFrame:
    """Load AIS vessel movement CSV, parse timestamps, drop junk columns."""

    df = pd.read_csv(path)

    # Drop unnamed/empty trailing columns (artefact of CSV export)
    junk_cols = [c for c in df.columns if c.startswith("Unnamed")]
    if junk_cols:
        logger.info("Dropping %d trailing unnamed columns.", len(junk_cols))
        df.drop(columns=junk_cols, inplace=True)

    # Parse timestamp to datetime (UTC)
    # Some records have fractional seconds (e.g., "2025/03/30 03:08:28.076+00")
    # so we use format='mixed' to handle both formats
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True)

    # Ensure correct dtypes
    numeric_cols = [
        "speed_knots", "dwt", "mep", "vref",
        "sfc_me", "sfc_ae", "sfc_ab", "ael", "abl",
        "latitude", "longitude", "safety_score",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Normalise fuel-type names so they match reference tables
    for col in ["main_engine_fuel_type", "aux_engine_fuel_type", "boil_engine_fuel_type"]:
        df[col] = df[col].map(FUEL_NAME_MAP).fillna(df[col])

    # Sort by vessel then time — essential for duration computation
    df.sort_values(["vessel_id", "timestamp"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    _validate_vessel_movements(df)
    return df


def _validate_vessel_movements(df: pd.DataFrame) -> None:
    """Log warnings for missing data or unexpected values."""
    required = [
        "vessel_id", "timestamp", "speed_knots", "in_anchorage",
        "in_port_boundary", "safety_score", "dwt",
        "main_engine_fuel_type", "aux_engine_fuel_type", "boil_engine_fuel_type",
        "engine_type", "mep", "vref", "sfc_me", "sfc_ae", "sfc_ab", "ael", "abl",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Null checks on critical numeric fields
    for col in ["speed_knots", "dwt", "mep", "vref", "sfc_me", "sfc_ae", "sfc_ab"]:
        n_null = df[col].isna().sum()
        if n_null > 0:
            logger.warning("Column '%s' has %d null values.", col, n_null)

    # Speed sanity
    neg_speed = (df["speed_knots"] < 0).sum()
    if neg_speed:
        logger.warning("%d records with negative speed — setting to 0.", neg_speed)
        df.loc[df["speed_knots"] < 0, "speed_knots"] = 0.0

    n_vessels = df["vessel_id"].nunique()
    logger.info(
        "Loaded %d AIS records for %d unique vessels. "
        "Time range: %s to %s.",
        len(df), n_vessels,
        df["timestamp"].min().isoformat(),
        df["timestamp"].max().isoformat(),
    )


# ────────────────── CALCULATION FACTORS (EXCEL) ───────────────────

def load_cf_table(path: str = CALC_FACTORS_PATH) -> tuple:
    """Emission factors (Cf) and LCV per fuel type.

    Returns:
        (cf_dataframe, reference_lcv) where reference_lcv is the Distillate fuel
        LCV read from the Cf table (primary source for Step 4a SFC adjustment).
    """
    df = pd.read_excel(path, sheet_name="Cf")
    df.dropna(subset=["Fuel Type"], inplace=True)
    df.rename(columns={"Fuel Type": "fuel_type", "LCV (MJ/kg)": "lcv"}, inplace=True)
    df["fuel_type"] = df["fuel_type"].map(FUEL_NAME_MAP).fillna(df["fuel_type"])
    df.set_index("fuel_type", inplace=True)

    # Provenance: Reference LCV sourced from Cf table, "Distillate fuel" row.
    # Per Methodology Step 4a: sfc_adjusted = sfc × (42.7 / LCV).
    # The value 42.7 is the Distillate fuel LCV; we read it dynamically.
    if "Distillate fuel" in df.index and pd.notna(df.loc["Distillate fuel", "lcv"]):
        reference_lcv = float(df.loc["Distillate fuel", "lcv"])
        logger.info("Reference LCV for SFC adjustment: %.1f MJ/kg (from Cf table, Distillate fuel).", reference_lcv)
    else:
        reference_lcv = REFERENCE_LCV_FALLBACK
        logger.warning(
            "FALLBACK: Distillate fuel LCV not found in Cf table. "
            "Using config fallback %.1f MJ/kg.", reference_lcv
        )

    logger.info("Cf table loaded: %d fuel types.", len(df))
    return df, reference_lcv


def load_fuel_cost_table(path: str = CALC_FACTORS_PATH) -> pd.DataFrame:
    """Fuel price (USD/GJ) and LCV per fuel type."""
    df = pd.read_excel(path, sheet_name="Fuel cost")
    df.dropna(subset=["Fuel Type"], inplace=True)
    df.rename(columns={
        "Fuel Type": "fuel_type",
        "Cost per GJ (USD)": "cost_per_gj",
        "LCV (MJ/kg)": "lcv",
    }, inplace=True)
    df["fuel_type"] = df["fuel_type"].map(FUEL_NAME_MAP).fillna(df["fuel_type"])
    df.set_index("fuel_type", inplace=True)
    logger.info("Fuel cost table loaded: %d fuel types.", len(df))
    return df


def load_carbon_cost(path: str = CALC_FACTORS_PATH) -> float:
    """Carbon cost USD/tonne CO2-eq.

    Provenance: Primary source is calculation_factors.xlsx -> "Cost of Carbon" sheet.
    Falls back to config CARBON_PRICE_USD_PER_TONNE_FALLBACK only if Excel load fails.
    """
    try:
        df = pd.read_excel(path, sheet_name="Cost of Carbon")
        cost = float(df["Carbon cost per ton (USD)"].dropna().iloc[0])
        logger.info("Carbon cost: %.1f USD/tonne (from Excel 'Cost of Carbon' sheet).", cost)
        return cost
    except Exception as e:
        logger.warning(
            "FALLBACK: Failed to load carbon cost from Excel (%s). "
            "Using config fallback $%.1f/tonne.", e, CARBON_PRICE_USD_PER_TONNE_FALLBACK
        )
        return CARBON_PRICE_USD_PER_TONNE_FALLBACK


def load_ship_cost_tables(path: str = CALC_FACTORS_PATH) -> dict:
    """
    Return dict with:
        "base_costs"  — list of (dwt_low, dwt_high, base_cost_million_usd)
        "multipliers" — dict[fuel_type, multiplier]
    Parsed directly from the 'Cost of ship' sheet.
    """
    df = pd.read_excel(path, sheet_name="Cost of ship", header=None)

    # Row 3 (0-indexed with header=None) has base costs for Distillate fuel
    # Columns 1..5 map to DWT brackets 10-40k, 40-55k, 55-80k, 80-120k, >120k
    base_row = df.iloc[3, 1:6].values.astype(float)
    brackets = [
        (10_000,  40_000, base_row[0]),
        (40_000,  55_000, base_row[1]),
        (55_000,  80_000, base_row[2]),
        (80_000, 120_000, base_row[3]),
        (120_000, float("inf"), base_row[4]),
    ]

    # Multiplier rows: rows 5..11, fuel name in col 0, multiplier in col 1
    multipliers = {}
    for i in range(5, 12):
        fuel = df.iloc[i, 0]
        if pd.notna(fuel):
            fuel_norm = FUEL_NAME_MAP.get(str(fuel).strip(), str(fuel).strip())
            m = float(df.iloc[i, 1])
            multipliers[fuel_norm] = m

    # Distillate fuel has implicit multiplier = 1.0
    multipliers.setdefault("Distillate fuel", 1.0)

    logger.info("Ship cost table loaded: %d brackets, %d fuel multipliers.", len(brackets), len(multipliers))
    return {"base_costs": brackets, "multipliers": multipliers}


def load_safety_adjustment(path: str = CALC_FACTORS_PATH) -> dict:
    """Safety score → adjustment rate (decimal)."""
    df = pd.read_excel(path, sheet_name="Safety score adjustment")
    adj = {}
    for _, row in df.iterrows():
        score = row["Safety score"]
        rate = row["Adjustment rate (%)"]
        if pd.notna(score) and pd.notna(rate):
            try:
                adj[int(score)] = float(rate) / 100.0
            except (ValueError, TypeError):
                pass
    logger.info("Safety adjustment table: %s", adj)
    return adj


# ──────────────────── LLAF TABLE ───────────────────────────────────

def load_llaf_table(path: str = LLAF_TABLE_PATH) -> pd.DataFrame:
    """
    Low Load Adjustment Factors indexed by integer load percentage.
    Returns DataFrame with integer index (2..20) and columns CO2, N2O, CH4.
    """
    df = pd.read_csv(path)
    # Parse "Load" column: "2%" → 2
    df["load_pct"] = df["Load"].str.replace("%", "").astype(int)
    df.set_index("load_pct", inplace=True)
    # Keep only the three GHG columns we need
    df = df[["CO2", "N2O", "CH4"]].copy()
    logger.info("LLAF table loaded: load range %d%%–%d%%.", df.index.min(), df.index.max())
    return df


# ──────────────── CONVENIENCE: LOAD EVERYTHING ────────────────────

def load_all_data() -> dict:
    """
    Single entry point — returns a dict of all cleaned DataFrames / lookups:
        "movements"         — AIS DataFrame
        "cf_table"          — Emission factors
        "reference_lcv"     — float, Distillate fuel LCV from Cf table (Step 4a)
        "fuel_cost_table"   — Fuel prices
        "carbon_cost"       — float (from Excel, provenance-tracked)
        "ship_cost_info"    — dict with base_costs & multipliers
        "safety_adjustment" — dict score→rate
        "llaf_table"        — LLAF DataFrame
    """
    cf_table, reference_lcv = load_cf_table()
    return {
        "movements":         load_vessel_movements(),
        "cf_table":          cf_table,
        "reference_lcv":     reference_lcv,
        "fuel_cost_table":   load_fuel_cost_table(),
        "carbon_cost":       load_carbon_cost(),
        "ship_cost_info":    load_ship_cost_tables(),
        "safety_adjustment": load_safety_adjustment(),
        "llaf_table":        load_llaf_table(),
    }
