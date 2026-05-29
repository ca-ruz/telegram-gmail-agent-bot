import pytest
import os
import json
from tools.local.data_manager import load_json, save_json

def test_save_and_load_json(tmp_path):
    # Create a temporary file path
    test_file = tmp_path / "test.json"
    data = {"key": "value", "list": [1, 2, 3]}
    
    # Save it
    save_json(str(test_file), data)
    
    # Check if file exists
    assert os.path.exists(str(test_file))
    
    # Load it back
    loaded = load_json(str(test_file), {})
    assert loaded == data

def test_load_json_default():
    # Load a file that doesn't exist
    loaded = load_json("non_existent_file.json", {"default": True})
    assert loaded == {"default": True}

def test_load_json_invalid(tmp_path):
    # Create an invalid JSON file
    test_file = tmp_path / "invalid.json"
    with open(test_file, "w") as f:
        f.write("not json")
    
    # Load it
    loaded = load_json(str(test_file), "fallback")
    assert loaded == "fallback"
