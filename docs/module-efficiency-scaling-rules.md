# Module Efficiency Scaling Rules

Groundwork for the upcoming module optimization pass.

This document defines a baseline mass-efficiency scaling model for module recipes, using module output mass versus total recipe input mass. It is intended as a balancing framework, not an immediate content rewrite.

---

## Purpose

The current module recipe set has grown organically. Some recipes are roughly aligned, some are highly lossy, and some create more module mass than their listed material inputs would imply.

For the next optimization phase, we want a simple rule system that:

- keeps raw material recipes where they are for now,
- normalizes **module** recipe efficiency expectations by tech tier,
- allows small per-item variation,
- gives designers a clear target band before hand-tuning flavor and role differences.

This document establishes that target band.

---

## Scope

This rule applies to:

- **module recipes** only,
- specifically recipes with `facility_type = "shipyard"`,
- evaluated by **mass efficiency**, not by item count or abstract quantity.

This rule does **not** apply to:

- raw material processing recipes,
- refinery outputs,
- fuel/chemical conversion recipes,
- recipes whose purpose is intentional concentration, enrichment, or purification unless they are later explicitly pulled into the same balancing framework.

For this phase, materials remain unchanged.

---

## Core Metric

Module recipe efficiency is defined as:

$$
\text{mass efficiency} = \frac{\text{output mass}}{\text{total input mass}}
$$

Where:

- output mass is the module's catalog mass,
- total input mass is the sum of all recipe inputs,
- all masses are evaluated in kg,
- for reporting, kg are converted to tons using:

$$
1 \text{ ton} = 1000 \text{ kg}
$$

Equivalent percentage form:

$$
\text{mass efficiency pct} = \text{mass efficiency} \times 100
$$

Example:

- inputs: `52,500 kg`
- output: `42,000 kg`
- efficiency: $42{,}000 / 52{,}500 = 0.80 = 80\%$

---

## Baseline Tier Curve

The baseline target efficiency curve for modules is:

| Tier | Target efficiency |
|---|---|
| 1 | 60% |
| 2 | 65% |
| 3 | 70% |
| 4 | 75% |

This is the foundational scaling rule.

Interpretation:

- higher tech should generally convert input material mass into finished module mass more efficiently,
- lower tech should waste more material in crude shaping, machining, heat loss, contamination, overbuild, sacrificial tooling, and low-precision fabrication,
- higher tech should still retain some loss, because real fabrication is not perfectly mass-conserving once cutting loss, insulation, process gases, fixture material, rejected tolerances, and embedded process overhead are considered.

---

## Tier Mapping Rule

For the current recipe set, map recipe `min_tech_tier` to the baseline curve using these brackets:

| Recipe min tech tier | Balancing bucket |
|---|---|
| `< 2` | Tier 1 target: 60% |
| `>= 2` and `< 3` | Tier 2 target: 65% |
| `>= 3` and `< 4` | Tier 3 target: 70% |
| `>= 4` | Tier 4 target: 75% |

This is intentionally simple and can be refined later if the tech tree needs half-tier-specific targets.

---

## Noise Band

The target curve is not meant to force every module to the exact same percentage.

Use a small noise band around the baseline target to preserve category identity and avoid overfitting:

- recommended default noise band: **+/- 3 percentage points**,
- acceptable normal variation example:
  - Tier 1: `57%` to `63%`
  - Tier 2: `62%` to `68%`
  - Tier 3: `67%` to `73%`
  - Tier 4: `72%` to `78%`

This noise band is for ordinary differentiation, not role-defining exceptions.

---

## Allowed Exceptions

Not every module family should be forced into the same exact curve without judgment. The baseline is the default, but some classes may justify deliberate offsets.

Examples of plausible downward exceptions:

- nuclear processing equipment,
- exotic separation equipment,
- enrichment-heavy modules,
- modules assumed to consume large hidden process mass in shielding, waste capture, refractory media, or sacrificial internal fabrication material.

Examples of plausible upward exceptions:

- late-game precision fabrication systems,
- compact, high-value electronics-heavy modules if their listed inputs intentionally already represent refined subassemblies,
- modules where the input list is already highly processed and not raw feedstock.

Even for exceptions, the deviation should be explicit and documented, not accidental.

---

## Severity Thresholds

To support audit and triage, classify module recipes by distance from their tier target.

Recommended thresholds:

| Absolute gap from target | Severity |
|---|---|
| `< 8` percentage points | Minor |
| `8-15` percentage points | Moderate |
| `15-25` percentage points | Major |
| `>= 25` percentage points | Drastic |

When a noise band is in use, evaluate severity based on remaining gap after subtracting the allowed noise band.

