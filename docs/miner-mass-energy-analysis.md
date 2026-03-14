# Miner Mass and Energy Analysis

Baseline analysis for mining-capable industrial equipment, focused on how equipment mass and electric power scale against mining throughput.

This document is intended as groundwork for later mining optimization and equipment scaling work.

---

## Scope

This analysis includes equipment that is actually mining-capable in runtime and content data:

- `items/miners/*`
- `items/constructors/*` that expose `mining_rate_kg_per_hr`

This analysis excludes robonauts.

Reason:

- robonauts currently function as **prospectors**, not active miners,
- their item data includes scan and prospecting fields, not mining throughput,
- runtime logic does not treat robonauts as productive mining units.

---

## Data Sources

Audit artifacts generated from current content data:

- `docs/miner_mass_energy_analysis.csv`
- `docs/miner_mass_energy_summary.csv`
- `docs/miner_mass_energy_analysis.xlsx`

The workbook contains:

- `detail` tab: one row per mining-capable item
- `summary` tab: grouped averages by mining family

---

## Core Fields Used

For each mining item, the analysis uses:

- `mass_t`
- `power_requirements.electric_mw`
- `performance.mining_rate_kg_per_hr`
- `miner_type`
- `operational_environment`
- `tech_level`

---

## Core Metrics

### 1. Raw mining rate

$$
\text{mining rate t/hr} = \frac{\text{mining rate kg/hr}}{1000}
$$

### 2. Mining rate per ton of equipment

This shows how much hourly output you get for each ton of installed miner mass.

$$
\text{tons/hr per ton equipment} = \frac{\text{mining rate t/hr}}{\text{equipment mass t}}
$$

Higher is better.

### 3. Mining rate per MW

This shows how much hourly output you get for each MW of electric demand.

$$
\text{tons/hr per MW} = \frac{\text{mining rate t/hr}}{\text{electric MW}}
$$

Higher is better.

### 4. Energy cost per ton mined

This is the inverse of throughput per MW.

$$
\text{MWh per ton mined} = \frac{\text{electric MW}}{\text{mining rate t/hr}}
$$

Lower is better.

---

## Equipment Sets Analyzed

Total mining-capable items analyzed: `15`

Grouped into:

- `constructors / large_body`: `8`
- `miners / cryovolatile`: `3`
- `miners / microgravity`: `4`

---

## Group Averages

| Group | Count | Avg Mass (t) | Avg Electric MW | Avg Rate (t/hr) | Avg t/hr/MW | Avg MWh/t |
|---|---|---:|---:|---:|---:|---:|
| Constructors / Large-Body | 8 | 85.112 | 55.750 | 0.481 | 0.024988 | 72.643 |
| Miners / Cryovolatile | 3 | 14.767 | 13.400 | 0.283 | 0.036481 | 36.818 |
| Miners / Microgravity | 4 | 24.550 | 19.025 | 0.277 | 0.025612 | 50.178 |

Initial takeaway:

- cryovolatile miners are the most power-efficient group,
- large-body constructors are the least power-efficient on average,
- microgravity miners sit between them on power efficiency,
- large-body constructors have much larger installed mass and scale much harder in absolute throughput.

---

## Best and Worst by Power Efficiency

Measured as `tons/hr per MW`.

### Best

| Item | t/hr/MW | MWh/t | Rate (t/hr) |
|---|---:|---:|---:|
| `cvm_1a_chione` | 0.066667 | 15.000 | 0.080 |
| `gcn_1a_antaeus` | 0.062500 | 16.000 | 0.050 |
| `gcn_1b_talpa` | 0.050000 | 20.000 | 0.110 |
| `mgm_1a_phaethon` | 0.050000 | 20.000 | 0.030 |
| `gcn_2a_hephaestus` | 0.030000 | 33.333 | 0.180 |

### Worst

| Item | t/hr/MW | MWh/t | Rate (t/hr) |
|---|---:|---:|---:|
| `gcn_4b_demiurge` | 0.006136 | 162.963 | 1.350 |
| `gcn_4a_titan` | 0.007500 | 133.333 | 0.900 |
| `gcn_3b_archon` | 0.010909 | 91.667 | 0.600 |
| `mgm_4a_quaoar` | 0.012727 | 78.571 | 0.700 |
| `gcn_3a_golem` | 0.014286 | 70.000 | 0.400 |

Interpretation:

- early mining systems are far more energy-efficient per unit of extraction,
- later mining systems are much more power-hungry relative to throughput,
- current scaling strongly favors **absolute throughput growth** over **energy efficiency growth**.

