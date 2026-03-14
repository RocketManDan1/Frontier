# Wear and Maintenance System

## Overview

All modules degrade over time through a unified **wear rate** system. Each module has a `wear_rate_pct_per_year` stat representing annual condition loss during continuous operation. Condition runs from 100% (new) down to 0% (inoperable).

This creates a sustained maintenance economy where printers serve a permanent role — not just for expansion, but for keeping existing infrastructure running.

## Core Mechanics

### Condition

- Every module has a **condition** value (100% → 0%)
- At 100%: nominal stats
- At 75%: minor performance penalties begin
- At 50%: significant degradation, increased power draw
- At 25%: severe — intermittent failures, sharp output drop
- At 0%: inoperable dead weight until repaired or scrapped

### Maintenance via Printers

Maintenance consumes **printer time** and **materials**, scaled by module mass and repair scope:

- **Quick patch** (+15% condition): ~10–15% of module mass through the printer
- **Major overhaul** (restore to ~90%): ~30–40% of module mass
- **Full rebuild** (new module): 100% of module mass

Material cost is a fraction of the original build recipe proportional to repair scope.

Printer type constraints apply: industrial printers (IPR) service miners, refineries, printers, and prospectors. Ship printers (SPR) service thrusters, reactors, generators, and radiators.

### Example

Servicing a 14t refinery (RFM-1 "Bloomery") with a T1 industrial printer (IPR-1A, 40 kg/hr):
- Quick patch (~1.4t material): **35 hours** printer time
- Major overhaul (~5.6t): **140 hours**
- Full replacement (14t): **350 hours**

### Glitches

Low condition increases the probability of **acute failure events**:

- Generator trips offline temporarily → downstream power loss cascade
- Refinery spoils a batch → wasted input materials, no output
- Prospector returns corrupted survey data → false readings
- Printer produces a defective module → new module starts at reduced condition

## Wear Rate Drivers

Two primary factors determine a module's wear rate:

1. **Mechanical/thermal intensity** — more extreme operating conditions (higher temps, moving fluids, plasma containment) produce higher base rates
2. **Tech tier within a branch** — later models push the envelope harder, extracting more performance at the cost of durability

Branch selection matters more than tier. A T3 solid-core reactor (4%) is more maintainable than a T2.5 liquid-core (8%).

### Wear Sources by Type

- **Operational wear** — baseline entropy from running. Mechanical modules (miners, printers) wear faster than passive ones (radiators)
- **Radiation wear** — cumulative dose degrades electronics-heavy modules. Varies by location (Jupiter system = extreme, asteroid belt = low). Primarily affects prospectors and generators
- **Thermal cycling** — modules that start/stop frequently suffer fatigue. Continuous operation is gentler than batch processing
- **Environmental wear** — abrasive regolith accelerates miner wear, cryogenic environments degrade seals, high gravity stresses structural mounts

## Proposed Wear Rates

### Generators

Thermoelectric generators have no moving parts. Degradation is gradual and chemical/thermal — dopant migration, junction sublimation, skutterudite filler loss at high temperatures. Higher efficiency designs use more exotic crystal structures that are thermodynamically less stable.

Real-world reference: Voyager's RTGs lost ~15–20% thermoelectric efficiency over ~420,000 hours (48 years).

| Item | Tech | Branch | Wear Rate |
|---|---|---|---|
| SNAP-SiGe "Wren" | T1 | silicon_germanium | 2% |
| SP-100 TE "Ibis" | T2 | silicon_germanium | 3% |
| SiGe-Segmented "Crane" | T3 | cascade | 4% |
| ASC-Skutterudite "Egret" | T4 | advanced_solid_state | 6% |

Primary wear mechanism: conversion efficiency degrades over time as thermoelectric junctions deteriorate. Servicing replaces the thermoelectric stacks (hot shoes and cold shoes).

### Reactors

Reactor wear is driven primarily by branch rather than tier. Solid cores are robust workhorses. Each step toward more exotic fuel containment (pebble → liquid → vapor → gas → fusion) increases thermal output but also maintenance burden.

| Item | Tech | Branch | Wear Rate |
|---|---|---|---|
| RD-0410 "Igrit" | T1 | solid_core | 3% |
| KIWI-B4E "Bison" | T1.5 | solid_core | 3% |
| PBR-40 "Cinder" | T1.5 | pebble_bed | 4% |
| PEWEE-1 "Sparrow" | T2 | solid_core | 3% |
| TWR-45 "Stormwind" | T2 | pebble_bed | 5% |
| Phoebus-1B "Titan" | T2.5 | solid_core | 4% |
| LARS-1 "Crucible" | T2.5 | liquid_core | 8% |
| TWR-75 "Firebrand" | T2.5 | pebble_bed | 5% |
| NERVA-2 "Aegis" | T3 | solid_core | 4% |
| CCR-1 "Mirage" | T3 | vapor_core | 10% |
| DCR-200 "Maelstrom" | T3 | liquid_core | 9% |
| Phoebus-2A "Colossus" | T3.5 | solid_core | 5% |
| VCR-400 "Tempest" | T3.5 | vapor_core | 12% |
| GCNR-200 "Alcyone" | T4 | gas_core | 15% |
| GCNR-4000 "Hyperion" | T4.5 | gas_core | 18% |
| Helion He3 Tokamak | T5 | fusion | 20% |

