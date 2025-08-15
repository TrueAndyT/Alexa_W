#!/usr/bin/env python3
"""Test script for Logger service - starts and tests it independently."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_logger_service():
    """Test Logger service independently."""
    print("\n" + "="*60)
    print("TESTING LOGGER SERVICE")
    print("="*60)
    
    service_process = None
    
    try:
        # Kill any existing logger service
        print("\n1. Killing any existing logger service...")
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(1)
        
        # Start the logger service
        print("\n2. Starting logger service...")
        venv_python = Path('.venv/bin/python').absolute()
        service_script = Path('services/logger/logger_service.py').absolute()
        
        log_file = open('test_logger.log', 'w')
        service_process = subprocess.Popen(
            [str(venv_python), str(service_script)],
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        
        print(f"   Started with PID: {service_process.pid}")
        
        # Wait for service to initialize
        print("\n3. Waiting for service to initialize...")
        time.sleep(3)
        
        # Check health
        print("\n4. Checking health status...")
        health_client = HealthClient(port=5001)
        
        for i in range(10):
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
        channel = grpc.insecure_channel('127.0.0.1:5001')
        stub = services_pb2_grpc.LoggerServiceStub(channel)
        
        # Test NewDialog
        print("   - Testing NewDialog...")
        response = stub.NewDialog(services_pb2.NewDialogRequest(
            timestamp_ms=int(time.time() * 1000)
        ))
        print(f"     Dialog ID: {response.dialog_id}")
        
        # Test WriteApp
        print("   - Testing WriteApp...")
        response = stub.WriteApp(services_pb2.AppLogRequest(
            service="test",
            event="test_event", 
            message="Test message from logger test",
            level="INFO",
            timestamp_ms=int(time.time() * 1000)
        ))
        print(f"     Success: {response.success}")
        
        print("\n✓ LOGGER SERVICE TEST PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ LOGGER SERVICE TEST FAILED: {e}")
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
        if Path('test_logger.log').exists():
            print("\n7. Last log lines:")
            with open('test_logger.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_logger_service()
    sys.exit(0 if success else 1)
