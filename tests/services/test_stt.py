#!/usr/bin/env python3
"""Test script for STT service - starts and tests it independently."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_stt_service():
    """Test STT service independently."""
    print("\n" + "="*60)
    print("TESTING STT SERVICE")
    print("="*60)
    
    service_process = None
    
    try:
        # Kill any existing stt service
        print("\n1. Killing any existing stt service...")
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        time.sleep(1)
        
        # Check GPU memory before starting
        print("\n2. Checking GPU memory...")
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            free_mem = int(result.stdout.strip())
            print(f"   Free GPU memory: {free_mem} MB")
            if free_mem < 2000:
                print("   ⚠ Warning: Low GPU memory, STT may fail to load Whisper model")
        
        # Start the stt service
        print("\n3. Starting stt service...")
        venv_python = Path('.venv/bin/python').absolute()
        service_script = Path('services/stt/stt_service.py').absolute()
        
        log_file = open('test_stt.log', 'w')
        service_process = subprocess.Popen(
            [str(venv_python), str(service_script)],
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        
        print(f"   Started with PID: {service_process.pid}")
        
        # Wait for service to initialize
        print("\n4. Waiting for service to initialize (loading Whisper model ~1.5GB)...")
        time.sleep(10)  # STT needs time to load Whisper model
        
        # Check health
        print("\n5. Checking health status...")
        health_client = HealthClient(port=5004)
        
        for i in range(30):  # Give more time for model loading
            status = health_client.check()
            print(f"   Attempt {i+1}: {status}")
            if status == "SERVING":
                print("   ✓ Service is healthy!")
                break
            time.sleep(2)
        else:
            print("   ✗ Service failed to become healthy")
            return False
        
        # Test gRPC connection
        print("\n6. Testing gRPC methods...")
        channel = grpc.insecure_channel('127.0.0.1:5004')
        stub = services_pb2_grpc.SttServiceStub(channel)
        
        # Test Start
        print("   - Testing Start...")
        response = stub.Start(services_pb2.StartRequest(
            dialog_id="test_dialog",
            turn_number=1
        ))
        print(f"     Success: {response.success}")
        
        # Test Stop
        print("   - Testing Stop...")
        response = stub.Stop(services_pb2.StopRequest(
            dialog_id="test_dialog"
        ))
        print(f"     Success: {response.success}")
        
        # Note: STT service doesn't have a GetState method
        # Status is tracked internally through Start/Stop
        
        print("\n✓ STT SERVICE TEST PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ STT SERVICE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if service_process:
            print("\n7. Stopping service...")
            service_process.terminate()
            try:
                service_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                service_process.kill()
            print("   Service stopped")
        
        # Show last log lines
        if Path('test_stt.log').exists():
            print("\n8. Last log lines:")
            with open('test_stt.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-40:]:  # Show more lines for debugging
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_stt_service()
    sys.exit(0 if success else 1)
