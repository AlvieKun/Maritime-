"""
visualization.py — Analytical plots that support fleet selection narratives.

Each plot is designed to answer a specific judge question:
    1. Cost vs Safety scatter → trade-off visibility
    2. Cost composition stacked bars → where the money goes
    3. Emissions intensity per vessel → sustainability ranking
    4. Fleet composition by fuel type → diversity assurance
    5. Cost per DWT ranked → value efficiency
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import seaborn as sns
import os
import logging

logger = logging.getLogger(__name__)

# Consistent style
sns.set_theme(style="whitegrid", font_scale=1.1)
FLEET_COLOR = "#2196F3"
NON_FLEET_COLOR = "#BDBDBD"
HIGHLIGHT_CMAP = "tab10"


def _savefig(fig, name: str, output_dir: str):
    """Save figure to output directory and close."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved plot: %s", path)


# ─────────────── 1. COST vs SAFETY SCATTER ─────────────────────────

def plot_cost_vs_safety(all_vessels: pd.DataFrame,
                        fleet_ids: set,
                        output_dir: str):
    """
    Scatter: adjusted cost (x) vs safety score (y), sized by DWT.
    Selected fleet vessels highlighted.
    NARRATIVE: Shows the trade-off frontier — are we selecting
    efficient vessels or sacrificing cost for safety?
    """
    fig, ax = plt.subplots(figsize=(12, 7))

    df = all_vessels.copy()
    df["in_fleet"] = df["vessel_id"].isin(fleet_ids)

    # Non-fleet
    nf = df[~df["in_fleet"]]
    ax.scatter(
        nf["adjusted_cost"] / 1e6, nf["safety_score"],
        s=nf["dwt"] / 1000, alpha=0.35, color=NON_FLEET_COLOR,
        edgecolors="gray", linewidths=0.5, label="Not selected",
    )
    # Fleet
    fl = df[df["in_fleet"]]
    ax.scatter(
        fl["adjusted_cost"] / 1e6, fl["safety_score"],
        s=fl["dwt"] / 1000, alpha=0.85, color=FLEET_COLOR,
        edgecolors="navy", linewidths=0.8, label="Selected fleet",
    )

    ax.set_xlabel("Adjusted Monthly Cost (M USD)")
    ax.set_ylabel("Safety Score")
    ax.set_title("Cost vs Safety — Fleet Selection Trade-off")
    ax.legend(loc="upper right", fontsize=10)
    ax.set_yticks([1, 2, 3, 4, 5])

    _savefig(fig, "01_cost_vs_safety", output_dir)


# ─────────────── 2. COST COMPOSITION (STACKED BAR) ────────────────

def plot_cost_composition(fleet_df: pd.DataFrame, output_dir: str):
    """
    Stacked horizontal bars showing fuel / carbon / ownership / risk premium
    for each selected vessel.
    NARRATIVE: Where does cost come from? Fuel-dominated or CAPEX-heavy?
    """
    df = fleet_df.sort_values("adjusted_cost", ascending=True).copy()
    labels = df["vessel_id"].astype(str)

    fig, ax = plt.subplots(figsize=(14, max(6, len(df) * 0.35)))

    # Components
    fuel     = df["fuel_cost"].values / 1e6
    carbon   = df["carbon_cost"].values / 1e6
    owner    = df["monthly_ownership_cost"].values / 1e6
    premium  = df["risk_premium"].values / 1e6

    y = np.arange(len(df))
    w = 0.7

    ax.barh(y, fuel, w, label="Fuel cost", color="#FF9800")
    ax.barh(y, carbon, w, left=fuel, label="Carbon cost", color="#F44336")
    ax.barh(y, owner, w, left=fuel + carbon, label="Ownership", color="#4CAF50")
    # Risk premium can be negative (reward)
    ax.barh(y, np.abs(premium), w,
            left=fuel + carbon + owner,
            label="Risk premium (abs)", color="#9C27B0", alpha=0.7)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Cost (M USD)")
    ax.set_title("Cost Composition per Vessel — Selected Fleet")
    ax.legend(loc="lower right", fontsize=9)

    _savefig(fig, "02_cost_composition", output_dir)


# ─────────────── 3. EMISSIONS INTENSITY BY VESSEL ──────────────────

def plot_emissions_intensity(fleet_df: pd.DataFrame, output_dir: str):
    """
    Bar chart: CO₂-eq per tonne of fuel consumed, colored by fuel type.
    NARRATIVE: Which fuel types are cleanest per unit energy?
    """
    df = fleet_df.sort_values("co2eq_per_fuel_tonne", ascending=False).copy()
    df = df[df["co2eq_per_fuel_tonne"].notna()]

    fig, ax = plt.subplots(figsize=(14, max(6, len(df) * 0.35)))

    fuel_types = df["main_engine_fuel_type"].unique()
    palette = dict(zip(fuel_types, sns.color_palette(HIGHLIGHT_CMAP, len(fuel_types))))

    colors = [palette[ft] for ft in df["main_engine_fuel_type"]]
    y = np.arange(len(df))
    ax.barh(y, df["co2eq_per_fuel_tonne"], color=colors, edgecolor="gray", linewidth=0.4)

    ax.set_yticks(y)
    ax.set_yticklabels(df["vessel_id"].astype(str), fontsize=8)
    ax.set_xlabel("CO2-eq per tonne of fuel (t/t)")
    ax.set_title("Emissions Intensity -- Selected Fleet Vessels")

    # Legend
    from matplotlib.patches import Patch
    handles = [Patch(facecolor=palette[ft], label=ft) for ft in sorted(fuel_types)]
    ax.legend(handles=handles, loc="lower right", fontsize=8, title="ME Fuel Type")

    _savefig(fig, "03_emissions_intensity", output_dir)


