# Maritime Hackathon 2026 — Smart Fleet Selection Pipeline

## 1. Project Overview

This repository contains a **decision-support pipeline** developed for **Maritime Hackathon 2026 – Smart Fleet Selection**, organised by the Maritime and Port Authority of Singapore (MPA).

The objective is to select a **cost-efficient fleet** of vessels to transport bunker fuel from Singapore to the Australia West Coast, subject to hard constraints on **safety**, **cargo capacity**, and **fuel-type diversity**, while minimising **total adjusted cost** and accounting for **greenhouse gas emissions**.

This is a **multi-objective fleet planning framework** — not a black-box optimiser. Every calculation traces back to the competition methodology document, and every parameter is sourced from the provided Excel reference tables. The pipeline is designed to be **auditable, reproducible, and explainable**.

---

## 2. Competition Context & Objective

| Parameter | Value | Source |
|---|---|---|
| Annual bunker sales volume | 54.92 million tonnes | MPA Annual Report 2024 (Page 10) |
| Monthly cargo requirement | ~4,576,667 tonnes | 54.92M ÷ 12 months |
| Carbon price | $80 USD/tonne CO₂-eq | `calculation_factors.xlsx` → Cost of Carbon |
| Fleet safety floor | Average ≥ 3.0 | Competition constraint |
| Fuel-type diversity | All 8 main-engine fuel types represented | Competition constraint |

The model balances three competing objectives:

1. **Cost** — minimise total adjusted monthly fleet cost (fuel + carbon + ownership + risk premium)
2. **Safety** — meet or exceed the average fleet safety score threshold
3. **Emissions** — account for CO₂, N₂O, and CH₄ using IMO methodology and IPCC AR5 GWP values

**Sensitivity analysis** is a core requirement: the pipeline supports scenario runs (e.g., raising the safety floor to 4.0, or doubling the carbon price) to demonstrate how the optimal fleet composition shifts under different policy assumptions.

---

## 3. Model Philosophy

- **Transparency over optimality**: Every calculation follows the competition methodology step-by-step. There are no hidden heuristics, neural networks, or opaque solvers. A reviewer can trace any output value back to its formula and input data.

- **AIS-based operational behaviour**: Rather than assuming theoretical speeds or utilisation rates, the model derives each vessel's operational profile (transit, maneuver, anchorage, drifting) from real AIS position data. This grounds fuel consumption and emissions estimates in observed behaviour.

- **Sensitivity analysis as a first-class feature**: The framework is designed so that changing a single parameter (safety threshold, carbon price) and re-running the pipeline produces a fully self-consistent alternative fleet — not a partial adjustment.

---

## 4. High-Level Pipeline Architecture

The pipeline executes seven sequential stages:

| Stage | Module | Purpose |
|---|---|---|
| 1 | Data Ingestion | Load and validate all input datasets (AIS records, fuel properties, cost tables, LLAF lookup) |
| 2 | AIS Behaviour Modelling | Classify each AIS record into operational modes (transit, maneuver, anchorage, drifting) based on speed thresholds |
| 3 | Engine Load & Fuel Consumption | Compute load factors from speed ratios, adjust SFC using LCV ratios, calculate ME/AE/BLR fuel consumption per record |
| 4 | Emissions Accounting | Apply fuel-specific emission factors (Cf) and low-load adjustment factors (LLAF) to compute CO₂, N₂O, CH₄, and CO₂-equivalent |
| 5 | Cost Decomposition | Calculate fuel cost, carbon cost, ship ownership cost (CRF-based amortisation), and safety-based risk premium for each vessel |
| 6 | Fleet Selection | Greedy constraint-satisfaction: seed one vessel per fuel type, then fill to meet DWT requirement by cost-per-DWT ranking |
| 7 | Visualisation & Output | Generate diagnostic plots, fleet detail CSVs, and the competition submission file |

All stages are deterministic. No randomisation is used anywhere in the pipeline.

---

## 5. Fleet Selection Logic

The fleet selection algorithm enforces three **hard constraints**:

1. **Cargo capacity**: Total fleet DWT ≥ monthly cargo requirement (~4.58M tonnes)
2. **Safety floor**: Average fleet safety score ≥ threshold (default 3.0)
3. **Fuel diversity**: At least one vessel for each of the 8 main-engine fuel types

The selection proceeds in two phases:

- **Phase 1 — Seeding**: For each of the 8 required fuel types, select the single vessel with the lowest cost-per-DWT. This guarantees fuel-type coverage.
- **Phase 2 — Filling**: From the remaining eligible vessels (those meeting the safety constraint), add vessels in ascending cost-per-DWT order until the DWT requirement is met.

Each vessel is selected **at most once**. The approach is **greedy and deterministic**, producing the same fleet on every run. This makes the result **decision-grade and defensible** — there is no stochastic variation, and the selection rationale for each vessel is logged.

---

## 6. Sensitivity Analysis

The pipeline supports scenario-based sensitivity analysis via the `--sensitivity` flag:

