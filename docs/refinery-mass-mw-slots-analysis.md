# Refinery Mass, MW, and Slot Audit

## Scope
- Inputs: all refinery modules under `items/refineries/*` (excluding `family.json`).
- Core fields audited: `mass_t`, `electric_mw`, `max_concurrent_recipes`.
- Buff fields audited: `throughput_mult` (speed) and `efficiency` (output/input yield factor).

## Family Summary
- lithic: mass 8.0 -> 20.0 t (+150.00%), MW 0.35 -> 15.00 (+4185.71%), slots 1 -> 6 (delta +5), throughput 1.00 -> 8.00 (+700.00%), efficiency 1.00 -> 0.68 (-32.00 pp), input savings 0% -> 32% (+32.00 pp).
- metallurgy: mass 14.0 -> 20.0 t (+42.86%), MW 0.55 -> 25.00 (+4445.45%), slots 1 -> 6 (delta +5), throughput 1.00 -> 8.00 (+700.00%), efficiency 1.00 -> 0.68 (-32.00 pp), input savings 0% -> 32% (+32.00 pp).
- nuclear: mass 12.0 -> 36.0 t (+200.00%), MW 0.80 -> 50.00 (+6150.00%), slots 1 -> 6 (delta +5), throughput 1.00 -> 8.00 (+700.00%), efficiency 1.00 -> 0.68 (-32.00 pp), input savings 0% -> 32% (+32.00 pp).
- volatiles: mass 8.0 -> 20.0 t (+150.00%), MW 0.25 -> 12.00 (+4700.00%), slots 1 -> 6 (delta +5), throughput 1.00 -> 8.00 (+700.00%), efficiency 1.00 -> 0.68 (-32.00 pp), input savings 0% -> 32% (+32.00 pp).

## Buff Mechanics Confirmed in Runtime
- Speed buff exists: refinery jobs use `actual_time = base_time / throughput_mult` (higher `throughput_mult` = faster completion).
- Efficiency buff exists: refinery job outputs are multiplied by `efficiency`; values below 1.0 reduce output and represent yield handling in current code/data conventions.
- Slot scaling exists: deploying a refinery creates `max_concurrent_recipes` rows in `refinery_slots`, allowing parallel recipe execution.

## Proposed Module Changes (Tech 4 Slots = 8)

Assumptions used for this proposal:
- Tech 1 baseline stays unchanged.
- Growth values are applied stepwise to the previous tech tier.
- `max_concurrent_recipes` is set to `1, 2, 4, 8` for tech 1-4.
- Efficiency changes are applied multiplicatively per step (for example, `-5%` means `efficiency *= 0.95`).

### Lithic (proposed)
- Mass growth per step: `+100%`, `+85%`, `+70%`.
- MW growth per step: `+70%`, `+70%`, `+70%`.
- Throughput growth per step: `+70%`, `+85%`, `+100%`.
- Efficiency change per step: `-5%`, `-7%`, `-9%`.

| Tech | Slots | Mass (t) | MW | Throughput Mult | Efficiency | Input Savings |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 8.0000 | 0.3500 | 1.0000 | 1.0000 | 0.00% |
| 2 | 2 | 16.0000 | 0.5950 | 1.7000 | 0.9500 | 5.00% |
| 3 | 4 | 29.6000 | 1.0115 | 3.1450 | 0.8835 | 11.65% |
| 4 | 8 | 50.3200 | 1.7196 | 6.2900 | 0.8040 | 19.60% |

### Metallurgy (proposed)
- Mass growth per step: `+110%`, `+95%`, `+80%`.
- MW growth per step: `+70%`, `+70%`, `+70%`.
- Throughput growth per step: `+60%`, `+75%`, `+90%`.
- Efficiency change per step: `-5%`, `-7%`, `-9%`.

| Tech | Slots | Mass (t) | MW | Throughput Mult | Efficiency | Input Savings |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 14.0000 | 0.5500 | 1.0000 | 1.0000 | 0.00% |
| 2 | 2 | 29.4000 | 0.9350 | 1.6000 | 0.9500 | 5.00% |
| 3 | 4 | 57.3300 | 1.5895 | 2.8000 | 0.8835 | 11.65% |
| 4 | 8 | 103.1940 | 2.7021 | 5.3200 | 0.8040 | 19.60% |

### Nuclear (proposed)
- Mass growth per step: `+120%`, `+110%`, `+100%`.
- MW growth per step: `+110%`, `+110%`, `+110%`.
- Throughput growth per step: `+70%`, `+85%`, `+100%`.
- Efficiency change per step: `-5%`, `-10%`, `-15%`.

| Tech | Slots | Mass (t) | MW | Throughput Mult | Efficiency | Input Savings |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 12.0000 | 0.8000 | 1.0000 | 1.0000 | 0.00% |
| 2 | 2 | 26.4000 | 1.6800 | 1.7000 | 0.9500 | 5.00% |
| 3 | 4 | 55.4400 | 3.5280 | 3.1450 | 0.8550 | 14.50% |
| 4 | 8 | 110.8800 | 7.4088 | 6.2900 | 0.7268 | 27.32% |

### Volatiles (proposed)
- Mass growth per step: `+100%`, `+85%`, `+70%`.
- MW growth per step: `+120%`, `+120%`, `+120%`.
- Throughput growth per step: `+70%`, `+105%`, `+140%`.
- Efficiency change per step: `-5%`, `-10%`, `-15%`.

| Tech | Slots | Mass (t) | MW | Throughput Mult | Efficiency | Input Savings |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 8.0000 | 0.2500 | 1.0000 | 1.0000 | 0.00% |
| 2 | 2 | 16.0000 | 0.5500 | 1.7000 | 0.9500 | 5.00% |
| 3 | 4 | 29.6000 | 1.2100 | 3.4850 | 0.8550 | 14.50% |
| 4 | 8 | 50.3200 | 2.6620 | 8.3640 | 0.7268 | 27.32% |

## Tech 4 Delta vs Current
- Lithic: mass `+151.60%` (`50.32` vs `20.00`), MW `-88.54%` (`1.72` vs `15.00`), throughput `-21.38%` (`6.29` vs `8.00`), efficiency `+12.40 pp` (`0.804` vs `0.680`).
- Metallurgy: mass `+415.97%` (`103.19` vs `20.00`), MW `-89.19%` (`2.70` vs `25.00`), throughput `-33.50%` (`5.32` vs `8.00`), efficiency `+12.40 pp` (`0.804` vs `0.680`).
- Nuclear: mass `+208.00%` (`110.88` vs `36.00`), MW `-85.18%` (`7.41` vs `50.00`), throughput `-21.38%` (`6.29` vs `8.00`), efficiency `+4.68 pp` (`0.727` vs `0.680`).
- Volatiles: mass `+151.60%` (`50.32` vs `20.00`), MW `-77.82%` (`2.66` vs `12.00`), throughput `+4.55%` (`8.36` vs `8.00`), efficiency `+4.68 pp` (`0.727` vs `0.680`).