# ─────────────── 4. FLEET COMPOSITION BY FUEL TYPE ─────────────────

def plot_fleet_fuel_composition(fleet_df: pd.DataFrame, output_dir: str):
    """
    Two panes: (a) count by fuel type, (b) DWT contribution by fuel type.
    NARRATIVE: Is fleet diversity genuine or token-only?
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    fuel_counts = fleet_df["main_engine_fuel_type"].value_counts()
    fuel_dwt = fleet_df.groupby("main_engine_fuel_type")["dwt"].sum().sort_values(ascending=False)

    palette = sns.color_palette(HIGHLIGHT_CMAP, len(fuel_counts))

    # (a) Count
    axes[0].barh(fuel_counts.index, fuel_counts.values,
                  color=palette[:len(fuel_counts)], edgecolor="gray")
    axes[0].set_xlabel("Number of Vessels")
    axes[0].set_title("Fleet Vessels by Fuel Type")

    # (b) DWT
    fuel_dwt_sorted = fuel_dwt.reindex(fuel_counts.index)
    axes[1].barh(fuel_dwt_sorted.index, fuel_dwt_sorted.values / 1e6,
                  color=palette[:len(fuel_dwt_sorted)], edgecolor="gray")
    axes[1].set_xlabel("Total DWT (million tonnes)")
    axes[1].set_title("DWT Contribution by Fuel Type")

    plt.tight_layout()
    _savefig(fig, "04_fleet_fuel_composition", output_dir)


# ─────────────── 5. COST PER DWT RANKED ────────────────────────────

def plot_cost_per_dwt_ranked(all_vessels: pd.DataFrame,
                              fleet_ids: set,
                              output_dir: str):
    """
    All vessels ranked by cost/DWT, selected fleet highlighted.
    NARRATIVE: Are we picking the most cost-efficient vessels?
    """
    df = all_vessels.sort_values("cost_per_dwt").copy()
    df["rank"] = range(1, len(df) + 1)
    df["in_fleet"] = df["vessel_id"].isin(fleet_ids)

    fig, ax = plt.subplots(figsize=(14, 6))

    nf = df[~df["in_fleet"]]
    fl = df[df["in_fleet"]]

    ax.bar(nf["rank"], nf["cost_per_dwt"], color=NON_FLEET_COLOR, alpha=0.5, width=1.0)
    ax.bar(fl["rank"], fl["cost_per_dwt"], color=FLEET_COLOR, alpha=0.85, width=1.0)

    ax.set_xlabel("Vessel Rank (by cost/DWT)")
    ax.set_ylabel("Cost per DWT (USD/tonne)")
    ax.set_title("Cost Efficiency — Selected vs All Vessels")
    ax.legend(["Not selected", "Selected"], loc="upper left")

    _savefig(fig, "05_cost_per_dwt_ranked", output_dir)


# ─────────────── 6. SENSITIVITY COMPARISON (OPTIONAL) ──────────────

def plot_sensitivity_comparison(results: list, output_dir: str):
    """
    Compare fleet metrics across sensitivity scenarios.
    results: list of dicts with keys [scenario_name, total_cost, avg_safety,
             total_co2eq, fleet_size, total_dwt].
    """
    if not results or len(results) < 2:
        logger.info("Skipping sensitivity plot — fewer than 2 scenarios.")
        return

    df = pd.DataFrame(results)
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    metrics = [
        ("total_cost", "Total Cost (M USD)", 1e6),
        ("avg_safety", "Avg Safety Score", 1),
        ("total_co2eq", "Total CO2-eq (kt)", 1e3),
        ("fleet_size", "Fleet Size", 1),
    ]

    for ax, (col, ylabel, divisor) in zip(axes.flat, metrics):
        if col in df.columns:
            ax.bar(df["scenario_name"], df[col] / divisor, color=FLEET_COLOR, edgecolor="gray")
            ax.set_ylabel(ylabel)
            ax.tick_params(axis="x", rotation=30)

    plt.suptitle("Sensitivity Analysis — Scenario Comparison", fontsize=14)
    plt.tight_layout()
    _savefig(fig, "06_sensitivity_comparison", output_dir)


# ────────────────── RUN ALL PLOTS ──────────────────────────────────

def generate_all_plots(all_vessels: pd.DataFrame,
                       fleet_df: pd.DataFrame,
                       output_dir: str):
    """Generate all analytical plots for the selected fleet."""
    fleet_ids = set(fleet_df["vessel_id"])

    plot_cost_vs_safety(all_vessels, fleet_ids, output_dir)
    plot_cost_composition(fleet_df, output_dir)
    plot_emissions_intensity(fleet_df, output_dir)
    plot_fleet_fuel_composition(fleet_df, output_dir)
    plot_cost_per_dwt_ranked(all_vessels, fleet_ids, output_dir)

    logger.info("All %d plots generated in '%s'.", 5, output_dir)