| Scenario | Safety Floor | Carbon Price | Purpose |
|---|---|---|---|
| **Base** | ≥ 3.0 | $80/t (from Excel) | Default optimal fleet |
| **Safety 4.0** | ≥ 4.0 | $80/t | Impact of stricter safety requirements |
| **Carbon 2×** | ≥ 3.0 | $160/t | Impact of higher carbon pricing |

**Key findings from sensitivity analysis:**

- Raising the safety floor from 3.0 to 4.0 increases total cost by ~4.2% ($21.67M vs $20.79M) while shifting the fleet toward higher-safety vessels
- Doubling the carbon price increases total cost by ~5.2% ($21.87M vs $20.79M) but does not change fleet composition — carbon cost is a relatively small share of total cost

Sensitivity analysis is central to the competition because it demonstrates that the model is not brittle and that decision-makers can understand the cost implications of different policy choices.

---

## 7. Repository Structure

```
├── main.py                          # Pipeline orchestrator (entry point)
├── pipeline/                        # Core analysis modules
│   ├── __init__.py                  # Package initialiser
│   ├── config.py                    # All tuneable parameters & file paths
│   ├── data_ingestion.py            # Load & validate CSV/Excel inputs
│   ├── ais_behavior.py              # AIS speed → operational mode classification
│   ├── engine_fuel.py               # Load factor, SFC adjustment, fuel consumption
│   ├── emissions.py                 # Emission factors, LLAF, CO₂-eq accounting
│   ├── cost_model.py                # Fuel cost, carbon cost, ownership, risk premium
│   ├── fleet_selection.py           # Constraint-based greedy fleet selection
│   └── visualization.py            # Diagnostic plots (matplotlib/seaborn)
├── Data/                            # Input datasets
│   ├── vessel_movements_dataset.csv # AIS records (13,216 records, 108 vessels)
│   ├── calculation_factors.xlsx     # Cf table, fuel costs, carbon price, ship costs, safety adj.
│   └── llaf_table.csv               # Low-Load Adjustment Factor lookup table
├── case descriptions/               # Competition reference materials
│   ├── Maritime Hackathon 2026_Calculation Methodology.docx
│   ├── Maritime Hackathon 2026_Problem Statement_Smart_Fleet_Selection_Final.pdf
│   ├── mpa-ar24-full-book_fa.pdf    # MPA Annual Report 2024
│   ├── submission_template.csv
│   └── submission_template - SAMPLE SUBMISSION.csv
├── .gitignore                       # Python/Anaconda ignore rules
└── README.md                        # This file
```

**Note**: The `output/` directory is generated by the pipeline and excluded from version control via `.gitignore`. Run the pipeline to regenerate all outputs.

---

## 8. How to Run the Model

### Prerequisites

- **Python 3.10+** (developed and tested on Python 3.13.5)
- Required packages (all included in standard Anaconda distribution):
  - `pandas`
  - `numpy`
  - `matplotlib`
  - `seaborn`
  - `openpyxl`

### Run the pipeline

```bash
# Default run (base scenario only)
python main.py

# Run with sensitivity analysis (base + safety 4.0 + carbon 2×)
python main.py --sensitivity
```

### Output

All results are written to the `output/` directory:

- `output/base/` — Base scenario results
  - `selected_fleet.csv` — Detailed fleet composition
  - `all_vessels_detailed.csv` — All 108 vessels with computed metrics
  - `YourTeamName_submission.csv` — Competition submission file
  - `*.png` — Diagnostic visualisations
- `output/sensitivity_safety4/` — Safety ≥ 4.0 scenario
- `output/sensitivity_carbon_2x/` — Carbon price doubled scenario
- `output/pipeline.log` — Full execution log with provenance annotations

---

## 9. Reproducibility & Verification

The model is **fully deterministic**:

- No random number generation is used
- No stochastic algorithms or sampling
- Repeated runs produce **bit-identical** output files (verified via MD5 checksums)

Built-in verification:

- All hard constraints are checked and logged (DWT, safety, fuel diversity)
- Cost component sums are internally consistent (fuel + carbon + ownership = total; total + risk premium = adjusted)
- Only transit and maneuver hours contribute to fuel consumption and emissions — anchorage and drifting are excluded
- Provenance logging traces every key parameter to its source (Excel sheet, Cf table, or config fallback)

---

## 10. Scope & Limitations

| Aspect | Scope | Limitation |
|---|---|---|
| Vessel behaviour | AIS-derived speed profiles | Proxy for operational behaviour, not a full route simulation |
| Fuel consumption | IMO methodology (ME + AE + BLR) | Assumes SFC relationship holds across all load conditions |
| Fleet selection | Greedy cost-per-DWT under constraints | Not a mathematical global optimum; designed for transparency |
| Time horizon | Single-month strategic planning | Does not model stochastic delays, weather, or port congestion |
| Emissions | Tank-to-wake (CO₂, N₂O, CH₄) | Does not include well-to-tank or lifecycle emissions |

---

## 11. Licensing / Usage Note

This repository is intended for **hackathon evaluation and educational purposes** as part of the Maritime Hackathon 2026 organised by the Maritime and Port Authority of Singapore. The input datasets and competition materials are provided by the organisers and are subject to their terms of use.
