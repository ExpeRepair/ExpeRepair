import os
import tempfile
import shutil
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test.utils import override_settings
import django

# Configure Django settings
if not settings.configured:
    settings.configure(
        DEBUG=True,
        USE_TZ=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': 'test.db',
            }
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        SECRET_KEY='test-secret-key',
        MEDIA_ROOT=tempfile.mkdtemp(),
    )

django.setup()

def test_file_permissions():
    """Test file upload permissions consistency"""
    
    # Test 1: Regular file creation (baseline)
    print("### Test 1:")
    print("Input:")
    print("Regular file creation to show baseline permissions")
    
    try:
        regular_file_path = os.path.join(settings.MEDIA_ROOT, "regular_file.txt")
        with open(regular_file_path, 'w') as f:
            f.write("regular file content")
        
        regular_stat = os.stat(regular_file_path)
        regular_permissions = oct(regular_stat.st_mode & 0o777)
        
        print("Output:")
        print(f"Regular file permissions: {regular_permissions}")
        
        # Clean up
        os.remove(regular_file_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 2: Tempfile permissions (shows the root cause)
    print("### Test 2:")
    print("Input:")
    print("NamedTemporaryFile creation to show restrictive permissions")
    
    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_file.write(b"temp file content")
        temp_file.close()
        
        temp_stat = os.stat(temp_file.name)
        temp_permissions = oct(temp_stat.st_mode & 0o777)
        
        print("Output:")
        print(f"Temp file permissions: {temp_permissions}")
        
        # Clean up
        os.remove(temp_file.name)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 3: Simulate Django's TemporaryUploadedFile behavior without FILE_UPLOAD_PERMISSIONS
    print("### Test 3:")
    print("Input:")
    print("Simulate TemporaryUploadedFile upload without FILE_UPLOAD_PERMISSIONS")
    
    try:
        storage = FileSystemStorage()
        
        # Create a temp file with restrictive permissions (simulating Django's behavior)
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        content = b"simulated temporary upload content"
        temp_file.write(content)
        temp_file.close()
        
        temp_stat = os.stat(temp_file.name)
        temp_permissions = oct(temp_stat.st_mode & 0o777)
        
        # Create a custom file-like object that simulates TemporaryUploadedFile
        class MockTemporaryUploadedFile:
            def __init__(self, temp_path, name):
                self.temporary_file_path = temp_path
                self.name = name
                self._file = open(temp_path, 'rb')
            
            def read(self, size=-1):
                return self._file.read(size)
            
            def close(self):
                self._file.close()
        
        mock_uploaded_file = MockTemporaryUploadedFile(temp_file.name, "temp_upload.txt")
        
        # Save through FileSystemStorage (this will use os.rename, preserving permissions)
        saved_name = storage.save("temp_upload.txt", mock_uploaded_file)
        saved_path = storage.path(saved_name)
        
        final_stat = os.stat(saved_path)
        final_permissions = oct(final_stat.st_mode & 0o777)
        
        print("Output:")
        print(f"Temp file permissions: {temp_permissions}")
        print(f"Final file permissions: {final_permissions}")
        print(f"Permissions preserved from temp: {temp_permissions == final_permissions}")
        
        # Clean up
        mock_uploaded_file.close()
        if os.path.exists(saved_path):
            os.remove(saved_path)
        if os.path.exists(temp_file.name):
            os.remove(temp_file.name)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 4: With explicit FILE_UPLOAD_PERMISSIONS setting
    print("### Test 4:")
    print("Input:")
    print("Simulate TemporaryUploadedFile upload with FILE_UPLOAD_PERMISSIONS=0o644")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            # Create a temp file with restrictive permissions
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            content = b"permission test content"
            temp_file.write(content)
            temp_file.close()
            
            temp_stat = os.stat(temp_file.name)
            temp_permissions = oct(temp_stat.st_mode & 0o777)
            
            # Create mock file object
            class MockTemporaryUploadedFile:
                def __init__(self, temp_path, name):
                    self.temporary_file_path = temp_path
                    self.name = name
                    self._file = open(temp_path, 'rb')
                
                def read(self, size=-1):
                    return self._file.read(size)
                
                def close(self):
                    self._file.close()
            
            mock_uploaded_file = MockTemporaryUploadedFile(temp_file.name, "permission_test.txt")
            
            # Save through FileSystemStorage
            saved_name = storage.save("permission_test.txt", mock_uploaded_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print("Output:")
            print(f"Temp file permissions: {temp_permissions}")
            print(f"Final file permissions: {final_permissions}")
            print(f"Expected permissions: 0o644")
            print(f"Correctly set to expected: {final_permissions == '0o644'}")
            
            # Clean up
            mock_uploaded_file.close()
            if os.path.exists(saved_path):
                os.remove(saved_path)
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 5: SimpleUploadedFile for comparison (in-memory, no temp file)
    print("### Test 5:")
    print("Input:")
    print("SimpleUploadedFile upload without FILE_UPLOAD_PERMISSIONS (no temp file)")
    
    try:
        storage = FileSystemStorage()
        
        content = b"simple upload content"
        simple_file = SimpleUploadedFile("simple_upload.txt", content, content_type="text/plain")
        
        # Save through FileSystemStorage
        saved_name = storage.save("simple_upload.txt", simple_file)
        saved_path = storage.path(saved_name)
        
        final_stat = os.stat(saved_path)
        final_permissions = oct(final_stat.st_mode & 0o777)
        
        print("Output:")
        print(f"SimpleUploadedFile final permissions: {final_permissions}")
        print(f"No temp file involved - uses default file creation permissions")
        
        # Clean up
        if os.path.exists(saved_path):
            os.remove(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")

if __name__ == "__main__":
    test_file_permissions()
    
    # Clean up
    if os.path.exists('test.db'):
        os.remove('test.db')
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
