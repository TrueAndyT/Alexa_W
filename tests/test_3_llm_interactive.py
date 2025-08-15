#!/usr/bin/env python3
"""Interactive LLM test - Type text to get AI response."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient

def test_llm_interactive():
    print("\n" + "="*80)
    print("INTERACTIVE LLM TEST - AI RESPONSES")
    print("="*80)
    
    processes = {}
    
    try:
        # Clean up
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(2)
        
        # Start Ollama
        print("\n2. Starting Ollama server...")
        processes['ollama'] = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for Ollama
        for i in range(10):
            time.sleep(1)
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if result.returncode == 0:
                print("   ✓ Ollama server ready")
                break
        
        # Pre-load model
        print("\n3. Loading LLM model (llama3.1:8b-instruct-q4_K_M)...")
        print("   This may take a moment...")
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "hi"],
            capture_output=True,
            text=True,
            timeout=60
        )
        print("   ✓ Model loaded")
        
        # Start LLM service
        print("\n4. Starting LLM service...")
        venv_python = Path('.venv/bin/python').absolute()
        llm_log = open('llm_service.log', 'w')
        processes['llm'] = subprocess.Popen(
            [str(venv_python), 'services/llm/llm_service.py'],
            stdout=llm_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for LLM
        llm_health = HealthClient(port=5005)
        for i in range(20):
            if llm_health.check() == "SERVING":
                print("   ✓ LLM service ready")
                break
            time.sleep(1)
        
        # Connect to LLM
        channel = grpc.insecure_channel('127.0.0.1:5005')
        llm_stub = services_pb2_grpc.LlmServiceStub(channel)
        
        print("\n" + "="*80)
        print("READY FOR TESTING")
        print("="*80)
        print("\n📢 INSTRUCTIONS:")
        print("   1. Type your question or message")
        print("   2. Press ENTER to send")
        print("   3. Watch the AI response stream in")
        print("   4. Type 'quit' to exit\n")
        print("="*80)
        
        test_count = 0
        successful_tests = 0
        
        while True:
            user_input = input("\n💬 You: ")
            if user_input.lower() == 'quit':
                break
            
            if not user_input.strip():
                print("   (Please type something)")
                continue
            
            test_count += 1
            
            # Send to LLM
            print("\n🤖 AI: ", end='', flush=True)
            
            try:
                complete_request = services_pb2.CompleteRequest(
                    text=user_input,
                    dialog_id=f"test_{test_count}",
                    turn_number=1,
                    conversation_history=""
                )
                
                full_response = ""
                chunk_count = 0
                
                # Stream response
                for chunk in llm_stub.Complete(complete_request):
                    if chunk.text:
                        print(chunk.text, end='', flush=True)
                        full_response += chunk.text
                        chunk_count += 1
                    if chunk.eot:
                        break
                
                print()  # New line after response
                
                if chunk_count > 0:
                    successful_tests += 1
                    print(f"\n   ✓ Response complete ({chunk_count} chunks)")
                else:
                    print("\n   ⚠️ No response received")
                    
            except Exception as e:
                print(f"\n   ✗ Error: {e}")
        
        print(f"\n✅ TEST COMPLETE: {successful_tests}/{test_count} successful responses")
        return successful_tests > 0
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        return False
        
    finally:
        print("\n5. Cleaning up...")
        for name, proc in processes.items():
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        print("   Done")

if __name__ == "__main__":
    success = test_llm_interactive()
    sys.exit(0 if success else 1)
