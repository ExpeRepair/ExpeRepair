import os
import tempfile
import shutil
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile, InMemoryUploadedFile
from django.core.files.uploadhandler import MemoryFileUploadHandler, TemporaryFileUploadHandler
from django.test.utils import override_settings
from io import BytesIO
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
        FILE_UPLOAD_MAX_MEMORY_SIZE=2621440,  # 2.5MB default
    )

django.setup()

def cleanup_file(filepath):
    """Helper to safely remove files"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except:
        pass

def get_file_permissions(filepath):
    """Helper to get file permissions"""
    try:
        stat_info = os.stat(filepath)
        return oct(stat_info.st_mode & 0o777)
    except:
        return "N/A"

def test_file_upload_permissions():
    """Comprehensive test suite for file upload permissions"""
    
    # Test 1: Small file upload (in-memory) without FILE_UPLOAD_PERMISSIONS
    print("### Test 1:")
    print("Input:")
    print("Small file upload (512 bytes) - should use InMemoryUploadedFile")
    
    try:
        storage = FileSystemStorage()
        small_content = b"x" * 512  # Small file, should stay in memory
        small_file = SimpleUploadedFile("small_file.txt", small_content, content_type="text/plain")
        
        saved_name = storage.save("small_test.txt", small_file)
        saved_path = storage.path(saved_name)
        
        permissions = get_file_permissions(saved_path)
        file_size = os.path.getsize(saved_path)
        
        print("Output:")
        print(f"File size: {file_size} bytes")
        print(f"File permissions: {permissions}")
        print(f"Upload type: InMemoryUploadedFile (small)")
        
        cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 2: Large file upload (temporary file) without FILE_UPLOAD_PERMISSIONS
    print("### Test 2:")
    print("Input:")
    print("Large file upload (3MB) - should use TemporaryUploadedFile")
    
    try:
        storage = FileSystemStorage()
        large_content = b"x" * (3 * 1024 * 1024)  # 3MB file, should go to temp file
        large_file = SimpleUploadedFile("large_file.txt", large_content, content_type="text/plain")
        
        # Simulate what happens with TemporaryUploadedFile
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_file.write(large_content)
        temp_file.close()
        
        temp_permissions = get_file_permissions(temp_file.name)
        
        # Now save it through storage
        with open(temp_file.name, 'rb') as f:
            saved_name = storage.save("large_test.txt", f)
            saved_path = storage.path(saved_name)
        
        final_permissions = get_file_permissions(saved_path)
        file_size = os.path.getsize(saved_path)
        
        print("Output:")
        print(f"File size: {file_size} bytes")
        print(f"Temp file permissions: {temp_permissions}")
        print(f"Final file permissions: {final_permissions}")
        print(f"Upload type: TemporaryUploadedFile (large)")
        
        cleanup_file(temp_file.name)
        cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 3: FILE_UPLOAD_PERMISSIONS set to 0o644
    print("### Test 3:")
    print("Input:")
    print("File upload with FILE_UPLOAD_PERMISSIONS=0o644")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            content = b"test content with permissions"
            test_file = SimpleUploadedFile("perm_test.txt", content, content_type="text/plain")
            
            saved_name = storage.save("perm_test.txt", test_file)
            saved_path = storage.path(saved_name)
            
            permissions = get_file_permissions(saved_path)
            
            print("Output:")
            print(f"Expected permissions: 0o644")
            print(f"Actual permissions: {permissions}")
            print(f"Permissions match expected: {permissions == '0o644'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 4: FILE_UPLOAD_PERMISSIONS set to 0o600 (restrictive)
    print("### Test 4:")
    print("Input:")
    print("File upload with FILE_UPLOAD_PERMISSIONS=0o600 (restrictive)")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o600):
            storage = FileSystemStorage()
            content = b"restrictive permissions test"
            test_file = SimpleUploadedFile("restrictive_test.txt", content, content_type="text/plain")
            
            saved_name = storage.save("restrictive_test.txt", test_file)
            saved_path = storage.path(saved_name)
            
            permissions = get_file_permissions(saved_path)
            
            print("Output:")
            print(f"Expected permissions: 0o600")
            print(f"Actual permissions: {permissions}")
            print(f"Permissions match expected: {permissions == '0o600'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 5: FILE_UPLOAD_PERMISSIONS set to 0o755 (permissive)
    print("### Test 5:")
    print("Input:")
    print("File upload with FILE_UPLOAD_PERMISSIONS=0o755 (permissive)")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o755):
            storage = FileSystemStorage()
            content = b"permissive permissions test"
            test_file = SimpleUploadedFile("permissive_test.txt", content, content_type="text/plain")
            
            saved_name = storage.save("permissive_test.txt", test_file)
            saved_path = storage.path(saved_name)
            
            permissions = get_file_permissions(saved_path)
            
            print("Output:")
            print(f"Expected permissions: 0o755")
            print(f"Actual permissions: {permissions}")
            print(f"Permissions match expected: {permissions == '0o755'}")
            
            cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 6: Empty file upload
    print("### Test 6:")
    print("Input:")
    print("Empty file upload - edge case")
    
    try:
        storage = FileSystemStorage()
        empty_content = b""
        empty_file = SimpleUploadedFile("empty_file.txt", empty_content, content_type="text/plain")
        
        saved_name = storage.save("empty_test.txt", empty_file)
        saved_path = storage.path(saved_name)
        
        permissions = get_file_permissions(saved_path)
        file_size = os.path.getsize(saved_path)
        
        print("Output:")
        print(f"File size: {file_size} bytes")
        print(f"File permissions: {permissions}")
        print(f"Empty file handled successfully: {file_size == 0}")
        
        cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 7: Binary file upload
    print("### Test 7:")
    print("Input:")
    print("Binary file upload (simulated image data)")
    
    try:
        storage = FileSystemStorage()
        # Simulate binary image data
        binary_content = bytes([i % 256 for i in range(1024)])  # 1KB of binary data
        binary_file = SimpleUploadedFile("test_image.jpg", binary_content, content_type="image/jpeg")
        
        saved_name = storage.save("binary_test.jpg", binary_file)
        saved_path = storage.path(saved_name)
        
        permissions = get_file_permissions(saved_path)
        file_size = os.path.getsize(saved_path)
        
        print("Output:")
        print(f"File size: {file_size} bytes")
        print(f"File permissions: {permissions}")
        print(f"Content type: image/jpeg")
        print(f"Binary file handled successfully: {file_size == 1024}")
        
        cleanup_file(saved_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")
    
    print()
    
    # Test 8: Consistency test between small and large files
    print("### Test 8:")
    print("Input:")
    print("Consistency test: comparing permissions between small and large files")
    
    try:
        with override_settings(FILE_UPLOAD_PERMISSIONS=0o644):
            storage = FileSystemStorage()
            
            # Small file
            small_content = b"small" * 100  # 500 bytes
            small_file = SimpleUploadedFile("small_consistency.txt", small_content, content_type="text/plain")
            small_saved = storage.save("small_consistency.txt", small_file)
            small_path = storage.path(small_saved)
            small_permissions = get_file_permissions(small_path)
            
            # Large file (simulated temporary file behavior)
            large_content = b"large" * 100000  # ~500KB
            large_file = SimpleUploadedFile("large_consistency.txt", large_content, content_type="text/plain")
            large_saved = storage.save("large_consistency.txt", large_file)
            large_path = storage.path(large_saved)
            large_permissions = get_file_permissions(large_path)
            
            print("Output:")
            print(f"Small file permissions: {small_permissions}")
            print(f"Large file permissions: {large_permissions}")
            print(f"Permissions are consistent: {small_permissions == large_permissions}")
            print(f"Both match expected 0o644: {small_permissions == '0o644' and large_permissions == '0o644'}")
            
            cleanup_file(small_path)
            cleanup_file(large_path)
        
    except Exception as e:
        print("Output:")
        print(f"Error: {e}")

if __name__ == "__main__":
    test_file_upload_permissions()
    
    # Clean up
    if os.path.exists('test.db'):
        os.remove('test.db')
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
