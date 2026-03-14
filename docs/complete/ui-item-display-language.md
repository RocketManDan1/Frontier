# Unified Item / Module / Resource Display Language

> **⚠️ Superseded.** This document describes the original card-based layout.
> The system has been replaced by Eve Online-style grid cells.
> See **[ui-item-display-system.md](ui-item-display-system.md)** for the current spec.

This project uses one shared UI language for presenting inventory entities (items, modules, resources).

## Source of truth

- Shared helper: `static/js/item_display.js`
- **Primary grid cell classes:** `.invCell`, `.invCellIcon`, `.invCellLabel`, `.invCellQty`
- **Tooltip classes:** `.invTooltip`, `.invTooltipTitle`, `.invTooltipSub`, `.invTooltipDivider`, `.invTooltipRow`
- Legacy card classes (deprecated): `.inventoryItemCard`, `.inventoryItemIcon`, `.inventoryItemBody`, `.inventoryItemTitle`, `.inventoryItemSub`, `.inventoryItemStats`
- Stack-specific wrappers: `.stackItemStrip`, `.stackItemCard`

## Rules for future additions

1. Use `window.ItemDisplay.createGridCell(...)` to render item cells (see [full docs](ui-item-display-system.md)).
2. Use `window.ItemDisplay.iconDataUri(...)` for deterministic icon generation.
3. Keep text structure consistent:
   - `label`: entity display name
   - `subtitle`: kind/category (module/resource/item type)
   - `tooltipLines`: array of `[label, value]` pairs for domain-specific stats
4. For drag-and-drop UIs, apply draggable behavior on top of the same cell component (do not create a new visual pattern).
5. Do not introduce alternate cell layouts for the same entity concepts unless replacing this standard everywhere.

## Current adopters

- Map inventory and stack windows (`static/js/map_windows.js`)
- Shipyard garage and ship stack (`static/js/shipyard.js`)
- Fleet page ship details (`static/js/fleet.js`)
- Ship/location info panels (`static/js/app.js`)
