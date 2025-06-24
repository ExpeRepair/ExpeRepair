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
        FILE_UPLOAD_MAX_MEMORY_SIZE=1024,  # Small size to force temporary files
    )

django.setup()

def cleanup_file(filepath):
    """Helper to safely cleanup files"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except:
        pass

def test_file_upload_permissions():
    """Comprehensive test suite for file upload permissions"""
    
    print("### Test 1:")
    print("Input:")
    print("Test default FILE_UPLOAD_PERMISSIONS behavior with TemporaryUploadedFile")
    print("Output:")
    
    try:
        # Test without FILE_UPLOAD_PERMISSIONS set (should use system default)
        storage = FileSystemStorage()
        
        # Create large content to force TemporaryUploadedFile usage
        large_content = b"x" * 2048  # Larger than FILE_UPLOAD_MAX_MEMORY_SIZE
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_file.write(large_content)
        temp_file.close()
        
        # Simulate TemporaryUploadedFile
        class MockTemporaryUploadedFile:
            def __init__(self, temp_path, name):
                self.temporary_file_path = temp_path
                self.name = name
                self._file = open(temp_path, 'rb')
            
            def read(self, size=-1):
                return self._file.read(size)
            
            def close(self):
                self._file.close()
        
        mock_file = MockTemporaryUploadedFile(temp_file.name, "test1.txt")
        saved_name = storage.save("test1.txt", mock_file)
        saved_path = storage.path(saved_name)
        
        final_stat = os.stat(saved_path)
        final_permissions = oct(final_stat.st_mode & 0o777)
        
        print(f"TemporaryUploadedFile permissions without setting: {final_permissions}")
        print(f"FILE_UPLOAD_PERMISSIONS setting: {getattr(settings, 'FILE_UPLOAD_PERMISSIONS', 'Not set')}")
        
        mock_file.close()
        cleanup_file(saved_path)
        cleanup_file(temp_file.name)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 2:")
    print("Input:")
    print("Test FILE_UPLOAD_PERMISSIONS=0o644 with TemporaryUploadedFile")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            large_content = b"y" * 2048
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            temp_file.write(large_content)
            temp_file.close()
            
            class MockTemporaryUploadedFile:
                def __init__(self, temp_path, name):
                    self.temporary_file_path = temp_path
                    self.name = name
                    self._file = open(temp_path, 'rb')
                
                def read(self, size=-1):
                    return self._file.read(size)
                
                def close(self):
                    self._file.close()
            
            mock_file = MockTemporaryUploadedFile(temp_file.name, "test2.txt")
            saved_name = storage.save("test2.txt", mock_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print(f"TemporaryUploadedFile permissions with 0o644: {final_permissions}")
            print(f"Expected: 0o644, Got: {final_permissions}, Match: {final_permissions == '0o644'}")
            
            mock_file.close()
            cleanup_file(saved_path)
            cleanup_file(temp_file.name)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 3:")
    print("Input:")
    print("Test FILE_UPLOAD_PERMISSIONS=0o600 (restrictive permissions)")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o600):
            storage = FileSystemStorage()
            
            content = b"restrictive content"
            simple_file = SimpleUploadedFile("test3.txt", content)
            
            saved_name = storage.save("test3.txt", simple_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print(f"Restrictive permissions (0o600): {final_permissions}")
            print(f"Expected: 0o600, Got: {final_permissions}, Match: {final_permissions == '0o600'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 4:")
    print("Input:")
    print("Test FILE_UPLOAD_PERMISSIONS=0o755 (permissive permissions)")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o755):
            storage = FileSystemStorage()
            
            content = b"permissive content"
            simple_file = SimpleUploadedFile("test4.txt", content)
            
            saved_name = storage.save("test4.txt", simple_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print(f"Permissive permissions (0o755): {final_permissions}")
            print(f"Expected: 0o755, Got: {final_permissions}, Match: {final_permissions == '0o755'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 5:")
    print("Input:")
    print("Test consistency between SimpleUploadedFile and TemporaryUploadedFile with same setting")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            # Test SimpleUploadedFile
            simple_content = b"simple content"
            simple_file = SimpleUploadedFile("simple.txt", simple_content)
            simple_saved = storage.save("simple.txt", simple_file)
            simple_path = storage.path(simple_saved)
            simple_permissions = oct(os.stat(simple_path).st_mode & 0o777)
            
            # Test TemporaryUploadedFile simulation
            temp_content = b"z" * 2048
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            temp_file.write(temp_content)
            temp_file.close()
            
            class MockTemporaryUploadedFile:
                def __init__(self, temp_path, name):
                    self.temporary_file_path = temp_path
                    self.name = name
                    self._file = open(temp_path, 'rb')
                
                def read(self, size=-1):
                    return self._file.read(size)
                
                def close(self):
                    self._file.close()
            
            mock_file = MockTemporaryUploadedFile(temp_file.name, "temp.txt")
            temp_saved = storage.save("temp.txt", mock_file)
            temp_path = storage.path(temp_saved)
            temp_permissions = oct(os.stat(temp_path).st_mode & 0o777)
            
            print(f"SimpleUploadedFile permissions: {simple_permissions}")
            print(f"TemporaryUploadedFile permissions: {temp_permissions}")
            print(f"Permissions consistent: {simple_permissions == temp_permissions}")
            
            mock_file.close()
            cleanup_file(simple_path)
            cleanup_file(temp_path)
            cleanup_file(temp_file.name)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 6:")
    print("Input:")
    print("Test FILE_UPLOAD_PERMISSIONS=None (explicitly set to None)")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=None):
            storage = FileSystemStorage()
            
            content = b"null permission content"
            simple_file = SimpleUploadedFile("test6.txt", content)
            
            saved_name = storage.save("test6.txt", simple_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print(f"Permissions with None setting: {final_permissions}")
            print(f"FILE_UPLOAD_PERMISSIONS: {settings.FILE_UPLOAD_PERMISSIONS}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 7:")
    print("Input:")
    print("Test custom FileSystemStorage with file_permissions_mode override")
    print("Output:")
    
    try:
        # Test storage-level permission override
        custom_storage = FileSystemStorage(file_permissions_mode=0o600)
        
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            content = b"custom storage content"
            simple_file = SimpleUploadedFile("test7.txt", content)
            
            saved_name = custom_storage.save("test7.txt", simple_file)
            saved_path = custom_storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            
            print(f"Storage-level override permissions: {final_permissions}")
            print(f"Global setting: 0o644, Storage override: 0o600")
            print(f"Uses storage override: {final_permissions == '0o600'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print(f"Error: {e}")
    
    print()
    
    print("### Test 8:")
    print("Input:")
    print("Test edge case with zero-byte file")
    print("Output:")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            # Zero-byte file
            empty_content = b""
            empty_file = SimpleUploadedFile("empty.txt", empty_content)
            
            saved_name = storage.save("empty.txt", empty_file)
            saved_path = storage.path(saved_name)
            
            final_stat = os.stat(saved_path)
            final_permissions = oct(final_stat.st_mode & 0o777)
            file_size = final_stat.st_size
            
            print(f"Zero-byte file permissions: {final_permissions}")
            print(f"File size: {file_size} bytes")
            print(f"Permissions set correctly: {final_permissions == '0o644'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    test_file_upload_permissions()
    
    # Clean up
    if os.path.exists('test.db'):
        os.remove('test.db')
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
