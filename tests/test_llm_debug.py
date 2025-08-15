#!/usr/bin/env python3
"""Debug test for LLM service to diagnose response issues."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient

def test_llm_debug():
    print("\n" + "="*80)
    print("LLM DEBUG TEST")
    print("="*80)
    
    loader_process = None
    
    try:
        # Clean up any existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        time.sleep(2)
        
        # Ensure Ollama is running
        print("\n2. Checking Ollama...")
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True
        )
        if result.returncode != 0:
            print("   Starting Ollama server...")
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            for i in range(15):
                result = subprocess.run(
                    ["curl", "-s", "http://localhost:11434/api/tags"],
                    capture_output=True
                )
                if result.returncode == 0:
                    print("   ✓ Ollama started")
                    break
                time.sleep(1)
            else:
                print("   ✗ Failed to start Ollama")
                return False
        else:
            print("   ✓ Ollama already running")
        
        # Start the Loader service
        print("\n3. Starting Loader service...")
        venv_python = Path('.venv/bin/python').absolute()
        loader_log = open('loader_service.log', 'w')
        loader_process = subprocess.Popen(
            [str(venv_python), 'services/loader/loader_service.py'],
            stdout=loader_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for LLM service to be ready
        print("\n4. Waiting for LLM service...")
        llm_health = HealthClient(port=5005)
        for i in range(60):
            status = llm_health.check()
            if status == "SERVING":
                print("   ✓ LLM service ready")
                break
            elif i % 5 == 0:
                print(f"   Still waiting... ({i}s)")
            time.sleep(1)
        else:
            print("   ✗ LLM service did not become ready")
            print("\n--- Checking logs ---")
            subprocess.run(["tail", "-n", "50", "loader_service.log"])
            return False
        
        # Connect to LLM
        print("\n5. Connecting to LLM service...")
        channel = grpc.insecure_channel('127.0.0.1:5005')
        llm_stub = services_pb2_grpc.LlmServiceStub(channel)
        
        # Test with a simple prompt
        test_prompt = "Hello, how are you?"
        print(f"\n6. Sending test prompt: '{test_prompt}'")
        print("   Timestamp:", time.time())
        
        complete_request = services_pb2.CompleteRequest(
            text=test_prompt,
            dialog_id="debug_test",
            turn_number=1,
            conversation_history=""
        )
        
        print("\n7. Waiting for response (10s timeout)...")
        response_text = ""
        chunk_count = 0
        first_chunk_time = None
        
        try:
            # Add 10-second timeout to prevent hanging
            for chunk in llm_stub.Complete(complete_request, timeout=10):
                if chunk_count == 0:
                    first_chunk_time = time.time()
                    print(f"   First chunk received at: {first_chunk_time}")
                    print(f"   First token latency: {chunk.latency_ms}ms")
                
                if chunk.text:
                    response_text += chunk.text
                    chunk_count += 1
                    print(f"   Chunk {chunk_count}: '{chunk.text}'")
                
                if chunk.eot:
                    print(f"   EOT received")
                    break
        except grpc.RpcError as e:
            print(f"\n   ✗ gRPC Error: {e.code()} - {e.details()}")
            if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                print("   The request timed out after 10 seconds.")
                print("   This likely means the LLM service is not processing requests.")
                print("\n   Checking LLM logs for errors...")
                subprocess.run(["tail", "-n", "30", "llm_service.log"])
            return False
        except Exception as e:
            print(f"\n   ✗ Error: {e}")
            return False
        
        if response_text:
            print(f"\n8. Response complete!")
            print(f"   Total chunks: {chunk_count}")
            print(f"   Response length: {len(response_text)} chars")
            print(f"   Response: {response_text}")
        else:
            print(f"\n8. No response received!")
            print("   Checking LLM logs...")
            subprocess.run(["tail", "-n", "30", "llm_service.log"])
        
        # Check the logs
        print("\n9. Checking service logs...")
        print("\n--- LLM Service Log (last 20 lines) ---")
        subprocess.run(["tail", "-n", "20", "llm_service.log"])
        
        print("\n--- Loader Service Log (last 10 lines) ---")
        subprocess.run(["tail", "-n", "10", "loader_service.log"])
        
        # Test Ollama directly
        print("\n10. Testing Ollama directly...")
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("   ✓ Ollama is responding")
        else:
            print("   ✗ Ollama is not responding")
        
        # Check if model is loaded
        print("\n11. Checking if model is loaded...")
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True
        )
        print(result.stdout)
        
        return bool(response_text)
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        print("\n12. Cleaning up...")
        
        if loader_process:
            loader_process.terminate()
            try:
                loader_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                loader_process.kill()
        
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        print("   Done")

if __name__ == "__main__":
    success = test_llm_debug()
    print(f"\n{'='*80}")
    if success:
        print("✅ LLM DEBUG TEST PASSED")
    else:
        print("❌ LLM DEBUG TEST FAILED")
    print("="*80)
    sys.exit(0 if success else 1)
