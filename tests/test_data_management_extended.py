import pytest
import os
from tools.local.data_manager import save_json, load_json

def test_add_group_logic(tmp_path):
    groups_file = tmp_path / "groups.json"
    state = {'groups': set()}
    config = {'GROUPS_FILE': str(groups_file)}
    
    # Simulate add_group logic
    group_id = -100123456789
    if group_id not in state['groups']:
        state['groups'].add(group_id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        
    loaded_groups = load_json(str(groups_file), [])
    assert group_id in loaded_groups
    assert len(loaded_groups) == 1

def test_remove_group_logic(tmp_path):
    groups_file = tmp_path / "groups.json"
    group_id = -100123456789
    save_json(str(groups_file), [group_id])
    
    state = {'groups': {group_id}}
    config = {'GROUPS_FILE': str(groups_file)}
    
    # Simulate remove_group logic
    if group_id in state['groups']:
        state['groups'].discard(group_id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        
    loaded_groups = load_json(str(groups_file), [])
    assert group_id not in loaded_groups
    assert len(loaded_groups) == 0
