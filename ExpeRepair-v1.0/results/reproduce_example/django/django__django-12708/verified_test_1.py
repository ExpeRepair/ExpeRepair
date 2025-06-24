import os
import django
from django.conf import settings
from django.db import models, migrations, connection
from django.db.migrations.operations import AlterIndexTogether, AlterUniqueTogether
from django.db.migrations.state import ProjectState
from django.test import TransactionTestCase

# Configure Django settings
if not settings.configured:
    settings.configure(
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
        USE_TZ=True,
    )
    django.setup()

class IndexTogetherUniqueTogetherTest(TransactionTestCase):
    """Test cases for the bug where deleting index_together fails when unique_together exists on same fields"""
    
    def setUp(self):
        # Clean up any existing tables
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'test_%'")
            tables = cursor.fetchall()
            for table in tables:
                cursor.execute(f"DROP TABLE IF EXISTS {table[0]}")
    
    def create_test_model_state(self, app_label, unique_together=None, index_together=None):
        """Helper to create a model state with specified unique_together and index_together"""
        fields = [
            ("id", models.AutoField(primary_key=True)),
            ("field1", models.CharField(max_length=100)),
            ("field2", models.CharField(max_length=100)),
            ("field3", models.CharField(max_length=100)),
        ]
        
        options = {}
        if unique_together:
            options['unique_together'] = unique_together
        if index_together:
            options['index_together'] = index_together
            
        project_state = ProjectState()
        project_state.add_model(
            models.state.ModelState(
                app_label=app_label,
                name="TestModel",
                fields=fields,
                options=options,
            )
        )
        return project_state
    
    def apply_create_model(self, project_state, app_label):
        """Apply the CreateModel operation to the database"""
        model_state = project_state.models[(app_label, 'testmodel')]
        create_operation = migrations.CreateModel(
            name="TestModel",
            fields=model_state.fields,
            options=model_state.options,
        )
        
        new_state = project_state.clone()
        with connection.schema_editor() as editor:
            create_operation.database_forwards(app_label, editor, ProjectState(), new_state)
        return new_state

def run_test_case(test_name, test_func):
    """Run a single test case with error handling"""
    print(f"### {test_name}:")
    try:
        test_func()
    except Exception as e:
        print(f"Output:\nError: {type(e).__name__}: {str(e)}")
    print()

def test_1():
    """Test basic case: model with both unique_together and index_together on same fields"""
    print("Input:\nModel with unique_together=[('field1', 'field2')] and index_together=[('field1', 'field2')]")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    # Create initial state with both constraints
    project_state = test.create_test_model_state(
        'test_app1',
        unique_together=[('field1', 'field2')],
        index_together=[('field1', 'field2')]
    )
    
    # Apply model creation
    current_state = test.apply_create_model(project_state, 'test_app1')
    
    # Try to remove index_together
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app1', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app1', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_2():
    """Test case: multiple fields in constraints"""
    print("Input:\nModel with unique_together=[('field1', 'field2', 'field3')] and index_together=[('field1', 'field2', 'field3')]")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app2',
        unique_together=[('field1', 'field2', 'field3')],
        index_together=[('field1', 'field2', 'field3')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app2')
    
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app2', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app2', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_3():
    """Test case: multiple index_together entries, removing one that overlaps with unique_together"""
    print("Input:\nModel with unique_together=[('field1', 'field2')] and index_together=[('field1', 'field2'), ('field2', 'field3')]")
    print("Operation: Remove ('field1', 'field2') from index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app3',
        unique_together=[('field1', 'field2')],
        index_together=[('field1', 'field2'), ('field2', 'field3')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app3')
    
    # Remove only the overlapping index
    operation = AlterIndexTogether('TestModel', [('field2', 'field3')])
    new_state = current_state.clone()
    operation.state_forwards('test_app3', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app3', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_4():
    """Test case: removing unique_together while keeping index_together (should work)"""
    print("Input:\nModel with unique_together=[('field1', 'field2')] and index_together=[('field1', 'field2')]")
    print("Operation: Remove unique_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app4',
        unique_together=[('field1', 'field2')],
        index_together=[('field1', 'field2')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app4')
    
    operation = AlterUniqueTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app4', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app4', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_5():
    """Test case: different field orders in constraints"""
    print("Input:\nModel with unique_together=[('field1', 'field2')] and index_together=[('field2', 'field1')]")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app5',
        unique_together=[('field1', 'field2')],
        index_together=[('field2', 'field1')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app5')
    
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app5', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app5', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_6():
    """Test case: multiple unique_together entries"""
    print("Input:\nModel with unique_together=[('field1', 'field2'), ('field1', 'field3')] and index_together=[('field1', 'field2')]")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app6',
        unique_together=[('field1', 'field2'), ('field1', 'field3')],
        index_together=[('field1', 'field2')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app6')
    
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app6', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app6', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_7():
    """Test case: only index_together, no unique_together (should work normally)"""
    print("Input:\nModel with index_together=[('field1', 'field2')] only")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app7',
        unique_together=None,
        index_together=[('field1', 'field2')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app7')
    
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app7', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app7', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

def test_8():
    """Test case: partial overlap between unique_together and index_together"""
    print("Input:\nModel with unique_together=[('field1', 'field2')] and index_together=[('field1', 'field3')]")
    print("Operation: Remove index_together")
    
    test = IndexTogetherUniqueTogetherTest()
    test.setUp()
    
    project_state = test.create_test_model_state(
        'test_app8',
        unique_together=[('field1', 'field2')],
        index_together=[('field1', 'field3')]
    )
    
    current_state = test.apply_create_model(project_state, 'test_app8')
    
    operation = AlterIndexTogether('TestModel', [])
    new_state = current_state.clone()
    operation.state_forwards('test_app8', new_state)
    
    with connection.schema_editor() as editor:
        operation.database_forwards('test_app8', editor, current_state, new_state)
    
    print("Output:\nOperation completed successfully")

if __name__ == '__main__':
    # Run all test cases
    run_test_case("Test 1", test_1)
    run_test_case("Test 2", test_2)
    run_test_case("Test 3", test_3)
    run_test_case("Test 4", test_4)
    run_test_case("Test 5", test_5)
    run_test_case("Test 6", test_6)
    run_test_case("Test 7", test_7)
    run_test_case("Test 8", test_8)
