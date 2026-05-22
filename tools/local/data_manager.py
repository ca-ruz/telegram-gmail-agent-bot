import json
import os
from datetime import timedelta

def load_json(file_path, default):
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(file_path, data):
    tmp = file_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, file_path)

def load_reminder_rules(config_file):
    if not os.path.exists(config_file):
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            raw_config = json.load(f)
        rules = {key: timedelta(**value) for key, value in raw_config.items()}
        # Sort by threshold descending
        return dict(sorted(rules.items(), key=lambda x: x[1], reverse=True))
    except Exception as e:
        print(f"Error loading reminder config: {e}")
        return {}
