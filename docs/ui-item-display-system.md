# Eve Online-Style Item Display System

> **Design reference:** Eve Online inventory grid — data-dense square cells with
> category-specific icons, item names, quantity badges, and rich hover tooltips.

This document describes the unified item display system used throughout
earthmoon-db. Every place that shows an item — ship parts, cargo resources,
shipyard catalogs, location inventories — uses the same shared component library,
CSS classes, backend data contract, and interaction patterns. Follow this spec
when adding any new item-bearing UI.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Shared Library — `item_display.js`](#shared-library--item_displayjs)
   - [Public API](#public-api)
   - [Icon Generation](#icon-generation)
   - [Category Visual Map](#category-visual-map)
   - [Grid Cell Component](#grid-cell-component)
   - [Tooltip System](#tooltip-system)
   - [Legacy `createCard` Wrapper](#legacy-createcard-wrapper)
3. [CSS Reference](#css-reference)
   - [Grid Cell Classes](#grid-cell-classes)
   - [Tooltip Classes](#tooltip-classes)
   - [Grid Container Classes](#grid-container-classes)
4. [Backend Data Contract](#backend-data-contract)
   - [Unified Item Shape](#unified-item-shape)
   - [Transfer Payload](#transfer-payload)
   - [Builder Functions](#builder-functions)
5. [Integration Patterns](#integration-patterns)
   - [Direct DOM Consumers](#direct-dom-consumers)
   - [Post-Render Pattern (innerHTML)](#post-render-pattern-innerhtml)
   - [Drag & Drop](#drag--drop)
6. [Current Adopters](#current-adopters)
7. [How to Add a New Consumer](#how-to-add-a-new-consumer)
8. [How to Add a New Item Category](#how-to-add-a-new-item-category)
9. [Design Decisions & Rationale](#design-decisions--rationale)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Backend (main.py)                                               │
│  Builds unified item dicts with: label, category, mass_kg,       │
│  volume_m3, quantity, icon_seed, transfer, tooltip fields         │
│  (thrust_kn, isp_s, power_mw, capacity_m3, phase …)             │
└──────────────┬───────────────────────────────────────────────────┘
               │  JSON via /api/state, /api/inventory/*, /api/stacks/*
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  item_display.js  (window.ItemDisplay)                           │
│                                                                  │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────┐  │
│  │ iconDataUri()     │  │ createGridCell()   │  │ showTooltip()│  │
│  │ SVG icon with     │  │ Returns a .invCell │  │ Floating div │  │
│  │ category shape +  │  │ DOM element with   │  │ built from   │  │
│  │ 2-letter glyph +  │  │ icon, label, qty   │  │ dataset.*    │  │
│  │ gradient fill     │  │ badge, dataset.*   │  │ attributes   │  │
│  └──────────────────┘  └───────────────────┘  └──────────────┘  │
└──────────────┬───────────────────────────────────────────────────┘
               │  DOM elements
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Consumer JS modules                                             │
│  map_windows.js · fleet.js · shipyard.js · app.js                │
│                                                                  │
│  Each consumer:                                                  │
│   1. Gets item data from API                                     │
│   2. Calls ItemDisplay.createGridCell(options)                   │
│   3. Appends returned DOM element into a grid container          │
│   4. Optionally wires drag events onto the cell                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Shared Library — `item_display.js`

**File:** `static/js/item_display.js`
**Global:** `window.ItemDisplay`
**Pattern:** Revealing module (IIFE returning a public API object)

### Public API

| Method | Returns | Purpose |
|---|---|---|
| `iconDataUri(seed, label, category)` | `string` (data URI) | Generate a deterministic 64×64 SVG icon |
| `createGridCell(options)` | `HTMLDivElement` | Build a complete `.invCell` DOM element |
| `showTooltip(cell, event)` | `void` | Display the floating tooltip for a cell |
| `hideTooltip()` | `void` | Hide the active tooltip |
| `categoryVisual(category)` | `{hueBase, shape}` or `null` | Look up visual config for a category string |
| `escapeHtml(value)` | `string` | HTML-escape a value |
| `fmtKg(value)` | `string` | Format mass (`"1234 kg"` or `"5 t"`) |
| `fmtM3(value)` | `string` | Format volume (`"12.50 m³"`) |
| `hashCode(text)` | `number` | Deterministic string hash |
| `itemGlyph(label)` | `string` | 2-letter abbreviation from an item name |
| `createCard(options)` | `HTMLElement` | **Legacy** — old list-card layout (kept for compatibility) |

### Icon Generation

`iconDataUri(seed, label, category)` produces an inline SVG as a `data:image/svg+xml` URI. Icons are **deterministic** — the same inputs always produce the same icon — and **cached** in a `Map` keyed by `seed::label::category`.

Construction pipeline:

1. **Category lookup** → `CATEGORY_VISUALS[category]` → `{hueBase, shape}`
2. **Hash** the seed string → base hue offset (±20° around `hueBase`)
3. **Gradient fill** — linear gradient from `hsl(hue 72% 46%)` to `hsl(hue+52 72% 28%)`
4. **Shape** — one of 9 SVG shapes (see table below), filled with the gradient
5. **Glyph** — 2-letter abbreviation of the label, centered as `<text>`
6. **Output** — `data:image/svg+xml;utf8,<encoded SVG>`

If the category is unknown, the shape defaults to `"square"` and the hue is `hash % 360`.

### Category Visual Map

Every item category maps to a unique shape and base hue, making items
instantly recognizable at a glance:

| Category | Shape | Hue Base | Visual Description |
|---|---|---|---|
| `thruster` | chevron | 18° (orange-red) | Pointed shield shape — propulsion |
| `reactor` | hexagon | 55° (amber-gold) | Six-sided — nuclear core |
| `generator` | diamond | 145° (green) | Rotated square — power conversion |
| `radiator` | circle | 200° (cyan-blue) | Round — thermal radiation |
| `storage` | square | 260° (purple) | Rounded box — containment |
| `fuel` | drop | 30° (warm orange) | Teardrop — liquid/gas propellant |
| `raw_material` | ore | 95° (yellow-green) | Irregular hexagon — unprocessed |
| `finished_material` | cube | 175° (teal) | Rectangular box — manufactured |
| `resource` | drop | 130° (green) | Teardrop — generic resource |
| `container` | square | 260° (purple) | Same as storage |
| `robonaut` | gear | 310° (magenta) | Circle with inner ring — automation |
| `refinery` | gear | 340° (pink-red) | Circle with inner ring — processing |
| `recipe` | cube | 45° (gold) | Box — blueprint/recipe |

The ±20° hash jitter means two items in the same category have similar but not identical colors.

### Grid Cell Component

`createGridCell(options)` returns a complete DOM element ready for insertion:

```
┌──────────────────────┐ 72px wide × 90px tall
│              ┌─────┐ │
│              │ qty │ │  ← .invCellQty (only if quantity > 1)
│              └─────┘ │
│     ┌──────────┐     │
│     │  48×48   │     │  ← .invCellIcon (<img> with data-URI src)
│     │  icon    │     │
│     └──────────┘     │
│    Item Label Text   │  ← .invCellLabel (2-line clamp)
└──────────────────────┘
         .invCell
```

**Options object:**

| Property | Type | Description |
|---|---|---|
| `label` | `string` | Item display name (shown under icon + in tooltip title) |
| `subtitle` | `string` | Category/type line (shown in tooltip) |
| `iconSeed` | `string` | Deterministic seed for icon generation |
| `category` | `string` | One of the category keys (drives icon shape/hue) |
| `mass_kg` | `number` | Mass in kg (tooltip) |
| `volume_m3` | `number` | Volume in m³ (tooltip) |
| `quantity` | `number` | Stack count. Badge shown only when > 1 |
| `phase` | `string` | `"solid"` / `"liquid"` / `"gas"` (tooltip) |
| `draggable` | `boolean` | Adds `draggable=true` + `.isDraggable` class |
| `className` | `string` | Extra CSS class(es) to add to the cell |
| `stats` | `string` | Freeform info string for tooltip |
| `tooltipLines` | `Array<[label, value]>` | Extra rows for the tooltip body |

**Data attributes stored on the element** (used by the tooltip system):

| Attribute | Source |
|---|---|
| `data-tooltip-label` | `options.label` |
| `data-tooltip-category` | `options.category` |
| `data-tooltip-mass-kg` | `options.mass_kg` |
| `data-tooltip-volume-m3` | `options.volume_m3` |
| `data-tooltip-quantity` | `options.quantity` |
| `data-tooltip-subtitle` | `options.subtitle` |
| `data-tooltip-stats` | `options.stats` |
| `data-tooltip-extra` | JSON-serialized `options.tooltipLines` |
| `data-tooltip-phase` | `options.phase` |

Tooltip events (`pointerenter` / `pointerleave` / `pointermove`) are wired automatically.

### Tooltip System

A single shared `<div class="invTooltip">` is lazily created and appended to `document.body`. It follows the cursor with `requestAnimationFrame`-throttled repositioning, and auto-flips when near viewport edges.

Tooltip layout:
```
┌──────────────────────────────┐
│ Item Name                     │  ← .invTooltipTitle
│ CATEGORY / SUBTITLE           │  ← .invTooltipSub
│ ────────────────────────────  │  ← .invTooltipDivider
│ Quantity              42      │  ← .invTooltipRow (if qty > 1)
│ Mass             5,000 kg     │  ← .invTooltipRow
│ Volume            12.50 m³    │  ← .invTooltipRow
│ Phase               Liquid    │  ← .invTooltipRow (if phase set)
│ Thrust             200 kN     │  ← .invTooltipRow (from tooltipLines)
│ ISP                900 s      │  ← .invTooltipRow (from tooltipLines)
└──────────────────────────────┘
```

Base rows (Mass, Volume, Quantity, Phase) come from the `dataset` attributes.
Extra rows come from `data-tooltip-extra` (a JSON array of `[label, value]` pairs).
Consumers build the `tooltipLines` array from whatever domain-specific stats
their items carry (thrust, ISP, power, capacity, etc.).

### Legacy `createCard` Wrapper

`createCard(options)` is retained for backward compatibility. It produces an
`<article class="inventoryItemCard">` with horizontal list layout (icon + text body).
**Do not use this for new features.** All new UI should use `createGridCell()`.

---

## CSS Reference

### Grid Cell Classes

Located in `static/styles.css` under the `Eve-style square grid cells` comment block.

| Class | Element | Purpose |
|---|---|---|
| `.invCell` | `<div>` | 72×90px cell container. Flex column, dark background, subtle border. Hover glow. |
| `.invCell.isDraggable` | modifier | Sets `cursor: grab` |
| `.invCell.isDragging` | modifier | Reduced opacity during drag |
| `.invCell.isDisabled` | modifier | Grayed out, `pointer-events: none` |
| `.invCellIcon` | `<img>` | 48×48px, `object-fit: contain`, no pointer events |
| `.invCellLabel` | `<div>` | 9px text, centered, 2-line clamp with ellipsis |
| `.invCellQty` | `<span>` | Absolute-positioned top-right badge, pill shape, bold 9px |

Key CSS properties for the cell:

```css
.invCell {
  width: 72px;
  height: 90px;
  border: 1px solid rgba(120, 165, 214, 0.16);
  border-radius: 4px;
  background: rgba(7, 12, 20, 0.82);
  /* ... flex column, center-aligned */
}

.invCell:hover {
  border-color: rgba(109, 182, 255, 0.42);
  box-shadow: 0 0 10px rgba(68, 180, 255, 0.12);
}
```

### Tooltip Classes

| Class | Purpose |
|---|---|
| `.invTooltip` | Fixed-position floating panel. `z-index: 9999`, dark blurred background, pointer-events none. |
| `.invTooltipTitle` | 13px bold white title |
| `.invTooltipSub` | 10px uppercase gray category line |
| `.invTooltipDivider` | 1px horizontal rule |
| `.invTooltipRow` | 2-column grid row (`1fr auto`) |
| `.invTooltipRowLabel` | Left column, muted gray |
| `.invTooltipRowVal` | Right column, bright, right-aligned, `tabular-nums` |

### Grid Container Classes

All grid containers use the same core pattern:

```css
display: grid;
grid-template-columns: repeat(auto-fill, 76px);
gap: 4px;
justify-content: start;
```

The 76px column width accommodates the 72px cell + 4px gap cleanly.

| Class | Used In | Notes |
|---|---|---|
| `.inventoryItemGrid` | Map inventory windows | `overflow: auto; min-height: 0` |
| `.inventoryContainerItems` | Map container group items | Same grid |
| `.stackItemStrip` | Map stack windows | `overflow: auto` |
| `.fleetPartsGrid` | Fleet page expanded details | `margin-top: 6px` |
| `.shipyardGarageGrid` | Shipyard catalog garage | `max-height: 280px; overflow-y: auto` |
| `.shipyardSlotsGrid` | Shipyard build slots | Same grid |
| `.shipInvGrid` | Ship / location info panels | `padding: 6px` |

**To create a new grid container**, add a CSS class with the same
`repeat(auto-fill, 76px)` pattern and set overflow/height constraints as needed.

---

## Backend Data Contract

### Unified Item Shape

Every item displayed in the UI follows a standardized JSON shape, regardless of
whether it's a ship part, cargo resource, or location stack item. All backend
builder functions output items in this format:

```jsonc
{
  // Identity
  "item_uid":     "ship:big_mama:part:0",    // unique across the game state
  "item_kind":    "part",                    // "part" | "resource"
  "item_id":      "nerva2_aegis",            // catalog item identifier
  "label":        "NERVA-2 \"Aegis\"",       // display name

  // Visual
  "subtitle":     "thruster",               // secondary label (tooltip)
  "category":     "thruster",               // drives icon shape/hue
  "icon_seed":    "ship_part::nerva2_aegis::0", // deterministic icon seed
  "phase":        "liquid",                 // (resources only) solid/liquid/gas

  // Metrics
  "mass_kg":      42000.0,
  "volume_m3":    0.0,
  "quantity":     1.0,                      // stack count (parts = 1, resources = mass)

  // Domain stats (nullable — only present when > 0)
  "thrust_kn":    500.0,                    // thrusters
  "isp_s":        900.0,                    // thrusters
  "power_mw":     2000.0,                   // reactors
  "capacity_m3":  100.0,                    // storage containers
  "resource_id":  "",                       // resource ID if applicable

  // Transfer (nullable — null if not transferable)
  "transfer": {
    "source_kind":  "ship_part",            // transfer source type
    "source_id":    "big_mama",             // ship ID or location ID
    "source_key":   "0",                    // part index or resource_id
    "amount":       1.0                     // transferable quantity
  }
}
```

### Transfer Payload

The `transfer` sub-object powers the drag-and-drop system. When a cell is
dragged, the consumer serializes `transfer` as JSON and attaches it to
`dataTransfer` with MIME type `application/x-earthmoon-inventory-transfer`.

Source kinds:
- `ship_part` — a part installed on a ship (amount always 1)
- `ship_resource` — aggregated resource on a ship (amount = total mass)
- `ship_container` — resource inside a specific container (amount = mass in that container)
- `location_part` — a part in location storage (amount = 1)
- `location_resource` — a resource in location storage (amount = mass)

### Builder Functions

All in `main.py`. Each returns `List[Dict[str, Any]]` in the unified item shape:

| Function | Context | Key Fields |
|---|---|---|
| `_stack_items_for_ship(ship_state)` | Ship parts (stack/module view) | `category`, `thrust_kn`, `isp_s`, `power_mw`, `capacity_m3` |
| `_stack_items_for_location(location_payload)` | Location part stacks | Same enrichment from part data |
| `_inventory_items_for_ship(ship_state)` | Ship cargo resources (aggregate) | `category: "resource"`, `phase`, `resource_id` |
| `_inventory_items_for_location(location_payload)` | Location resources + parts | Both `"resource"` and part categories |
| `_inventory_container_groups_for_ship(ship_state)` | Per-container resource items | Grouped by container index |
| `compute_ship_inventory_resources(ship_id, containers)` | Aggregated ship resources | Resources rolled up across containers |

When adding a new item source, create a builder function that outputs dicts
matching the unified shape above. The frontend will render them automatically.

---

## Integration Patterns

### Direct DOM Consumers

When a consumer builds UI by direct DOM manipulation (not innerHTML), it calls
`createGridCell()` and appends the result:

```javascript
const itemDisplay = window.ItemDisplay;

// Build one cell:
const cell = itemDisplay.createGridCell({
  label: "NERVA-2 \"Aegis\"",
  iconSeed: "nerva2_aegis",
  category: "thruster",
  mass_kg: 42000,
  subtitle: "thruster",
  tooltipLines: [
    ["Thrust", "500 kN"],
    ["ISP", "900 s"],
  ],
});

// Append to a grid container:
document.querySelector(".myGrid").appendChild(cell);
```

**Examples:** `map_windows.js` (`renderInventoryItemCard`, `renderStackItemCard`),
`shipyard.js` (`createItemCard`)

### Post-Render Pattern (innerHTML)

Some code constructs HTML as a string (for performance or structural reasons)
and sets it via `innerHTML`. Since `createGridCell()` returns a DOM element, it
can't be embedded in an HTML string. The solution is a **two-phase render**:

**Phase 1 — HTML string** includes a placeholder container with a data attribute:

```javascript
function buildSectionHtml(items) {
  return `
    <div class="sectionTitle">Parts</div>
    <div class="fleetPartsGrid" data-ship-parts="true"></div>
  `;
}
```

**Phase 2 — Post-render** populates the placeholder with real DOM elements:

```javascript
// After innerHTML is set:
const gridEl = container.querySelector('[data-ship-parts="true"]');
items.forEach((item) => {
  const cell = ItemDisplay.createGridCell({
    label: item.name,
    category: item.type,
    iconSeed: item.item_id,
    mass_kg: item.mass_kg,
    // ...
  });
  gridEl.appendChild(cell);
});
```

**Examples:** `fleet.js` (`partsStackHtml` + `renderPartsGrid`),
`app.js` (`buildLocationInventoryHtml` + `renderLocationInventoryGrids`,
`buildInventoryListHtml` + `renderShipInventoryGrids`)

### Drag & Drop

Cells that participate in drag-and-drop are created with `draggable: true`.
The consumer then wires `dragstart` / `dragend` events:

```javascript
const cell = itemDisplay.createGridCell({
  label: item.label,
  // ...
  draggable: !!item.transfer,
});

if (item.transfer) {
  cell.addEventListener("dragstart", (e) => {
    const payload = JSON.stringify(item.transfer);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("application/x-earthmoon-inventory-transfer", payload);
    e.dataTransfer.setData("text/plain", `earthmoon-transfer:${payload}`);
    cell.classList.add("isDragging");
  });

  cell.addEventListener("dragend", () => {
    cell.classList.remove("isDragging");
  });
}
```

Drop targets (`.inventoryDropZone`) listen for `dragover`/`drop` and parse the
MIME payload to call the appropriate transfer API.

---

## Current Adopters

| Module | File | What It Renders |
|---|---|---|
| Map Inventory Windows | `static/js/map_windows.js` | Ship/location cargo as grid cells with drag & drop |
| Map Stack Windows | `static/js/map_windows.js` | Ship modules and location parts as grid cells |
| Fleet Page | `static/js/fleet.js` | Ship parts grid in expanded row details |
| Shipyard | `static/js/shipyard.js` | Catalog garage grid + build slot grid |
| Ship Info Panel | `static/js/app.js` | Ship cargo contents grid |
| Location Info Panel | `static/js/app.js` | Location resources + parts grid |

---

## How to Add a New Consumer

Checklist for adding a new page or panel that displays items:

### 1. Backend — provide unified item data

Create or reuse a builder function in `main.py` that returns items in the
[unified shape](#unified-item-shape). Ensure every item has at minimum:
`label`, `category`, `icon_seed`, `mass_kg`, and `quantity`.

### 2. HTML — include `item_display.js`

Add the script tag in your HTML page (after any dependencies):

```html
<script src="/static/js/item_display.js"></script>
```

### 3. CSS — add a grid container class

Add a class to `static/styles.css`:

```css
.myNewGrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, 76px);
  gap: 4px;
  justify-content: start;
  /* Add overflow/height constraints as needed */
}
```

### 4. JS — render grid cells

```javascript
const ItemDisplay = window.ItemDisplay;

async function renderMyItems() {
  const resp = await fetch("/api/my-items");
  const data = await resp.json();
  const grid = document.querySelector(".myNewGrid");
  grid.innerHTML = "";

  for (const item of data.items) {
    // Build tooltip lines from domain-specific stats
    const tooltipLines = [];
    if (item.thrust_kn) tooltipLines.push(["Thrust", `${item.thrust_kn} kN`]);
    if (item.isp_s) tooltipLines.push(["ISP", `${item.isp_s} s`]);

    const cell = ItemDisplay.createGridCell({
      label: item.label,
      iconSeed: item.icon_seed,
      category: item.category,
      mass_kg: item.mass_kg,
      volume_m3: item.volume_m3,
      quantity: item.quantity,
      phase: item.phase,
      subtitle: item.subtitle,
      draggable: !!item.transfer,
      tooltipLines: tooltipLines.length ? tooltipLines : undefined,
    });

    // Wire drag if transferable
    if (item.transfer) {
      cell.addEventListener("dragstart", (e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData(
          "application/x-earthmoon-inventory-transfer",
          JSON.stringify(item.transfer)
        );
        cell.classList.add("isDragging");
      });
      cell.addEventListener("dragend", () => {
        cell.classList.remove("isDragging");
      });
    }

    grid.appendChild(cell);
  }
}
```

### 5. Test

- Items render as 72×90px cells in a wrapping grid
- Icons have the correct category shape and hue
- Hover tooltip appears with all relevant stats
- Drag & drop works (if enabled)
- Grid reflows responsively when the container resizes

---

## How to Add a New Item Category

When adding a fundamentally new type of item (e.g., `weapon`, `sensor`, `habitat`):

### 1. Choose a shape and hue

Pick an unused SVG shape or add a new one. Available shapes:
`hexagon`, `diamond`, `circle`, `chevron`, `drop`, `ore`, `cube`, `gear`, `square`

To add a new shape, add a `case` to the `shapeSvg()` function in `item_display.js`.
The shape must fit within a 64×64 SVG viewBox.

### 2. Register in `CATEGORY_VISUALS`

```javascript
const CATEGORY_VISUALS = {
  // ... existing entries ...
  weapon: { hueBase: 0, shape: "chevron" },  // red chevron
};
```

### 3. Backend — set the `category` field

In your builder function, set `"category": "weapon"` on the item dicts.
The frontend icon system picks up the mapping automatically.

### 4. Tooltip lines

Build appropriate `tooltipLines` in the consumer JS. There is no central
registry — each consumer decides which stats are relevant:

```javascript
const tooltipLines = [];
if (item.damage_dps) tooltipLines.push(["DPS", `${item.damage_dps}`]);
if (item.range_km) tooltipLines.push(["Range", `${item.range_km} km`]);
```

---

## Design Decisions & Rationale

### Why procedural SVG icons instead of image assets?

- **Zero asset pipeline.** No sprite sheets, no image optimization, no CDN.
  Icons are generated at runtime and cached in a `Map`.
- **Deterministic.** Same item always gets the same icon. Cache-friendly.
- **Category-driven.** The shape/hue system gives instant visual recognition
  (hexagon = reactor, chevron = thruster) without requiring per-item artwork.
- **Small footprint.** Each icon is ~500 bytes of inline SVG.

### Why dataset attributes for tooltip data?

Storing tooltip data as `data-*` attributes on the cell means:
- The tooltip can be shown from any pointer event without needing access to the
  original item object.
- Cells can be created in one scope and tooltipped in another (e.g., post-render
  pattern).
- No closure memory leaks — all state is in the DOM.

### Why a single shared tooltip element?

One `<div class="invTooltip">` is lazily created and reused. This avoids
creating/destroying tooltip DOMs on every hover, reduces GC pressure, and makes
z-index stacking trivial.

### Why `repeat(auto-fill, 76px)` grid columns?

- 76px = 72px cell + 4px gap. Cells pack tightly with consistent spacing.
- `auto-fill` makes the grid responsive to container width — cells reflow
  automatically with no JS resize listeners.
- Every grid container in the app uses the same column width for visual
  consistency.

### Why the post-render pattern?

Several modules (`fleet.js`, `app.js`) build large HTML trees as strings for
performance. `createGridCell()` returns a DOM element, not an HTML string.
Rather than rewrite those modules to be fully DOM-based, the post-render pattern
lets them keep their string-based structure while still using the shared grid
cell component. The two-phase approach (innerHTML → querySelector → appendChild)
is explicit and easy to follow.