---

## Current Audit Summary

Using the current module recipe set and the baseline curve above:

- total module recipes audited: `97`
- post-noise (`+/- 3pp`) severe distribution:
  - `9` drastic
  - `12` major
  - `15` moderate
  - `61` minor

This indicates the proposed curve is broadly usable as a framework, but several current recipes are clear outliers and would need deliberate handling.

---

## Current Drastic Outliers

These module recipes are far enough from the proposed rule that they would be heavily affected by a normalization pass.

| Recipe | Current | Target | Gap |
|---|---|---|---|
| `cvm_3a_themis` | 168.27% | 70% | +98.27pp |
| `rfn_4_ion_plasma_sep` | 11.08% | 75% | -63.92pp |
| `rfv_4_plasma_cracker` | 11.43% | 75% | -63.57pp |
| `rfn_3_centrifuge_cascade` | 20.63% | 70% | -49.37pp |
| `rfv_3_steam_reformer` | 22.40% | 70% | -47.60pp |
| `rfm_1_bloomery` | 100.00% | 60% | +40.00pp |
| `spr_4a_fabricator` | 112.00% | 75% | +37.00pp |
| `cvm_2a_boreas` | 98.68% | 65% | +33.68pp |
| `rfn_2_thermal_diffuser` | 34.29% | 65% | -30.71pp |

These outliers fall into two distinct problem types:

### 1. Too efficient

These create much more module mass than the baseline would imply.

Likely causes:

- module output mass too high,
- input requirements too low,
- input list composed of already-finished materials but balanced as if they were raw mass,
- legacy recipe tuning that prioritized convenience over scaling consistency.

### 2. Too inefficient

These destroy far more mass than the baseline would imply.

Likely causes:

- intentional hidden-process assumptions,
- legacy overpricing of industrial infrastructure,
- category-specific balancing that was never formalized,
- recipes functioning as progression or throughput gates rather than true mass-conversion recipes.

---

## Recommended Balancing Workflow

For the upcoming module optimization pass, use this order:

1. Compute current module mass efficiency.
2. Map the recipe to its target efficiency bucket.
3. Check whether the current value falls inside the target plus noise band.
4. If outside the band, decide whether the recipe is:
   - a normal candidate for retuning, or
   - an intentional exception that should remain offset.
5. If retuning, prefer changing one of these levers explicitly:
   - output module mass,
   - input quantities,
   - both, if the module identity itself is being redesigned.

The important part is consistency: every large deviation should be either corrected or justified.

---

## Retuning Guidance

When a module recipe is outside the band, there are two main correction strategies.

### Strategy A: Keep module mass, change recipe inputs

Use when:

- the module's catalog mass feels right,
- the gameplay role and ship-fitting implications should not change,
- only recipe balance is wrong.

Formula for target total input mass:

$$
\text{target input mass} = \frac{\text{output mass}}{\text{target efficiency}}
$$

### Strategy B: Keep recipe inputs, change module mass

Use when:

- the recipe composition feels right,
- the module's catalog mass looks suspect,
- the item likely needs a broader rebalance anyway.

Formula for target output mass:

$$
\text{target output mass} = \text{input mass} \times \text{target efficiency}
$$

### Strategy C: Mark as intentional exception

Use sparingly.

If a module sits far outside the curve because of role design, document the exception in the recipe family notes or balancing notes.

---

## Why This Rule Is Useful

This rule gives the module optimization pass a stable backbone:

- it is simple enough to audit automatically,
- it scales cleanly with progression,
- it preserves room for hand-tuned variation,
- it prevents future recipes from drifting randomly,
- it gives designers a shared language when discussing whether a recipe is expensive for lore reasons or just inconsistent.

Without a baseline like this, every module recipe becomes a one-off judgment call and long-term balancing becomes slower and less defensible.

---

## Suggested Follow-Up Work

Recommended next steps:

1. Split module recipes into families for exception policy:
   - thrusters
   - reactors
   - generators
   - radiators
   - robonauts
   - constructors
   - refineries
2. Decide which families are allowed systematic offsets from the baseline.
3. Produce a retuning sheet with:
   - current efficiency,
   - target efficiency,
   - target output mass,
   - target input mass,
   - recommended action.
4. Add a content-audit script so future recipe additions can be checked automatically.

---

## Audit Artifacts

Current supporting files:

- `docs/recipe_mass_efficiency_tons.csv`
- `docs/recipe_mass_efficiency_tons.xlsx`
- `docs/module_efficiency_gap_analysis.csv`

These are working audit outputs, not the rule source of truth. The source of truth for the balancing framework should be this document.
