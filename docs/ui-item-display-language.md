# Unified Item / Module / Resource Display Language

This project uses one shared UI language for presenting inventory entities (items, modules, resources).

## Source of truth

- Shared helper: `static/js/item_display.js`
- Shared card classes: `.inventoryItemCard`, `.inventoryItemIcon`, `.inventoryItemBody`, `.inventoryItemTitle`, `.inventoryItemSub`, `.inventoryItemStats`
- Stack-specific wrappers: `.stackItemStrip`, `.stackItemCard`

## Rules for future additions

1. Use `window.ItemDisplay.createCard(...)` to render entity cards.
2. Use `window.ItemDisplay.iconDataUri(...)` for deterministic icon generation.
3. Keep text structure consistent:
   - `title`: entity display name
   - `subtitle`: kind/category (module/resource/item type)
   - `stats`: concise mass/volume or equivalent compact metrics
4. For drag-and-drop UIs, apply draggable behavior on top of the same card component (do not create a new visual pattern).
5. Do not introduce alternate card layouts for the same entity concepts unless replacing this standard everywhere.

## Current adopters

- Map inventory and stack windows (`static/js/map_windows.js`)
- Shipyard garage and ship stack (`static/js/shipyard.js`)
