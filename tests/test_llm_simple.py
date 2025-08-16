#!/usr/bin/env python3
"""Simple LLM test using existing loader service with VRAM monitoring."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def get_vram_usage():
    """Get current VRAM usage in MB."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except:
        pass
    return None


def test_llm_with_loader():
    """Test LLM service using the existing loader."""
    
    print("\n" + "="*60)
    print("LLM SERVICE TEST (using loader)")
    print("="*60)
    
    # Check if loader is already running
    loader_health = HealthClient(port=5002)
    loader_running = loader_health.check() == "SERVING"
    
    if not loader_running:
        print("\n1. Starting loader service...")
        print("   Run: python manage_services.py start loader")
        print("   Then re-run this test")
        return False
    
    print("\n1. Loader service already running ✓")
    
    # Monitor VRAM while waiting for LLM
    print("\n2. Monitoring LLM service and VRAM...")
    
    initial_vram = get_vram_usage()
    print(f"   Initial VRAM: {initial_vram}MB")
    
    llm_health = HealthClient(port=5005)
    last_vram = initial_vram
    no_change_count = 0
    
    for i in range(30):  # Max 30 seconds
        # Check LLM health
        status = llm_health.check()
        
        # Monitor VRAM every 5 seconds
        if i > 0 and i % 5 == 0:
            current_vram = get_vram_usage()
            if current_vram:
                change = current_vram - initial_vram
                print(f"   [{i}s] VRAM: {current_vram}MB (change: {change:+}MB)")
                
                # Check if VRAM is changing
                if last_vram and abs(current_vram - last_vram) < 50:
                    no_change_count += 1
                    if no_change_count >= 3:  # No change for 15 seconds
                        print("   ⚠ VRAM stable - model likely already loaded")
                else:
                    no_change_count = 0
                    print("   ✓ Model loading detected")
                
                last_vram = current_vram
        
        if status == "SERVING":
            print(f"\n3. LLM service is ready! (took {i}s)")
            break
            
        time.sleep(1)
    else:
        print("\n✗ LLM service not ready after 30s")
        print("\nChecking logs...")
        subprocess.run(["tail", "-n", "20", "logs/llm_service.log"])
        return False
    
    # Test LLM with a simple query
    print("\n4. Testing LLM query...")
    
    try:
        channel = grpc.insecure_channel('127.0.0.1:5005')
        stub = services_pb2_grpc.LlmServiceStub(channel)
        
        request = services_pb2.CompleteRequest(
            text="Say hello in exactly three words",
            dialog_id="test_simple",
            turn_number=1,
            conversation_history=""
        )
        
        print("   Sending test prompt...")
        response_text = ""
        chunk_count = 0
        
        for chunk in stub.Complete(request, timeout=15):
            if chunk.text:
                response_text += chunk.text
                chunk_count += 1
                if chunk_count == 1:
                    print(f"   ✓ First response in {chunk.latency_ms}ms")
            if chunk.eot:
                break
        
        if response_text:
            print(f"\n5. Success! LLM responded:")
            print(f"   \"{response_text.strip()}\"")
            print(f"   (Received {chunk_count} chunks)")
            
            # Check final VRAM
            final_vram = get_vram_usage()
            if initial_vram and final_vram:
                total_increase = final_vram - initial_vram
                print(f"\n6. VRAM usage increased by {total_increase}MB total")
            
            return True
        else:
            print("   ✗ No response received")
            return False
            
    except grpc.RpcError as e:
        print(f"   ✗ gRPC Error: {e.code()}")
        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            print("   Request timed out - LLM may not be processing")
            print("\nChecking LLM log for errors...")
            subprocess.run(["tail", "-n", "30", "logs/llm_service.log"])
        return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False


if __name__ == "__main__":
    # First ensure Ollama is running
    print("\nChecking Ollama...")
    result = subprocess.run(["ollama", "list"], capture_output=True)
    if result.returncode != 0:
        print("✗ Ollama not running. Starting it...")
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    else:
        print("✓ Ollama is running")
    
    success = test_llm_with_loader()
    
    print("\n" + "="*60)
    if success:
        print("✅ LLM TEST PASSED")
    else:
        print("❌ LLM TEST FAILED")
    print("="*60)
    
    sys.exit(0 if success else 1)
