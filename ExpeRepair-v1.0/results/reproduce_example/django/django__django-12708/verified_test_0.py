#!/usr/bin/env python3

"""
Test script for Django migration issue: 
Migration crashes deleting an index_together if there is a unique_together on the same fields
"""

import os
import sys
import django
from django.conf import settings
from django.test.utils import get_runner
from django.db import connection, migrations, models
from django.db.migrations.migration import Migration
from django.db.migrations.state import ProjectState
from django.core.management import execute_from_command_line

# Configure Django settings
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        SECRET_KEY='test-secret-key',
        USE_TZ=True,
    )

django.setup()

def run_test_input(test_num, description, test_func):
    """Run a single test input safely"""
    print(f"### Test {test_num}:")
    print(f"Input:")
    print(f"{description}")
    print("Output:")
    
    try:
        result = test_func()
        print(result)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)}")
    print()

def test_basic_issue_scenario():
    """Test the basic scenario described in the issue"""
    app_label = "test_basic_issue"
    
    # Create initial state with both unique_together and index_together on same fields
    project_state = ProjectState()
    
    # Create model with both constraints
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2')],
            'index_together': [('field1', 'field2')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    # Try to delete index_together
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    # Test database operations
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        # This should fail according to the issue
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully created model with both constraints and removed index_together"

def test_reverse_scenario():
    """Test removing unique_together while keeping index_together"""
    app_label = "test_reverse"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2')],
            'index_together': [('field1', 'field2')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    # Try to delete unique_together
    operation2 = migrations.AlterUniqueTogether(
        "TestModel", 
        unique_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully removed unique_together while keeping index_together"

def test_single_field_constraint():
    """Test with single field constraints"""
    app_label = "test_single_field"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1',)],
            'index_together': [('field1',)],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully handled single field constraint scenario"

def test_multiple_constraint_sets():
    """Test with multiple different constraint sets"""
    app_label = "test_multiple"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
            ("field3", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2'), ('field2', 'field3')],
            'index_together': [('field1', 'field2'), ('field1', 'field3')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    # Remove one overlapping index
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[('field1', 'field3')]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully handled multiple constraint sets"

def test_no_overlap_scenario():
    """Test when unique_together and index_together don't overlap"""
    app_label = "test_no_overlap"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
            ("field3", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2')],
            'index_together': [('field2', 'field3')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully handled non-overlapping constraints"

def test_three_field_constraint():
    """Test with three-field constraints"""
    app_label = "test_three_field"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
            ("field3", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2', 'field3')],
            'index_together': [('field1', 'field2', 'field3')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully handled three-field constraint scenario"

def test_partial_overlap():
    """Test with partial field overlap between constraints"""
    app_label = "test_partial"
    
    project_state = ProjectState()
    
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
            ("field3", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2')],
            'index_together': [('field1', 'field2', 'field3')],
        }
    )
    
    new_state = project_state.clone()
    operation1.state_forwards(app_label, new_state)
    
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    final_state = new_state.clone()
    operation2.state_forwards(app_label, final_state)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, new_state)
        operation2.database_forwards(app_label, editor, new_state, final_state)
    
    return "Successfully handled partial overlap scenario"

def test_add_then_remove():
    """Test adding index_together then removing it when unique_together exists"""
    app_label = "test_add_remove"
    
    project_state = ProjectState()
    
    # Start with only unique_together
    operation1 = migrations.CreateModel(
        "TestModel",
        [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
        ],
        options={
            'unique_together': [('field1', 'field2')],
        }
    )
    
    state1 = project_state.clone()
    operation1.state_forwards(app_label, state1)
    
    # Add index_together
    operation2 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[('field1', 'field2')]
    )
    
    state2 = state1.clone()
    operation2.state_forwards(app_label, state2)
    
    # Remove index_together
    operation3 = migrations.AlterIndexTogether(
        "TestModel", 
        index_together=[]
    )
    
    state3 = state2.clone()
    operation3.state_forwards(app_label, state3)
    
    with connection.schema_editor() as editor:
        operation1.database_forwards(app_label, editor, project_state, state1)
        operation2.database_forwards(app_label, editor, state1, state2)
        operation3.database_forwards(app_label, editor, state2, state3)
    
    return "Successfully added and then removed index_together"

if __name__ == "__main__":
    print("Testing Django migration issue with index_together and unique_together")
    print("=" * 80)
    print()
    
    test_cases = [
        ("Basic issue scenario: same fields in unique_together and index_together, delete index_together", test_basic_issue_scenario),
        ("Reverse scenario: delete unique_together while keeping index_together", test_reverse_scenario),
        ("Single field constraint scenario", test_single_field_constraint),
        ("Multiple constraint sets with partial overlap", test_multiple_constraint_sets),
        ("No overlap between unique_together and index_together", test_no_overlap_scenario),
        ("Three-field constraint scenario", test_three_field_constraint),
        ("Partial field overlap between constraints", test_partial_overlap),
        ("Add index_together then remove it when unique_together exists", test_add_then_remove),
    ]
    
    for i, (description, test_func) in enumerate(test_cases, 1):
        run_test_input(i, description, test_func)
