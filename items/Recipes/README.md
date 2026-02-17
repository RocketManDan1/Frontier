# Recipes

Construction and processing recipes live in this directory as JSON files.

## File format

Each file should contain one recipe object:

- `recipe_id` (string, unique)
- `name` (string)
- `output_item_id` (string)
- `output_qty` (number)
- `inputs` (array of `{ item_id, qty }`)
- `build_time_s` (number)
- `facility_type` (string)
- `refinery_category` (string)
- `min_tech_tier` (integer)
- `power_kw` (number)
- `byproducts` (array of `{ item_id, qty }`)

Use `recipe_template.json` as a starting point.

## API exposure

Recipes are returned by:

- `GET /api/catalog/recipes`
- `GET /api/shipyard/catalog` under `recipes`
