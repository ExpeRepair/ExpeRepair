import os
import tempfile
import shutil
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test.utils import override_settings
from django.core.files.uploadhandler import MemoryFileUploadHandler, TemporaryFileUploadHandler
import django
from io import BytesIO

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
        FILE_UPLOAD_MAX_MEMORY_SIZE=2621440,  # 2.5 MB default
    )

django.setup()

def test_file_upload_permissions():
    """Comprehensive test suite for file upload permissions"""
    
    # Test 1: Default behavior - no FILE_UPLOAD_PERMISSIONS setting
    print("### Test 1:")
    print("Input:")
    print("Test default behavior without FILE_UPLOAD_PERMISSIONS setting")
    
    try:
        storage = FileSystemStorage()
        
        # Small file (uses SimpleUploadedFile/MemoryFileUploadHandler)
        small_content = b"small file content"
        small_file = SimpleUploadedFile("small.txt", small_content)
        small_saved = storage.save("small.txt", small_file)
        small_path = storage.path(small_saved)
        small_perms = oct(os.stat(small_path).st_mode & 0o777)
        
        # Large file simulation (uses TemporaryUploadedFile)
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        large_content = b"large file content" * 1000
        temp_file.write(large_content)
        temp_file.close()
        
        # Simulate TemporaryUploadedFile
        with open(temp_file.name, 'rb') as f:
            temp_upload = TemporaryUploadedFile("large.txt", "text/plain", len(large_content), "utf-8")
            temp_upload.file = f
            temp_upload.temporary_file_path = temp_file.name
            large_saved = storage.save("large.txt", temp_upload)
            large_path = storage.path(large_saved)
            large_perms = oct(os.stat(large_path).st_mode & 0o777)
        
        print("Output:")
        print(f"Small file permissions: {small_perms}")
        print(f"Large file permissions: {large_perms}")
        print(f"Permissions consistent: {small_perms == large_perms}")
        
        # Cleanup
        for path in [small_path, large_path, temp_file.name]:
            if os.path.exists(path):
                os.remove(path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 2: Explicit FILE_UPLOAD_PERMISSIONS=0o644
    print("### Test 2:")
    print("Input:")
    print("Test with FILE_UPLOAD_PERMISSIONS=0o644")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            content = b"test content for 644 permissions"
            uploaded_file = SimpleUploadedFile("test644.txt", content)
            saved_name = storage.save("test644.txt", uploaded_file)
            saved_path = storage.path(saved_name)
            permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"File permissions: {permissions}")
            print(f"Expected: 0o644")
            print(f"Correct: {permissions == '0o644'}")
            
            if os.path.exists(saved_path):
                os.remove(saved_path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 3: Restrictive permissions FILE_UPLOAD_PERMISSIONS=0o600
    print("### Test 3:")
    print("Input:")
    print("Test with restrictive FILE_UPLOAD_PERMISSIONS=0o600")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o600):
            storage = FileSystemStorage()
            
            content = b"test content for 600 permissions"
            uploaded_file = SimpleUploadedFile("test600.txt", content)
            saved_name = storage.save("test600.txt", uploaded_file)
            saved_path = storage.path(saved_name)
            permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"File permissions: {permissions}")
            print(f"Expected: 0o600")
            print(f"Correct: {permissions == '0o600'}")
            
            if os.path.exists(saved_path):
                os.remove(saved_path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 4: Permissive permissions FILE_UPLOAD_PERMISSIONS=0o666
    print("### Test 4:")
    print("Input:")
    print("Test with permissive FILE_UPLOAD_PERMISSIONS=0o666")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o666):
            storage = FileSystemStorage()
            
            content = b"test content for 666 permissions"
            uploaded_file = SimpleUploadedFile("test666.txt", content)
            saved_name = storage.save("test666.txt", uploaded_file)
            saved_path = storage.path(saved_name)
            permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"File permissions: {permissions}")
            print(f"Expected: 0o666")
            print(f"Correct: {permissions == '0o666'}")
            
            if os.path.exists(saved_path):
                os.remove(saved_path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 5: File size threshold testing - small file under memory limit
    print("### Test 5:")
    print("Input:")
    print("Test small file under FILE_UPLOAD_MAX_MEMORY_SIZE threshold")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644, FILE_UPLOAD_MAX_MEMORY_SIZE=1024):
            storage = FileSystemStorage()
            
            # File smaller than threshold (should use memory handler)
            small_content = b"x" * 512  # 512 bytes < 1024
            small_file = SimpleUploadedFile("small_threshold.txt", small_content)
            saved_name = storage.save("small_threshold.txt", small_file)
            saved_path = storage.path(saved_name)
            permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"File size: {len(small_content)} bytes")
            print(f"Memory threshold: 1024 bytes")
            print(f"File permissions: {permissions}")
            print(f"Uses memory handler: {len(small_content) < 1024}")
            
            if os.path.exists(saved_path):
                os.remove(saved_path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 6: File size threshold testing - large file over memory limit
    print("### Test 6:")
    print("Input:")
    print("Test large file over FILE_UPLOAD_MAX_MEMORY_SIZE threshold")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644, FILE_UPLOAD_MAX_MEMORY_SIZE=1024):
            storage = FileSystemStorage()
            
            # Create a temporary file larger than threshold
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            large_content = b"x" * 2048  # 2048 bytes > 1024
            temp_file.write(large_content)
            temp_file.close()
            
            # Simulate temporary file upload
            with open(temp_file.name, 'rb') as f:
                temp_upload = TemporaryUploadedFile("large_threshold.txt", "text/plain", len(large_content), "utf-8")
                temp_upload.file = f
                temp_upload.temporary_file_path = temp_file.name
                saved_name = storage.save("large_threshold.txt", temp_upload)
                saved_path = storage.path(saved_name)
                permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"File size: {len(large_content)} bytes")
            print(f"Memory threshold: 1024 bytes")
            print(f"File permissions: {permissions}")
            print(f"Uses temporary handler: {len(large_content) > 1024}")
            
            # Cleanup
            for path in [saved_path, temp_file.name]:
                if os.path.exists(path):
                    os.remove(path)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 7: Multiple files with different sizes
    print("### Test 7:")
    print("Input:")
    print("Test multiple files of different sizes with consistent permissions")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            files_data = [
                ("tiny.txt", b"tiny"),
                ("small.txt", b"small" * 100),
                ("medium.txt", b"medium" * 1000),
            ]
            
            results = []
            for filename, content in files_data:
                uploaded_file = SimpleUploadedFile(filename, content)
                saved_name = storage.save(filename, uploaded_file)
                saved_path = storage.path(saved_name)
                permissions = oct(os.stat(saved_path).st_mode & 0o777)
                results.append((filename, len(content), permissions))
                
                if os.path.exists(saved_path):
                    os.remove(saved_path)
            
            print("Output:")
            for filename, size, perms in results:
                print(f"{filename}: {size} bytes, permissions: {perms}")
            
            all_same = len(set(perms for _, _, perms in results)) == 1
            print(f"All permissions consistent: {all_same}")
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 8: Custom storage location with permissions
    print("### Test 8:")
    print("Input:")
    print("Test custom storage location with FILE_UPLOAD_PERMISSIONS")
    
    try:
        custom_dir = tempfile.mkdtemp()
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage(location=custom_dir)
            
            content = b"content for custom storage location"
            uploaded_file = SimpleUploadedFile("custom_storage.txt", content)
            saved_name = storage.save("custom_storage.txt", uploaded_file)
            saved_path = storage.path(saved_name)
            permissions = oct(os.stat(saved_path).st_mode & 0o777)
            
            print("Output:")
            print(f"Custom storage directory: {custom_dir}")
            print(f"File permissions: {permissions}")
            print(f"Expected: 0o644")
            print(f"Storage location works: {os.path.dirname(saved_path) == custom_dir}")
            
            # Cleanup
            if os.path.exists(saved_path):
                os.remove(saved_path)
            if os.path.exists(custom_dir):
                os.rmdir(custom_dir)
                
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
        # Cleanup on error
        if 'custom_dir' in locals() and os.path.exists(custom_dir):
            try:
                shutil.rmtree(custom_dir)
            except:
                pass


if __name__ == "__main__":
    test_file_upload_permissions()
    
    # Final cleanup
    if os.path.exists('test.db'):
        os.remove('test.db')
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
