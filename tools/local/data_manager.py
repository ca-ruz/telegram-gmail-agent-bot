import json
import os
import logging
from datetime import timedelta

# Initialize logger for this module
logger = logging.getLogger(__name__)

def load_json(file_path, default):
    """
    Loads data from a JSON file.
    
    Args:
        file_path (str): Path to the JSON file.
        default: The value to return if the file does not exist or is invalid.
        
    Returns:
        The parsed JSON data or the default value.
    """
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading JSON from {file_path}: {e}")
        return default

def save_json(file_path, data):
    """
    Saves data to a JSON file atomically using a temporary file.
    
    Args:
        file_path (str): Path to the destination JSON file.
        data: The data to serialize to JSON.
    """
    tmp = file_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # os.replace is atomic on most systems, preventing file corruption
        os.replace(tmp, file_path)
    except Exception as e:
        logger.error(f"Error saving JSON to {file_path}: {e}")

def load_reminder_rules(config_file):
    """
    Loads and parses reminder thresholds from a configuration file.
    
    Args:
        config_file (str): Path to the reminder_config.json file.
        
    Returns:
        dict: A dictionary mapping threshold names to timedelta objects, 
              sorted by duration descending.
    """
    if not os.path.exists(config_file):
        logger.warning(f"Reminder config file {config_file} not found.")
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            raw_config = json.load(f)
        
        # Convert dictionary values into actual Python timedelta objects
        rules = {key: timedelta(**value) for key, value in raw_config.items()}
        
        # Sort by threshold descending so we can check larger windows first if needed
        return dict(sorted(rules.items(), key=lambda x: x[1], reverse=True))
    except Exception as e:
        logger.error(f"Error loading reminder config: {e}")
        return {}