Key wear mechanisms by branch:
- **Solid core**: control drum bearings, neutron reflector swelling, instrumentation drift. Low maintenance.
- **Pebble bed**: pebble handling mechanism, graphite dust from pebble friction, sorting system. Moderate.
- **Liquid core**: containment drum erosion from molten uranium, centrifugal bearings, fission product filters. High.
- **Vapor core**: vortex confinement injectors, containment wall cooling, fission product separation from vapor stream. Very high.
- **Gas core**: magnetic confinement coils, plasma-facing first wall erosion, propellant injection nozzles. Extreme.
- **Fusion**: superconducting magnet maintenance, first wall neutron damage, plasma-facing component replacement. Extreme.

### Thrusters

Thrusters are unique — they only wear **when firing**. Wear rates below assume a typical duty cycle annualized. Actual degradation scales with burn hours.

| Item | Tech | Branch | Wear Rate |
|---|---|---|---|
| SCN-1 "Pioneer" | T1 | solid_core | 4% |
| SCN-2 "Frontier" | T1.5 | solid_core | 4% |
| ASCN-1 "Venture" | T2 | solid_core | 5% |
| ASCN-2 "Atlas" | T2.5 | solid_core | 5% |
| LCN-1 "Torrent" | T2.5 | liquid_core | 8% |
| LCN-2 "Cascade" | T3 | liquid_core | 9% |
| CGN-1 "Helios" | T3 | gas_core_closed | 12% |
| CGN-2 "Prometheus" | T3.5 | gas_core_closed | 13% |
| OGN-1 "Icarus" | T4 | gas_core_open | 17% |
| OGN-2 "Daedalus" | T4.5 | gas_core_open | 19% |

Key wear mechanisms:
- **Nozzle throat erosion** from superheated hydrogen, thermal shock on startup/shutdown
- **Turbopump bearings** operating at high speed in cryogenic hydrogen
- **Propellant valve seals** degrading from cryo-to-hot thermal cycling
- **Regenerative cooling channels** suffering hydrogen embrittlement and microcracking

Higher rated temperatures (2,800K → 5,250K → 12,000K → 25,000K) dramatically accelerate nozzle and chamber erosion.

### Miners, Refineries, Printers, ISRU, Prospectors, Radiators

*To be added.*

## Strategic Implications

### Branch Selection as Maintenance Decision

Higher-performance reactor/thruster branches deliver more thermal MW and Isp but demand more maintenance infrastructure. A solid-core NERVA is a workhorse you can nearly forget about. A gas-core Hyperion needs a dedicated ship printer and material supply chain to keep running.

### Printer Capacity as Bottleneck

Printers are either building new capacity or maintaining existing capacity — not both. Expanding too fast without enough printer capacity means existing equipment degrades while new modules are being built. The scheduling problem (which module to service, when to pause production) is a core strategic decision.

### Location-Dependent Wear

Radiation-heavy environments (Jupiter system, close solar orbit) impose additional wear on electronics-heavy modules. This gives `radiation_composite_armor` a potential role as a shielding upgrade that slows radiation-driven degradation.

### Run Hot vs. Run Long

If modules can be throttled below 100% capacity, running at reduced load could slow wear. Players who overbuild capacity can run everything gently and save on maintenance. Players who are resource-constrained run at max and pay the maintenance tax.

### Target Availability

Real-world mining operations target ~85% equipment availability. Players who plan maintenance well should achieve similar uptime. Neglecting maintenance pushes availability toward 50–60% as glitches cascade.

## Real-World References

- **Mining equipment**: 5–15% of operational time is scheduled maintenance, plus 5–10% unscheduled downtime
- **Electric arc furnaces**: electrode replacement every 50–150 operating hours, refractory relining every 3,000–12,000 hours
- **PEM electrolysers**: membrane replacement every 40,000–60,000 hours (~5–7 years continuous)
- **CNC fabrication**: spindle service every 5,000–8,000 hours, tool changes every 1–50 hours
- **Voyager RTGs**: ~15–20% thermoelectric efficiency loss over 48 years
- **ISS**: ~30% of crew time devoted to maintenance and repair
- **Plasma torches**: electrode/nozzle replacement every 100–500 hours
