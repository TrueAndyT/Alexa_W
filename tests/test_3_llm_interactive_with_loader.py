#!/usr/bin/env python3
"""Interactive LLM test using the Loader service for proper orchestration."""

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
    print("INTERACTIVE LLM TEST - AI RESPONSES (Using Loader)")
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
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(2)
        
        # Start the Loader service which will orchestrate everything
        print("\n2. Starting Loader service (this will start all services)...")
        print("   This may take a moment as it loads the LLM model...")
        
        venv_python = Path('.venv/bin/python').absolute()
        loader_log = open('loader_service.log', 'w')
        loader_process = subprocess.Popen(
            [str(venv_python), 'services/loader/loader_service.py'],
            stdout=loader_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for all services to be ready (the Loader will start them in order)
        print("\n3. Waiting for services to be ready...")
        
        # Check if LLM service is ready (which means all dependencies are also ready)
        llm_health = HealthClient(port=5005)
        for i in range(60):  # Give it up to 60 seconds
            status = llm_health.check()
            if status == "SERVING":
                print("   ✓ LLM service ready (all services operational)")
                break
            elif i % 10 == 0:
                print(f"   Still waiting... ({i}s)")
            time.sleep(1)
        else:
            print("   ✗ LLM service did not become ready in time")
            print("   Check loader_service.log for details")
            return False
        
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
        print("\n4. Cleaning up...")
        
        # Terminate the loader (which should clean up all child services)
        if loader_process:
            print("   Stopping Loader service...")
            loader_process.terminate()
            try:
                loader_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                loader_process.kill()
        
        # Make sure everything is cleaned up
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        print("   Done")

if __name__ == "__main__":
    success = test_llm_interactive()
    sys.exit(0 if success else 1)
