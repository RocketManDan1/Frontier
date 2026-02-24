import json
import os

storage_dir = "items/Storage"
for filename in os.listdir(storage_dir):
    if filename.endswith(".json"):
        filepath = os.path.join(storage_dir, filename)
        with open(filepath, "r") as f:
            item = json.load(f)
        
        # Add tech_level 1 to all storage items so they can be boosted
        item["tech_level"] = 1
        
        with open(filepath, "w") as f:
            json.dump(item, f, indent=2)
            f.write("\n")

print("Done")
