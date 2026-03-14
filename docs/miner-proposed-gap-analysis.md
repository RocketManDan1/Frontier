# Miner Proposed Gap Analysis

Gap analysis for the proposed mining-equipment adjustment pass.

This document evaluates the effect of the following rule set:

- keep **tech 1** mining equipment unchanged,
- adjust all higher-tech mining equipment by family,
- reduce MW growth by `2` percentage points at each consecutive tech step within a family.

---

## Proposed Changes

### Cryovolatile

- `+10%` mass
- MW growth starts at `+10%`, then drops by `2pp` per higher tech step
- `+20%` mining rate

### Large Body

- `+13%` mass
- MW growth starts at `+13%`, then drops by `2pp` per higher tech step
- `+17%` mining rate

### Microgravity

- `+7%` mass
- MW growth starts at `+5%`, then drops by `2pp` per higher tech step
- `+13%` mining rate

Tech 1 items remain unchanged.

### MW Growth by Consecutive Tech Step

The tapered MW rule is applied by family progression order, not by a flat multiplier across all higher-tech items.

#### Cryovolatile

| Tech level | MW growth |
|---|---:|
| 1.0 | 0% |
| 2.0 | 10% |
| 3.0 | 8% |

#### Large Body

| Tech level | MW growth |
|---|---:|
| 1.0 | 0% |
| 1.5 | 13% |
| 2.0 | 11% |
| 2.5 | 9% |
| 3.0 | 7% |
| 3.5 | 5% |
| 4.0 | 3% |
| 4.5 | 1% |

#### Microgravity

| Tech level | MW growth |
|---|---:|
| 1.0 | 0% |
| 2.0 | 5% |
| 3.0 | 3% |
| 4.0 | 1% |

---

## Scope

This analysis uses the same mining-capable set as the base miner analysis:

- `miners`
- legacy constructor-miners

Excluded:

- robonauts / prospectors

---

## Method

For each eligible item:

1. Read current `mass_t`, `electric_mw`, and `mining_rate_kg_per_hr`.
2. If `tech_level <= 1`, leave unchanged.
3. Otherwise apply the family multiplier.
4. Recompute:
   - raw throughput (`t/hr`)
   - throughput per ton of equipment
   - throughput per MW
   - energy cost per ton mined (`MWh/t`)

---

## Audit Artifacts

- `docs/miner_proposed_gap_analysis.csv`
- `docs/miner_proposed_gap_summary.csv`
- `docs/miner_proposed_gap_analysis.xlsx`

The workbook contains:

- `detail` tab: one row per mining item
- `summary` tab: family-level before/after averages

---

## High-Level Result

This proposal improves **absolute throughput** in every affected family.

It improves **mass efficiency** by the same amount as the original proposal, but improves **power efficiency** more strongly at higher tiers because MW growth now tapers by tech step.

That means the change is directionally coherent:

- miners get faster,
- mass efficiency improves modestly,
- power efficiency improves more noticeably as tiers rise,
- but the proposal does **not** fundamentally rewrite the current progression structure.

The current identity remains intact:

- cryovolatiles remain the most efficient,
- large-body miners remain scale-focused,
- microgravity remains in the middle.

---

## Family Summary

| Family | Count | Adjusted | Avg Rate Change | Avg Throughput per Ton Change | Avg Throughput per MW Change | Avg MWh/t Change |
|---|---:|---:|---:|---:|---:|---:|
| Cryovolatile | 3 | 2 | +13.33% | +6.06% | +6.73% | -6.11% |
| Large Body | 8 | 7 | +14.88% | +3.10% | +8.31% | -7.48% |
| Microgravity | 4 | 3 | +9.75% | +4.21% | +7.30% | -6.64% |

Interpretation:

- all three families improve,
- cryovolatiles still get the strongest mass-efficiency lift,
- large-body now gets a meaningful power-efficiency lift,
- microgravity also improves cleanly on power efficiency,
- large-body still gets the biggest raw throughput lift, but its mass-efficiency improvement remains the weakest.

---

## Per-Family Interpretation

### Cryovolatile

Because mining rate increases faster than both mass and MW, cryovolatile miners improve cleanly.

Per affected item:

- `cvm_2a_boreas`
   - throughput: `+20%`
   - throughput per MW: `+9.09%`
   - throughput per ton equipment: `+9.09%`
   - MWh per ton mined: `-8.33%`
- `cvm_3a_themis`
   - throughput: `+20%`
   - throughput per MW: `+11.11%`
   - throughput per ton equipment: `+9.09%`
   - MWh per ton mined: `-10.00%`

