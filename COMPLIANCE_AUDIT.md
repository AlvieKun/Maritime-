# Maritime Hackathon 2026 — Compliance Audit Report

**Audit date:** 2026-02-06  
**Branch:** `compliance-audit-2026-02-06`  
**Audited against:** Official Emissions + Cost Calculation Methodology & Organizer Q&A (updated 12:30 pm)

---

## 1. Compliance Checklist

| # | Methodology Item | Status | File & Line | Notes |
|---|---|---|---|---|
| 1 | **mep = P** (Main Engine Power) used in Step 4b | ✅ Compliant | `pipeline/engine_fuel.py` L108 | `df["mep"]` used directly as `P` in `LF × P × sfc_adjusted_me × A / 1e6` |
| 2 | **Activity hour A = current − previous timestamp** | ✅ Compliant | `pipeline/ais_behavior.py` L62–L66 | Uses `df.groupby("vessel_id")["timestamp"].diff()` which computes `current − previous` |
| 3 | **LF can be > 1, use as-is** | ✅ Compliant | `pipeline/engine_fuel.py` L33–L51 | No cap/clip at 1.0. LF is `(AS/MS)³` rounded to 2 d.p., floored at 0.02 only |
| 4a | **Anchorage:** `in_anchorage=1 AND speed<1` | ✅ Compliant | `pipeline/ais_behavior.py` L35 | Uses `.notna()` which matches dataset semantics (string "anchorage" vs NaN) |
| 4b | **Maneuver:** `in_port_boundary=1 AND speed>1` | ✅ Compliant | `pipeline/ais_behavior.py` L37 | Uses `.notna()` which matches dataset semantics (string place-names vs NaN) |
| 4c | **Transit:** `in_port_boundary=0 AND speed≥1` | ✅ Compliant | `pipeline/ais_behavior.py` L39 | Uses `.isna()` for =0 and `>=` for speed threshold |
| 4d | **Drifting:** everything else | ✅ Compliant | `pipeline/ais_behavior.py` L42 | `np.select(..., default="drifting")` |
| 5 | **In-scope:** fuel + emissions only for Transit + Maneuver | ✅ Compliant | `pipeline/ais_behavior.py` L87–L93 | `in_scope = mode ∈ {transit, maneuver}`. Fuel consumption zeroed for out-of-scope at `engine_fuel.py` L109–L112 |
| 6 | **LF rounding:** 2 decimal places | ✅ Compliant | `pipeline/engine_fuel.py` L41 | `.round(LF_DECIMAL_PLACES)` where `LF_DECIMAL_PLACES=2` in config |
| 7 | **LF floor:** if LF < 0.02 AND transit/maneuver → LF=0.02 | ✅ Compliant | `pipeline/engine_fuel.py` L44–L50 | Checks `in_scope & (load_factor < MIN_LOAD_FACTOR)`, sets to 0.02 |
| 8a | **LLAF %LF:** `round(LF×100)` to nearest integer | ✅ Compliant | `pipeline/emissions.py` L46 | `(load_factor * 100).round(0).astype(int)` |
| 8b | **LLAF=1 if %LF > 20** | ✅ Compliant | `pipeline/emissions.py` L57–L62 | Default is 1.0; lookup only applies when `pct_lf <= LLAF_THRESHOLD_PCT` (20) |
| 8c | **LLAF:** if %LF < 2 AND transit/maneuver → use 2% | ✅ Compliant | `pipeline/emissions.py` L49–L53 | Floors at `LLAF_MIN_PCT` (2) for in-scope records |
| 9 | **SFC adjustment** per machinery fuel type (ME/AE/AB separately) | ✅ Compliant | `pipeline/engine_fuel.py` L75–L89 | Maps LCV separately for `lcv_me`, `lcv_ae`, `lcv_blr` from each machinery's fuel type |
| 10 | **Fuel cost: per-machinery pricing** (organizer clarification) | ❌ → ✅ **Fixed** | `pipeline/cost_model.py` L30–L82 | **Was non-compliant:** used `main_engine_fuel_type` rate for all machinery. **Now fixed:** prices ME/AE/BLR fuel separately by each machinery's fuel type |
| 11 | **Emissions Cf per machinery fuel type** | ✅ Compliant | `pipeline/emissions.py` L80–L85 | Maps Cf separately per `(gas, machinery)` using each machinery's own fuel type column |
| 12 | **CO₂-eq = Σ(GWP × Total_Emission)** with GWP: CO₂=1, N₂O=265, CH₄=28 | ✅ Compliant | `pipeline/emissions.py` L98, `pipeline/config.py` L47–L51 | GWP dict matches methodology exactly |
| 13a | **Ownership: r=8%, N=30** | ✅ Compliant | `pipeline/config.py` L67–L68 | `DEPRECIATION_RATE=0.08`, `SHIP_LIFE_YEARS=30` |
| 13b | **Ship cost = DWT base × M(fuel_type)** | ✅ Compliant | `pipeline/cost_model.py` L129–L137 | Looks up base cost by DWT bracket, multiplies by fuel-type multiplier |
| 13c | **Salvage S = 10% of P** | ✅ Compliant | `pipeline/cost_model.py` L139, `pipeline/config.py` L69 | `SALVAGE_FRACTION=0.10` |
| 13d | **CRF formula** | ✅ Compliant | `pipeline/cost_model.py` L115–L119 | `r(1+r)^N / ((1+r)^N - 1)` |
| 13e | **Annual ownership = (P−S)×CRF + r×S** | ✅ Compliant | `pipeline/cost_model.py` L142–L145 | Matches formula exactly |
| 13f | **Annual → Monthly conversion** | ✅ Compliant | `pipeline/cost_model.py` L147 | `annual_ownership_cost / 12.0` |
| 14 | **Total monthly = Fuel + Carbon + Ownership** | ✅ Compliant | `pipeline/cost_model.py` L176–L180 | Sums all three components |
| 15 | **Risk premium = total monthly × adjustment rate** | ✅ Compliant | `pipeline/cost_model.py` L182–L183 | Maps safety score → rate, applies `total × rate` |
| 16 | **Adjusted cost = total monthly + risk premium** | ✅ Compliant | `pipeline/cost_model.py` L184 | `adjusted_cost = total_monthly_cost + risk_premium` (note: rate is signed, so +/- handled) |
| 17 | **Fleet fuel = simple summation irrespective of fuel type** | ✅ Compliant | `pipeline/fleet_selection.py` L248 | `fleet_df["fuel_total"].sum()` — sums all machinery fuel in tonnes |
| 18 | **AIS gaps treated as correct duration** | ✅ Compliant | `pipeline/ais_behavior.py` L77–L82 | Large gaps flagged but retained; no trimming. Per Q&A: "account the total gap" |
| 19 | **Per-record computation (not aggregated early)** | ✅ Compliant | Full pipeline | LF, fuel, emissions all computed per AIS record, then aggregated to vessel level |
| 20 | **Fuel consumption: ME=LF×P×sfc_adj×A/1e6** | ✅ Compliant | `pipeline/engine_fuel.py` L108–L113 | Formula matches Step 4b exactly |
| 21 | **Fuel consumption: AE=AEL×sfc_adj_ae×A/1e6** | ✅ Compliant | `pipeline/engine_fuel.py` L115–L120 | Formula matches Step 4b |
| 22 | **Fuel consumption: AB=ABL×sfc_adj_blr×A/1e6** | ✅ Compliant | `pipeline/engine_fuel.py` L122–L127 | Formula matches Step 4b |
| 23 | **Max speed MS = 1.066 × Vref** | ✅ Compliant | `pipeline/engine_fuel.py` L28–L29 | `VREF_MULTIPLIER=1.066` from config |

