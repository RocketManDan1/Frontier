# Printer Mass/MW vs Build Rate Audit

## Scope
- Inputs: all printer modules under `items/printers/industrial` and `items/printers/ship` (excluding `family.json`).
- Metrics: `mass_t`, `electric_mw`, `construction_rate_kg_per_hr`, plus derived efficiency ratios.

## Family Summary
- industrial: tech 1 -> 4; rate 40 -> 600 kg/h (+1400.00%), MW 1.5 -> 100.0 (+6566.67%), mass 2.5 -> 160.0 t (+6300.00%), kg/h per MW 26.67 -> 6.00 (-77.50%), MWh/t 37.50 -> 166.67 (+344.44%).
- ship: tech 1 -> 4; rate 50 -> 750 kg/h (+1400.00%), MW 1.2 -> 90.0 (+7400.00%), mass 2.0 -> 140.0 t (+6900.00%), kg/h per MW 41.67 -> 8.33 (-80.00%), MWh/t 24.00 -> 120.00 (+400.00%).

## Step-by-Step Notes
### Industrial Printers
- Tech 1 baseline `ipr_1a_mold`: mass 2.5 t, MW 1.5, rate 40 kg/h, kg/h/MW 26.67, kg/h/t 16.00.
- Tech 1 -> 2 `ipr_2a_forge`: mass +220.00%, MW +433.33%, rate +150.00%, kg/h/MW -53.12%, kg/h/t -21.88%, MWh/t +113.33%.
- Tech 2 -> 3 `ipr_3a_foundry`: mass +400.00%, MW +275.00%, rate +150.00%, kg/h/MW -33.33%, kg/h/t -50.00%, MWh/t +50.00%.
- Tech 3 -> 4 `ipr_4a_colossus`: mass +300.00%, MW +233.33%, rate +140.00%, kg/h/MW -28.00%, kg/h/t -40.00%, MWh/t +38.89%.

### Ship Printers
- Tech 1 baseline `spr_1a_lathe`: mass 2.0 t, MW 1.2, rate 50 kg/h, kg/h/MW 41.67, kg/h/t 25.00.
- Tech 1 -> 2 `spr_2a_mill`: mass +250.00%, MW +483.33%, rate +140.00%, kg/h/MW -58.86%, kg/h/t -31.43%, MWh/t +143.06%.
- Tech 2 -> 3 `spr_3a_press`: mass +400.00%, MW +300.00%, rate +150.00%, kg/h/MW -37.50%, kg/h/t -50.00%, MWh/t +60.00%.
- Tech 3 -> 4 `spr_4a_fabricator`: mass +300.00%, MW +221.43%, rate +150.00%, kg/h/MW -22.22%, kg/h/t -37.50%, MWh/t +28.57%.

## Proposed Change (Tech 1 Fixed)

Tech 1 remains unchanged for both printer families. Growth percentages are applied stepwise from one tech level to the next.

### Industrial Printers (proposed)
- Build rate growth per step: +150%, then +165%, then +180%.
- Mass growth per step: +125%, then +115%, then +105%.
- MW growth per step: +135%, then +130%, then +125%.

| Tech | Step | Mass (t) | MW | Build Rate (kg/h) | kg/h per MW | kg/h per t |
|---|---|---:|---:|---:|---:|---:|
| 1 | baseline | 2.50 | 1.50 | 40.00 | 26.67 | 16.00 |
| 2 | 1 -> 2 | 5.62 | 3.52 | 100.00 | 28.37 | 17.78 |
| 3 | 2 -> 3 | 12.09 | 8.11 | 265.00 | 32.68 | 21.91 |
| 4 | 3 -> 4 | 24.79 | 18.24 | 742.00 | 40.67 | 29.93 |

### Aerospace Printers (ship printer line, proposed)
- Build rate growth per step: +160%, then +175%, then +190%.
- Mass growth per step: +130%, then +124%, then +118%.
- MW growth per step: +135%, then +133%, then +131%.

| Tech | Step | Mass (t) | MW | Build Rate (kg/h) | kg/h per MW | kg/h per t |
|---|---|---:|---:|---:|---:|---:|
| 1 | baseline | 2.00 | 1.20 | 50.00 | 41.67 | 25.00 |
| 2 | 1 -> 2 | 4.60 | 2.82 | 130.00 | 46.10 | 28.26 |
| 3 | 2 -> 3 | 10.30 | 6.57 | 357.50 | 54.41 | 34.70 |
| 4 | 3 -> 4 | 22.46 | 15.18 | 1036.75 | 68.31 | 46.16 |

## Impact vs Current Tech 4
- Industrial tech 4 (`ipr_4a_colossus`): proposed 742 kg/h vs current 600 kg/h (+23.67%), 24.79 t vs 160 t (-84.50%), 18.24 MW vs 100 MW (-81.76%).
- Aerospace tech 4 (`spr_4a_fabricator`): proposed 1036.75 kg/h vs current 750 kg/h (+38.23%), 22.46 t vs 140 t (-83.95%), 15.18 MW vs 90 MW (-83.14%).