At the family-average level, because the tech 1 item is unchanged, the observed full-family average is softer:

- `+13.33%` raw throughput
- `+6.73%` throughput per MW
- `+6.06%` throughput per ton

Conclusion:

- strong improvement,
- clean and favorable trade,
- no sign of distortion.

### Large Body

Large-body miners still gain the most raw throughput, but the tapered MW rule now creates a clear efficiency gain at higher tiers.

Per affected item:

- throughput: `+17%`
- throughput per ton equipment: `+3.54%`
- throughput per MW varies by tier:
   - tech `1.5`: `+3.54%`
   - tech `2.0`: `+5.41%`
   - tech `2.5`: `+7.34%`
   - tech `3.0`: `+9.35%`
   - tech `3.5`: `+11.43%`
   - tech `4.0`: `+13.59%`
   - tech `4.5`: `+15.84%`
- MWh per ton mined improves by the same tier-varying pattern in reverse, reaching `-13.68%` at tech `4.5`

Conclusion:

- strong scale increase,
- weak mass-efficiency improvement,
- meaningful power-efficiency improvement at the high end,
- preserves the current identity of big miners as throughput-first machines.

This remains conservative on equipment mass, but it does a better job of reducing late-tier power pain than the flat-MW version did.

### Microgravity

Microgravity gets a better efficiency trade than large-body because MW grows more slowly than rate.

Per affected item:

- `mgm_2a_ixion`
   - throughput: `+13%`
   - throughput per MW: `+7.62%`
   - throughput per ton equipment: `+5.61%`
   - MWh per ton mined: `-7.08%`
- `mgm_3a_sedna`
   - throughput: `+13%`
   - throughput per MW: `+9.71%`
   - throughput per ton equipment: `+5.61%`
   - MWh per ton mined: `-8.85%`
- `mgm_4a_quaoar`
   - throughput: `+13%`
   - throughput per MW: `+11.88%`
   - throughput per ton equipment: `+5.61%`
   - MWh per ton mined: `-10.62%`

Conclusion:

- healthy efficiency gain,
- moderate scale gain,
- keeps microgravity between cryo and large-body in profile,
- and gives later microgravity units a cleaner power curve.

---

## Affected Items

### Cryovolatile

- `cvm_2a_boreas`
- `cvm_3a_themis`

### Large Body

- `gcn_1b_talpa`
- `gcn_2a_hephaestus`
- `gcn_2b_kyklops`
- `gcn_3a_golem`
- `gcn_3b_archon`
- `gcn_4a_titan`
- `gcn_4b_demiurge`

### Microgravity

- `mgm_2a_ixion`
- `mgm_3a_sedna`
- `mgm_4a_quaoar`

Unchanged tech 1 items:

- `cvm_1a_chione`
- `gcn_1a_antaeus`
- `mgm_1a_phaethon`

---

## What This Proposal Actually Does

This proposal is best understood as a **moderate buff pass**, not a systemic rebalance.

It does three things:

1. Raises extraction output across almost all non-tech-1 miners.
2. Gives a mild mass-efficiency improvement to all higher-tier miners.
3. Gives a stronger power-efficiency improvement to higher-tier miners than the flat-MW version.
4. Keeps the current branch identities mostly unchanged.

It does **not** do these things:

1. It does not flatten the large-body mass-efficiency penalty.
2. It does not make late-game miners light or compact.
3. It does not alter the ranking order of the branches.

---

## Design Judgment

This is a good proposal if your goal is:

- make higher-tier miners feel better immediately,
- avoid destabilizing the branch identities,
- improve numbers without forcing a deeper redesign,
- ease late-tier power scaling more than the original flat-MW proposal.

This is not enough if your goal is:

- make large-body miners scale efficiently by mass,
- materially narrow the gap between early and late extraction efficiency,
- reposition the mining ladder around cleaner technology progression.

---

## Recommendation

If you want a low-risk interim pass, this proposal is defensible.

Recommended interpretation by family:

- **Cryovolatile:** strong candidate to accept as-is
- **Microgravity:** good candidate to accept as-is
- **Large Body:** substantially better than the flat-MW version for power scaling, but still under-corrected if the long-term goal is mass-efficient scaling

If a second pass is planned later, large-body should probably be revisited separately.

---

## Suggested Next Step

If this proposed adjustment is likely to move forward, the next useful step is:

1. define target scaling rules for miner families,
2. compare this proposal against those targets,
3. decide whether large-body miners should remain deliberately inefficient by mass while becoming less punitive by power,
4. decide whether the tapered-MW rule should become the default progression rule for future miner additions.
