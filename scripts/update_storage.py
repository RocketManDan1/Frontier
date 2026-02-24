import json
import os

storage_data = {
    "solid_tank_10_m3": {"mass": 800, "recipe": [{"item_id": "structural_alloys", "qty": 0.8}]},
    "solid_tank_50_m3": {"mass": 3000, "recipe": [{"item_id": "structural_alloys", "qty": 3.0}]},
    "solid_tank_100_m3": {"mass": 5500, "recipe": [{"item_id": "structural_alloys", "qty": 5.5}]},
    "water_tank_10_m3": {"mass": 1500, "recipe": [{"item_id": "structural_alloys", "qty": 1.3}, {"item_id": "cryo_polymers", "qty": 0.2}]},
    "water_tank_50_m3": {"mass": 6500, "recipe": [{"item_id": "structural_alloys", "qty": 5.5}, {"item_id": "cryo_polymers", "qty": 1.0}]},
    "water_tank_100_m3": {"mass": 12000, "recipe": [{"item_id": "structural_alloys", "qty": 10.0}, {"item_id": "cryo_polymers", "qty": 2.0}]},
    "gas_tank_10_m3": {"mass": 2500, "recipe": [{"item_id": "advanced_aerospace_alloys", "qty": 1.5}, {"item_id": "carbon_composites", "qty": 1.0}]},
    "gas_tank_50_m3": {"mass": 12000, "recipe": [{"item_id": "advanced_aerospace_alloys", "qty": 7.0}, {"item_id": "carbon_composites", "qty": 5.0}]},
    "gas_tank_100_m3": {"mass": 22000, "recipe": [{"item_id": "advanced_aerospace_alloys", "qty": 13.0}, {"item_id": "carbon_composites", "qty": 9.0}]}
}

for item_id, data in storage_data.items():
    # Update storage item
    storage_path = f"items/Storage/{item_id}.json"
    if os.path.exists(storage_path):
        with open(storage_path, "r") as f:
            item = json.load(f)
        item["mass_kg"] = data["mass"]
        with open(storage_path, "w") as f:
            json.dump(item, f, indent=2)
            f.write("\n")
    
    # Create recipe
    recipe_path = f"items/Recipes/{item_id}.json"
    name = item_id.replace("_", " ").title()
    recipe = {
        "recipe_id": item_id,
        "name": f"Produce {name}",
        "output_item_id": item_id,
        "output_qty": 1,
        "inputs": data["recipe"],
        "build_time_s": 1000,
        "facility_type": "shipyard",
        "refinery_category": "unassigned",
        "min_tech_tier": 1,
        "power_kw": 50,
        "byproducts": []
    }
    with open(recipe_path, "w") as f:
        json.dump(recipe, f, indent=2)
        f.write("\n")

print("Done")
