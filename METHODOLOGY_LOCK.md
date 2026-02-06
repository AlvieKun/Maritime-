# Methodology Lock — DO NOT MODIFY

These assumptions are **locked** for the duration of the Maritime Hackathon 2026
optimization experiments. Any code change that alters these items is a
**compliance violation** and will invalidate results.

## Locked Methodology Items

- **Operating mode definitions**
  - Anchorage: `in_anchorage` is not null AND `speed_knots < 1`
  - Maneuver: `in_port_boundary` is not null AND `speed_knots > 1`
  - Transit: `in_port_boundary` is null AND `speed_knots >= 1`
  - Drifting: everything else
  - In-scope: Transit + Maneuver only

- **Activity hours A** = current timestamp − previous timestamp (per vessel)

- **Load Factor rounding & floors**
  - LF = (AS / MS)³, rounded to 2 decimal places
  - If LF < 0.02 AND mode is transit/maneuver → LF = 0.02
  - LF > 1 is allowed and used as-is

- **LLAF lookup rounding rules**
  - %LF = round(LF × 100) to nearest integer
  - LLAF = 1 if %LF > 20
  - If %LF < 2 AND mode is transit/maneuver → use 2% for lookup

- **SFC adjustment** per machinery fuel type (ME/AE/AB separately using each
  machinery's own fuel LCV)

- **Fuel cost** calculated per machinery fuel type (organizer clarification):
  `Total Fuel Cost = Σ(fuel_consumed_by_machinery × Cost_per_GJ × LCV_for_that_machinery)`

- **Carbon cost** = Total CO₂-eq × carbon price per tonne (from Excel sheet)

- **Ownership cost**
  - r = 8%, N = 30 years, salvage = 10% of purchase price
  - CRF = r(1+r)^N / ((1+r)^N − 1)
  - Annual = (P − S) × CRF + r × S
  - Monthly = Annual / 12

- **Risk premium** = Total monthly cost × safety adjustment rate (signed +/−)

- **Adjusted cost** = Total monthly cost + Risk premium

- **Fleet metrics aggregation**
  - Total fuel consumption = simple summation irrespective of fuel type
  - Total cost = sum of adjusted costs
  - Total CO₂-eq = sum of vessel CO₂-eq
  - Average safety = mean of selected vessels' safety scores

## What IS Allowed to Change

- Fleet selection algorithm / optimizer method
- Code structure, new modules, test scripts
- Reporting and visualization

## What is NOT Allowed

- Input data files (Data/, case descriptions/)
- Emissions calculation formulas
- Cost calculation formulas
- Any of the locked parameters above