---

## 2. Non-Compliance Found & Fix Applied

### Issue: Step 6a — Fuel Cost Pricing (❌ → ✅ Fixed)

**What was wrong:**  
The original `compute_fuel_cost()` in `pipeline/cost_model.py` priced **all** machinery fuel at the `main_engine_fuel_type` rate:

```python
# BEFORE (non-compliant)
vessel_df["fuel_cost_per_tonne"] = vessel_df["main_engine_fuel_type"].map(cost_per_tonne)
vessel_df["fuel_cost"] = vessel_df["fuel_total"] * vessel_df["fuel_cost_per_tonne"]
```

This followed the original Step 6a wording, but the **organizer clarification** (updated 12:30 pm) explicitly overrides this:

> *"fuel costs should be calculated separately for each machinery based on its respective fuel type"*  
> *"Total Fuel Cost (USD) = Σ (Fuel consumed by each machinery × Cost per tonne of fuel for that machinery)"*

**What was changed:**  
`pipeline/cost_model.py` lines 30–82 — `compute_fuel_cost()` now prices each machinery separately:

```python
# AFTER (compliant)
# Main Engine: priced at main_engine_fuel_type rate
vessel_df["fuel_cost_me"] = vessel_df["fuel_me_total"] * cost_per_tonne[main_engine_fuel]
# Auxiliary Engine: priced at aux_engine_fuel_type rate
vessel_df["fuel_cost_ae"] = vessel_df["fuel_ae_total"] * cost_per_tonne[aux_engine_fuel]
# Boiler: priced at boil_engine_fuel_type rate
vessel_df["fuel_cost_blr"] = vessel_df["fuel_blr_total"] * cost_per_tonne[boiler_fuel]
# Total = sum of per-machinery costs
vessel_df["fuel_cost"] = fuel_cost_me + fuel_cost_ae + fuel_cost_blr
```

