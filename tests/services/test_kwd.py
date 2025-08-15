#!/usr/bin/env python3
"""Test script for KWD service - starts and tests it independently."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_kwd_service():
    """Test KWD service independently."""
    print("\n" + "="*60)
    print("TESTING KWD SERVICE")
    print("="*60)
    
    service_process = None
    
    try:
        # Kill any existing kwd service
        print("\n1. Killing any existing kwd service...")
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        time.sleep(1)
        
        # Start the kwd service
        print("\n2. Starting kwd service...")
        venv_python = Path('.venv/bin/python').absolute()
        service_script = Path('services/kwd/kwd_service.py').absolute()
        
        log_file = open('test_kwd.log', 'w')
        service_process = subprocess.Popen(
            [str(venv_python), str(service_script)],
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        
        print(f"   Started with PID: {service_process.pid}")
        
        # Wait for service to initialize
        print("\n3. Waiting for service to initialize (may take time to load model)...")
        time.sleep(5)  # KWD may take longer to load model
        
        # Check health
        print("\n4. Checking health status...")
        health_client = HealthClient(port=5003)
        
        for i in range(30):  # Give more time for model loading
            status = health_client.check()
            print(f"   Attempt {i+1}: {status}")
            if status == "SERVING":
                print("   ✓ Service is healthy!")
                break
            time.sleep(1)
        else:
            print("   ✗ Service failed to become healthy")
            return False
        
        # Test gRPC connection
        print("\n5. Testing gRPC methods...")
        channel = grpc.insecure_channel('127.0.0.1:5003')
        stub = services_pb2_grpc.KwdServiceStub(channel)
        
        # Test Enable
        print("   - Testing Enable...")
        response = stub.Enable(services_pb2.Empty())
        print(f"     Success: {response.success}")
        
        # Test Disable
        print("   - Testing Disable...")
        response = stub.Disable(services_pb2.Empty())
        print(f"     Success: {response.success}")
        
        # Note: KWD service doesn't have a GetState method
        # Status is implicit from Enable/Disable success
        
        print("\n✓ KWD SERVICE TEST PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ KWD SERVICE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if service_process:
            print("\n6. Stopping service...")
            service_process.terminate()
            try:
                service_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                service_process.kill()
            print("   Service stopped")
        
        # Show last log lines
        if Path('test_kwd.log').exists():
            print("\n7. Last log lines:")
            with open('test_kwd.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-30:]:  # Show more lines for debugging
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_kwd_service()
    sys.exit(0 if success else 1)
