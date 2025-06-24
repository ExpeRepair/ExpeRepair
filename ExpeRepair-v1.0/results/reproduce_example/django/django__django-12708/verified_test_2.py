#!/usr/bin/env python3

import os
import sys
import django
from django.conf import settings
from django.test.utils import get_runner

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
            'test_migrations',
        ],
        USE_TZ=True,
    )

django.setup()

from django.db import models, connection
from django.db.migrations.state import ProjectState
from django.db.migrations import operations as migration_ops
from django.test import TestCase

# Test inputs covering the issue scenario
test_inputs = [
    # Test 1: Basic case - unique_together and index_together on same fields, remove index_together
    {
        'description': 'Remove index_together when unique_together exists on same fields',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2')],
                'index_together': [('field1', 'field2')],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together=set(),
        )
    },
    
    # Test 2: Multiple field combinations
    {
        'description': 'Remove index_together with multiple field combinations',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2')],
                'index_together': [('field1', 'field2'), ('field2', 'field3')],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together={('field2', 'field3')},  # Remove the overlapping one
        )
    },
    
    # Test 3: Three fields together
    {
        'description': 'Three fields in both unique_together and index_together',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2', 'field3')],
                'index_together': [('field1', 'field2', 'field3')],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together=set(),
        )
    },
    
    # Test 4: Partial overlap
    {
        'description': 'Partial field overlap between unique_together and index_together',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2')],
                'index_together': [('field1', 'field3')],  # Different combination
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together=set(),
        )
    },
    
    # Test 5: Multiple unique_together with overlapping index_together
    {
        'description': 'Multiple unique_together with overlapping index_together',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
                ('field4', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2'), ('field3', 'field4')],
                'index_together': [('field1', 'field2')],  # Overlaps with first unique_together
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together=set(),
        )
    },
    
    # Test 6: Remove specific index_together while keeping others
    {
        'description': 'Remove specific index_together entry while keeping others',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
                ('field4', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2')],
                'index_together': [('field1', 'field2'), ('field3', 'field4')],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together={('field3', 'field4')},  # Keep non-overlapping, remove overlapping
        )
    },
    
    # Test 7: Single field unique_together and index_together
    {
        'description': 'Single field in both unique_together and index_together',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100, unique=False)),
                ('field2', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1',)],
                'index_together': [('field1',)],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together=set(),
        )
    },
    
    # Test 8: Complex scenario with mixed constraints
    {
        'description': 'Complex scenario with multiple overlapping constraints',
        'initial_model': {
            'fields': [
                ('id', models.AutoField(primary_key=True)),
                ('field1', models.CharField(max_length=100)),
                ('field2', models.CharField(max_length=100)),
                ('field3', models.CharField(max_length=100)),
                ('field4', models.CharField(max_length=100)),
                ('field5', models.CharField(max_length=100)),
            ],
            'options': {
                'unique_together': [('field1', 'field2'), ('field3', 'field4', 'field5')],
                'index_together': [('field1', 'field2'), ('field2', 'field3'), ('field3', 'field4', 'field5')],
            }
        },
        'operation': migration_ops.AlterIndexTogether(
            name='TestModel',
            index_together={('field2', 'field3')},  # Remove the overlapping ones
        )
    }
]

def create_test_model(app_label, model_name, model_definition):
    """Create a test model with given definition."""
    fields = model_definition['fields']
    options = model_definition.get('options', {})
    
    operation = migration_ops.CreateModel(
        name=model_name,
        fields=fields,
        options=options,
    )
    return operation

def run_test_input(test_num, test_input):
    """Run a single test input and capture results."""
    print(f"### Test {test_num}:")
    print("Input:")
    print(f"Description: {test_input['description']}")
    print(f"Initial model options: {test_input['initial_model'].get('options', {})}")
    print(f"Operation: {test_input['operation']}")
    print("Output:")
    
    try:
        app_label = f"test_app_{test_num}"
        model_name = "TestModel"
        
        # Create initial project state
        project_state = ProjectState()
        
        # Create the model
        create_operation = create_test_model(app_label, model_name, test_input['initial_model'])
        new_state = project_state.clone()
        create_operation.state_forwards(app_label, new_state)
        
        # Apply creation to database
        with connection.schema_editor() as editor:
            create_operation.database_forwards(app_label, editor, project_state, new_state)
        
        project_state = new_state
        
        # Now try to apply the problematic operation
        operation = test_input['operation']
        final_state = project_state.clone()
        operation.state_forwards(app_label, final_state)
        
        # Try to apply the operation to database - this should trigger the bug
        with connection.schema_editor() as editor:
            operation.database_forwards(app_label, editor, project_state, final_state)
        
        print("SUCCESS: Operation completed without error")
        
        # Clean up
        with connection.schema_editor() as editor:
            cleanup_operation = migration_ops.DeleteModel(model_name)
            cleanup_operation.database_forwards(app_label, editor, final_state, ProjectState())
            
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {str(e)}")
        
        # Try to clean up even if the main operation failed
        try:
            with connection.schema_editor() as editor:
                cleanup_operation = migration_ops.DeleteModel(model_name)
                cleanup_operation.database_forwards(app_label, editor, project_state, ProjectState())
        except:
            pass  # Ignore cleanup errors
    
    print()

def main():
    """Run all test inputs."""
    print("Testing Django migration issue: index_together deletion crashes when unique_together exists on same fields")
    print("=" * 100)
    print()
    
    for i, test_input in enumerate(test_inputs, 1):
        run_test_input(i, test_input)

if __name__ == "__main__":
    main()