**Why it matters:**  
Many vessels use Methanol/LNG/Ammonia for their main engine but Distillate fuel for auxiliary engine and boiler. The old approach overcharged (or undercharged) AE/BLR fuel by using the ME fuel price.

---

## 3. Impact on Results

### Base Scenario (safety ≥ 3.0)

| Metric | Before Fix | After Fix | Delta |
|---|---|---|---|
| Fleet size | 22 | 22 | — |
| Total DWT | 4,721,503 t | 4,721,503 t | — |
| **Total cost** | **$20,790,327** | **$20,534,564** | **−$255,763 (−1.2%)** |
| Avg safety | 3.18 | 3.18 | — |
| CO₂-eq | 13,361.56 t | 13,361.56 t | — |
| Fuel consumption | 4,679.35 t | 4,679.35 t | — |
| Fleet composition | Identical | Identical | Same 22 vessels selected |

### Sensitivity: Safety ≥ 4.0

| Metric | Before Fix | After Fix | Delta |
|---|---|---|---|
| Fleet size | 22 | 22 | — |
| Total DWT | 4,597,591 t | 4,597,591 t | — |
| **Total cost** | **$21,669,556** | **$21,382,224** | **−$287,332 (−1.3%)** |
| Avg safety | 4.00 | 4.00 | — |
| CO₂-eq | 12,647.94 t | 12,647.94 t | — |
| Fleet composition | Identical | Identical | Same 22 vessels selected |

### Sensitivity: Carbon price 2× ($160/t)

| Metric | Before Fix | After Fix | Delta |
|---|---|---|---|
| Fleet size | 22 | 22 | — |
| **Total cost** | **$21,873,829** | **$21,618,066** | **−$255,763 (−1.2%)** |
| Fleet composition | Identical | Identical | Same 22 vessels selected |

### Why Cost Decreased

The fix correctly prices AE and Boiler fuel at the **Distillate fuel rate** ($555.10/tonne) for most vessels, instead of the ME fuel rate. For vessels running alternative ME fuels (e.g., Methanol at $1,074.60/t, Hydrogen at $6,000/t), their AE/BLR fuel was previously overpriced. The correction reduces total fuel cost from $22.97M → $22.71M across the full 108-vessel fleet, propagating a ~$256K reduction in the 22-ship base fleet cost.

---

## 4. Risk-to-Submission Summary

| Item | Risk Level | Assessment |
|---|---|---|
| Fuel cost per-machinery pricing | **HIGH** (now fixed) | Would have been flagged by judges as contradicting the explicit clarification. Affected final cost figure in submission. |
| All other methodology items | **LOW** | All 22 other checklist items are fully compliant with methodology and Q&A. |

---

## 5. Items Verified as NOT Requiring Changes

- **Operating mode definitions** — match exactly (confirmed dataset uses NaN/string encoding, so `.notna()`/`.isna()` is semantically correct for the `=1`/`=0` described in methodology)
- **LF > 1 allowed** — no cap exists in code
- **Activity hours direction** — `diff()` on sorted timestamps = current − previous ✅
- **Emissions** — per-record, per-machinery, per-gas with LLAF lookup, all correct
- **Ownership cost** — CRF formula, salvage, annual→monthly all correct
- **Fleet selection constraints** — DWT, safety floor, fuel diversity, no duplication all enforced
- **AIS data gaps** — retained per Q&A guidance, no trimming

---

*Audit performed on branch `compliance-audit-2026-02-06`. No changes to optimization logic, selection heuristics, or model parameters were made. Only the fuel cost calculation was corrected for methodology compliance.*