---

## Best and Worst by Mass Efficiency

Measured as `tons/hr per ton of equipment`.

### Best

| Item | t/hr per t equipment | Equipment Mass (t) | Rate (t/hr) |
|---|---:|---:|---:|
| `cvm_1a_chione` | 0.044444 | 1.8 | 0.080 |
| `gcn_2b_kyklops` | 0.031707 | 8.2 | 0.260 |
| `cvm_2a_boreas` | 0.029333 | 7.5 | 0.220 |
| `gcn_2a_hephaestus` | 0.028571 | 6.3 | 0.180 |
| `gcn_1a_antaeus` | 0.025000 | 2.0 | 0.050 |

### Worst

| Item | t/hr per t equipment | Equipment Mass (t) | Rate (t/hr) |
|---|---:|---:|---:|
| `gcn_4b_demiurge` | 0.003857 | 350.0 | 1.350 |
| `gcn_4a_titan` | 0.005000 | 180.0 | 0.900 |
| `gcn_3b_archon` | 0.007059 | 85.0 | 0.600 |
| `gcn_3a_golem` | 0.008889 | 45.0 | 0.400 |
| `mgm_4a_quaoar` | 0.009333 | 75.0 | 0.700 |

Interpretation:

- installed mass scales up much faster than output at the high end,
- late-game miners appear to buy strategic scale and capability, not favorable mass efficiency,
- if transport mass, deployment mass, or fabrication burden matters, current late miners are expensive in a very literal way.

---

## Scaling Pattern Observed

The current miner progression does **not** behave like a standard efficiency ladder.

Instead, it behaves like this:

- early miners are relatively lean and power-efficient,
- mid-tier miners still improve output without collapsing efficiency too badly,
- late miners achieve much larger absolute throughput,
- but they do so with steep increases in both mass and power demand,
- meaning endgame miners mostly scale by **bigness**, not by **efficiency**.

That may be intentional, but if it is intentional it should be made explicit as a design rule.

---

## Design Implications

This analysis suggests the project should decide which scaling philosophy it wants for miners.

### Option A: Bigger miners should also become more efficient

Use if the intended fantasy is technological refinement.

Under this model:

- later miners should show better `t/hr/MW`,
- later miners should show better or at least stable `t/hr per t equipment`,
- energy cost per ton should fall or stay roughly flat with tech progression.

### Option B: Bigger miners should mainly buy scale, not efficiency

Use if the intended fantasy is industrial gigantism.

Under this model:

- later miners can have worse `t/hr/MW`,
- later miners can have worse `t/hr per t equipment`,
- but absolute throughput should rise enough to justify their footprint,
- power grid and logistics burden become part of the cost of scaling.

### Option C: Split by mining family

This may be the strongest long-term model.

Example:

- cryovolatiles: best energy efficiency,
- microgravity: balanced efficiency,
- large-body gravity mining: worst efficiency but highest top-end throughput.

That would give each mining branch a clear identity instead of forcing one universal curve.

---

## Recommended Next Questions

Before changing content, decide the following:

1. Should higher-tech miners be expected to improve **absolute throughput**, **power efficiency**, **mass efficiency**, or some combination?
2. Should large-body miners be intentionally worse on energy efficiency because of excavation physics and material handling scale?
3. Should miners and constructors continue sharing the same scaling expectations, or should legacy constructor-miners be normalized separately?
4. Is deployment mass intended to be a real balancing lever for surface industry, or mostly flavor?

---

## Suggested Follow-Up Work

Practical next steps:

1. Add a mining scaling rules doc similar to module efficiency scaling.
2. Define family-specific target bands for:
   - `tons/hr per MW`
   - `tons/hr per ton equipment`
   - absolute `tons/hr`
3. Flag outliers by tier and family.
4. Decide whether late miners need lower power draw, higher throughput, or both.
5. Separate constructor utility value from mining efficiency if constructors are carrying hidden value through build capability.

---

## Summary

The current mining equipment set is not simply under- or over-tuned; it expresses a specific pattern:

- early systems are efficient,
- late systems are huge,
- power and mass costs scale faster than mining throughput,
- cryovolatile miners are currently the strongest branch in energy efficiency,
- late large-body constructor-miners are the weakest in both power-normalized and mass-normalized extraction.

That gives a strong basis for the next balancing step: choose whether future miner optimization should preserve this identity or move the whole mining ladder toward cleaner scaling.
