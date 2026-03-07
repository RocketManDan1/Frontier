# Research Tree ↔ draw.io Sync

Use this workflow to keep `config/research_tree.json` aligned with your draw.io layout edits.

## Source files

- draw.io source: `config/research_tree.drawio`
- game tree config: `config/research_tree.json`
- sync script: `scripts/sync_research_tree_from_drawio.py`

## Standard workflow

1. Edit `config/research_tree.drawio` in draw.io (or replace it with a newer export).
2. Preview changes (dry run):

```bash
python3 scripts/sync_research_tree_from_drawio.py --drawio config/research_tree.drawio
```

3. Apply changes:

```bash
python3 scripts/sync_research_tree_from_drawio.py --drawio config/research_tree.drawio --write
```

4. Refresh `/research` in-game.

## What the script updates

- Node positions (`x`, `y`) by matching draw.io node titles to research node names.
- Research edges from draw.io connectors (unless `--keep-existing-edges` is used).

## Matching resilience

The sync includes normalization and title aliases for known draw.io label variants/typos (for example: `Advance Solid Core`, `Metellurgy`, `Seperation`, `Cryovolitile`, etc.).

If future edits introduce a new naming variant, add it in:

- `TITLE_ALIASES_TO_NODE_ID` inside `scripts/sync_research_tree_from_drawio.py`

## Useful options

- `--x-scale` / `--y-scale`: control spacing density in output coordinates.
- `--padding`: adds outer margin around the imported layout.
- `--keep-existing-edges`: only update node positions, keep current edge list.

Example with custom spacing:

```bash
python3 scripts/sync_research_tree_from_drawio.py \
  --drawio config/research_tree.drawio \
  --x-scale 1.8 \
  --y-scale 1.0 \
  --padding 240 \
  --write
```
