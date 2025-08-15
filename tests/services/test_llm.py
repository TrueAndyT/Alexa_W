#!/usr/bin/env python3
"""Test script for LLM service - starts and tests it independently."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_llm_service():
    """Test LLM service independently."""
    print("\n" + "="*60)
    print("TESTING LLM SERVICE")
    print("="*60)
    
    service_process = None
    
    try:
        # Kill any existing llm service
        print("\n1. Killing any existing llm service...")
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        time.sleep(1)
        
        # Check GPU memory before starting
        print("\n2. Checking GPU memory...")
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            free_mem = int(result.stdout.strip())
            print(f"   Free GPU memory: {free_mem} MB")
            if free_mem < 4000:
                print("   ⚠ Warning: Low GPU memory, LLM may fail to load model")
        
        # Start the llm service
        print("\n3. Starting llm service...")
        venv_python = Path('.venv/bin/python').absolute()
        service_script = Path('services/llm/llm_service.py').absolute()
        
        log_file = open('test_llm.log', 'w')
        service_process = subprocess.Popen(
            [str(venv_python), str(service_script)],
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        
        print(f"   Started with PID: {service_process.pid}")
        
        # Wait for service to initialize
        print("\n4. Waiting for service to initialize (loading LLM model)...")
        time.sleep(15)  # LLM needs time to load model
        
        # Check health
        print("\n5. Checking health status...")
        health_client = HealthClient(port=5005)
        
        for i in range(60):  # Give more time for model loading
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
        channel = grpc.insecure_channel('127.0.0.1:5005')
        stub = services_pb2_grpc.LlmServiceStub(channel)
        
        # Test Complete (non-streaming)
        print("   - Testing Complete (streaming)...")
        request = services_pb2.CompleteRequest(
            text="Hello, how are you?",
            dialog_id="test_dialog",
            turn_number=1,
            conversation_history=""
        )
        
        chunks_received = 0
        full_response = ""
        try:
            for chunk in stub.Complete(request):
                if chunk.text:
                    full_response += chunk.text
                    chunks_received += 1
                    if chunks_received == 1:
                        print(f"     First chunk: {chunk.text[:50]}...")
                if chunk.eot:
                    print(f"     EOT received")
                    break
            print(f"     Chunks received: {chunks_received}")
            print(f"     Total response length: {len(full_response)} chars")
        except grpc.RpcError as e:
            print(f"     Error: {e}")
            return False
        
        # Note: LLM service doesn't have a GetModelInfo method
        # Model info is configured via config.ini
        
        print("\n✓ LLM SERVICE TEST PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ LLM SERVICE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if service_process:
            print("\n7. Stopping service...")
            service_process.terminate()
            try:
                service_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service_process.kill()
            print("   Service stopped")
        
        # Show last log lines
        if Path('test_llm.log').exists():
            print("\n8. Last log lines:")
            with open('test_llm.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-50:]:  # Show more lines for debugging
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_llm_service()
    sys.exit(0 if success else 1)
