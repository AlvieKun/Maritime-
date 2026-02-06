"""
ais_behavior.py — Classify AIS records into operating modes and compute activity durations.

Operating modes are defined by the methodology document:
    Anchorage:  in_anchorage is not null  AND  speed_knots < 1
    Maneuver:   in_port_boundary is not null  AND  speed_knots > 1
    Transit:    in_port_boundary is null  AND  speed_knots >= 1
    Drifting:   everything else

WHY this matters:
    Only Transit and Maneuver contribute to fuel consumption and emissions.
    Anchorage and Drifting are excluded from scope per methodology rules.
"""

import pandas as pd
import numpy as np
import logging
from pipeline.config import (
    ANCHORAGE_SPEED_THRESHOLD,
    MANEUVER_SPEED_THRESHOLD,
    TRANSIT_SPEED_THRESHOLD,
)

logger = logging.getLogger(__name__)


def classify_operating_mode(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add column 'operating_mode' to the AIS DataFrame.
    Classification is applied in priority order to avoid ambiguity
    when a record could match multiple conditions.
    """
    conditions = [
        # 1. Anchorage — vessel is at anchorage AND nearly stationary
        (df["in_anchorage"].notna()) & (df["speed_knots"] < ANCHORAGE_SPEED_THRESHOLD),
        # 2. Maneuver — within port boundary AND moving
        (df["in_port_boundary"].notna()) & (df["speed_knots"] > MANEUVER_SPEED_THRESHOLD),
        # 3. Transit — outside port AND moving
        (df["in_port_boundary"].isna()) & (df["speed_knots"] >= TRANSIT_SPEED_THRESHOLD),
    ]
    choices = ["anchorage", "maneuver", "transit"]

    df["operating_mode"] = np.select(conditions, choices, default="drifting")

    # Log mode distribution
    counts = df["operating_mode"].value_counts()
    for mode, cnt in counts.items():
        logger.info("  Mode '%s': %d records (%.1f%%)", mode, cnt, 100 * cnt / len(df))

    return df


def compute_activity_hours(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the time delta (in hours) between consecutive timestamps
    for each vessel. The first record of each vessel has no predecessor
    so its activity_hours is set to 0.

    WHY hours (not seconds):
        Fuel consumption formulas use hours as the time unit.
    """
    df = df.sort_values(["vessel_id", "timestamp"]).copy()

    # Time difference in hours between consecutive rows per vessel
    df["activity_hours"] = (
        df.groupby("vessel_id")["timestamp"]
        .diff()
        .dt.total_seconds()
        / 3600.0
    )
    # First record per vessel — no predecessor, zero hours
    df["activity_hours"] = df["activity_hours"].fillna(0.0)

    # Sanity: flag unreasonably large gaps (> 48 h) — likely data gaps
    large_gaps = df["activity_hours"] > 48
    n_large = large_gaps.sum()
    if n_large > 0:
        logger.warning(
            "%d records have activity_hours > 48h (data gap). "
            "Retaining but flagging.", n_large
        )
        df["activity_gap_flag"] = large_gaps

    return df


def flag_in_scope(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add boolean 'in_scope' column.
    Only transit and maneuver records are in scope for fuel/emissions.
    """
    df["in_scope"] = df["operating_mode"].isin(["transit", "maneuver"])
    in_scope_pct = df["in_scope"].mean() * 100
    logger.info("%.1f%% of records are in scope (transit + maneuver).", in_scope_pct)
    return df


def aggregate_vessel_hours(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-vessel summary of total hours by operating mode.
    Returns a wide DataFrame indexed by vessel_id with columns:
        hours_anchorage, hours_maneuver, hours_transit, hours_drifting, hours_total
    """
    pivot = (
        df.groupby(["vessel_id", "operating_mode"])["activity_hours"]
        .sum()
        .unstack(fill_value=0.0)
    )
    # Ensure all expected columns exist
    for mode in ["anchorage", "maneuver", "transit", "drifting"]:
        if mode not in pivot.columns:
            pivot[mode] = 0.0

    pivot = pivot.rename(columns=lambda c: f"hours_{c}")
    pivot["hours_total"] = pivot.sum(axis=1)
    pivot = pivot.reset_index()

    logger.info(
        "Vessel hours aggregated: %d vessels, avg total %.1f h.",
        len(pivot), pivot["hours_total"].mean(),
    )
    return pivot


def run_ais_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full AIS behaviour pipeline:
        1. Classify operating modes
        2. Compute activity durations
        3. Flag in-scope records
    Returns enriched AIS DataFrame.
    """
    df = classify_operating_mode(df)
    df = compute_activity_hours(df)
    df = flag_in_scope(df)
    return df
