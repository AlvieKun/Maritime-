"""
config.py — Central configuration for the Maritime Hackathon 2026 pipeline.

All tuneable parameters live here so that sensitivity analysis
(e.g., raising the safety floor from 3 to 4, or changing carbon price)
requires changes in ONE place only.
"""

import os

# ─────────────────────────── FILE PATHS ────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Data")
CASE_DIR = os.path.join(BASE_DIR, "case descriptions")

VESSEL_MOVEMENTS_PATH = os.path.join(DATA_DIR, "vessel_movements_dataset.csv")
CALC_FACTORS_PATH     = os.path.join(DATA_DIR, "calculation_factors.xlsx")
LLAF_TABLE_PATH       = os.path.join(DATA_DIR, "llaf_table.csv")
SUBMISSION_TEMPLATE   = os.path.join(CASE_DIR, "submission_template.csv")

OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# ─────────────────────── CARGO REQUIREMENT ─────────────────────────
# From MPA Annual Report 2024 — Page 10: 2024 Performance Summary
# Bunker Sales Volume = 54.92 million tonnes (all fuel types, 2024)
# Monthly = 54.92 / 12  (problem statement example logic)
ANNUAL_BUNKER_SALES_MT = 54.92e6          # tonnes
MONTHLY_CARGO_REQUIREMENT = ANNUAL_BUNKER_SALES_MT / 12  # ≈ 4,576,667 tonnes

# ──────────────── FLEET SELECTION CONSTRAINTS ──────────────────────
MIN_SAFETY_SCORE = 3.0      # Average fleet safety score floor (change for sensitivity)
REQUIRE_ALL_FUEL_TYPES = True  # At least one vessel per ME fuel type

# ─────────────── OPERATING-MODE SPEED THRESHOLDS ───────────────────
ANCHORAGE_SPEED_THRESHOLD = 1.0   # knots — strict less-than
MANEUVER_SPEED_THRESHOLD  = 1.0   # knots — strict greater-than
TRANSIT_SPEED_THRESHOLD   = 1.0   # knots — greater-than or equal

# ──────────────── ENGINE LOAD FACTOR RULES ─────────────────────────
VREF_MULTIPLIER        = 1.066     # MS = 1.066 × Vref
MIN_LOAD_FACTOR        = 0.02      # Floor for transit / maneuver
LF_DECIMAL_PLACES      = 2         # Standard rounding to 2 d.p.

# ──────────────── EMISSIONS (GWP AR5 values) ───────────────────────
GWP = {
    "CO2": 1,
    "N2O": 265,
    "CH4": 28,
}

# LLAF threshold: if %LF > this, LLAF = 1 for all gases
LLAF_THRESHOLD_PCT = 20
LLAF_MIN_PCT       = 2   # Floor when mode is transit/maneuver

# FALLBACK: Reference LCV for SFC adjustment (Step 4a).
# Primary source: Cf table "Distillate fuel" row, loaded at runtime.
# This fallback is used ONLY if the Cf table lookup fails.
REFERENCE_LCV_FALLBACK = 42.7     # MJ/kg — Distillate fuel per Methodology Step 4a

# ──────────────── COST MODEL PARAMETERS ────────────────────────────
# FALLBACK: Carbon price used ONLY if Excel "Cost of Carbon" sheet fails to load.
# Primary source: calculation_factors.xlsx -> "Cost of Carbon" sheet, loaded at runtime.
CARBON_PRICE_USD_PER_TONNE_FALLBACK = 80.0  # USD/tCO2-eq — European Exchange, as of 2024

# Ship ownership amortisation
DEPRECIATION_RATE = 0.08    # 8 % per annum
SHIP_LIFE_YEARS   = 30
SALVAGE_FRACTION  = 0.10    # 10 % of purchase price

# FALLBACK: DWT size brackets. Primary source: Excel "Cost of ship" sheet.
# Used ONLY if Excel loading fails.
DWT_COST_BRACKETS_FALLBACK = [
    (10_000,  40_000,  35),
    (40_000,  55_000,  53),
    (55_000,  80_000,  80),
    (80_000, 120_000,  78),
    (120_000, float("inf"), 90),
]

# FALLBACK: Multiplier factors. Primary source: Excel "Cost of ship" sheet.
# Used ONLY if Excel loading fails.
FUEL_TYPE_COST_MULTIPLIER_FALLBACK = {
    "Distillate fuel":  1.00,
    "LPG (Propane)":    1.30,
    "LPG (Butane)":     1.35,
    "LNG":              1.40,
    "Methanol":         1.30,
    "Ethanol":          1.20,
    "Ammonia":          1.40,
    "Hydrogen":         1.10,
}

# FALLBACK: Safety adjustment rates. Primary source: Excel "Safety score adjustment" sheet.
# Used ONLY if Excel loading fails.
SAFETY_ADJUSTMENT_FALLBACK = {
    1:  0.10,
    2:  0.05,
    3:  0.00,
    4: -0.02,
    5: -0.05,
}

# ───────────────── FUEL-NAME NORMALISATION MAP ─────────────────────
# The AIS dataset uses "DISTILLATE FUEL" (upper); reference tables use "Distillate fuel"
FUEL_NAME_MAP = {
    "DISTILLATE FUEL": "Distillate fuel",
    "Distillate fuel": "Distillate fuel",
    "LPG (Propane)":   "LPG (Propane)",
    "LPG (Butane)":    "LPG (Butane)",
    "LNG":             "LNG",
    "Methanol":        "Methanol",
    "Ethanol":         "Ethanol",
    "Ammonia":         "Ammonia",
    "Hydrogen":        "Hydrogen",
    "Light Fuel Oil":  "Light Fuel Oil",
    "Heavy Fuel Oil":  "Heavy Fuel Oil",
}

# ────────────────── TEAM / SUBMISSION META ─────────────────────────
TEAM_NAME = "YourTeamName"
CATEGORY  = "A"
REPORT_FILE_NAME = "MaritimeHackathon2026_CasePaper_YourTeamName.pdf"
