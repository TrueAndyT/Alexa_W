#!/usr/bin/env python3
"""Test LLM service with llama3.1:8b-instruct-q4_K_M model."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_llm_llama31():
    """Test LLM service with llama3.1:8b model."""
    
    print("\n" + "="*80)
    print("LLM SERVICE TEST WITH LLAMA 3.1 8B MODEL")
    print("="*80)
    
    ollama_process = None
    logger_process = None
    llm_process = None
    
    try:
        # Clean up existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(2)
        
        # Start Ollama server
        print("\n2. Starting Ollama server...")
        ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for Ollama to be ready
        for i in range(10):
            time.sleep(1)
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("   ✓ Ollama server is ready")
                models = result.stdout
                print(f"   Available models:\n{models}")
                break
        else:
            print("   ✗ Ollama server failed to start")
            return False
        
        # Test model directly
        print("\n3. Testing llama3.1:8b model directly with Ollama...")
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "Say 'test successful' and nothing else"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print(f"   Direct test response: {result.stdout.strip()}")
        else:
            print(f"   Direct test failed: {result.stderr}")
        
        # Start logger service
        print("\n4. Starting logger service...")
        venv_python = Path('.venv/bin/python').absolute()
        logger_script = Path('services/logger/logger_service.py').absolute()
        
        logger_log = open('test_logger.log', 'w')
        logger_process = subprocess.Popen(
            [str(venv_python), str(logger_script)],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        print(f"   Logger started with PID: {logger_process.pid}")
        
        # Wait for logger
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service is ready")
                break
            time.sleep(1)
        
        # Start LLM service
        print("\n5. Starting LLM service...")
        llm_script = Path('services/llm/llm_service.py').absolute()
        
        llm_log = open('test_llm.log', 'w')
        llm_process = subprocess.Popen(
            [str(venv_python), str(llm_script)],
            stdout=llm_log,
            stderr=subprocess.STDOUT
        )
        print(f"   LLM started with PID: {llm_process.pid}")
        
        # Wait for LLM service
        print("\n6. Waiting for LLM service to initialize...")
        llm_health = HealthClient(port=5005)
        
        for i in range(30):
            status = llm_health.check()
            if status == "SERVING":
                print("   ✓ LLM service is ready!")
                break
            elif i % 5 == 0:
                print(f"   Still loading... ({i}s)")
            time.sleep(1)
        else:
            print("   ✗ LLM service failed to become ready")
            # Show log
            if Path('test_llm.log').exists():
                with open('test_llm.log', 'r') as f:
                    print("\n   LLM Service Log:")
                    print(f.read())
            return False
        
        # Connect to LLM service
        print("\n7. Connecting to LLM service...")
        channel = grpc.insecure_channel('127.0.0.1:5005')
        stub = services_pb2_grpc.LlmServiceStub(channel)
        
        # Test queries
        test_queries = [
            "What is 2 + 2?",
            "Say hello in one word",
            "Complete this: The sky is",
        ]
        
        print("\n8. Testing LLM completions...")
        for query in test_queries:
            print(f"\n   Query: '{query}'")
            print("   Response: ", end="", flush=True)
            
            request = services_pb2.CompleteRequest(
                text=query,
                dialog_id=f"test_{int(time.time())}",
                turn_number=1,
                conversation_history=""
            )
            
            response_text = []
            first_token_time = None
            start_time = time.time()
            token_count = 0
            
            try:
                for chunk in stub.Complete(request):
                    if chunk.text:
                        if first_token_time is None:
                            first_token_time = time.time()
                            latency = (first_token_time - start_time) * 1000
                            print(f"[First token: {latency:.0f}ms] ", end="", flush=True)
                        
                        print(chunk.text, end="", flush=True)
                        response_text.append(chunk.text)
                        token_count = chunk.token_count
                    
                    if chunk.eot:
                        total_time = (time.time() - start_time) * 1000
                        print(f"\n   Stats: {token_count} tokens in {total_time:.0f}ms")
                        break
                
                if not response_text:
                    print("(no response)")
                    
            except grpc.RpcError as e:
                print(f"\n   Error: {e}")
                # Show last log lines
                if Path('test_llm.log').exists():
                    with open('test_llm.log', 'r') as f:
                        lines = f.readlines()
                        print("   Last log lines:")
                        for line in lines[-10:]:
                            print(f"     {line.rstrip()}")
        
        # Check GPU usage
        print("\n9. Checking GPU memory usage...")
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            used, free = result.stdout.strip().split(', ')
            print(f"   GPU Memory - Used: {used}, Free: {free}")
        
        print("\n" + "="*80)
        print("✓ LLM TEST COMPLETED SUCCESSFULLY!")
        print("="*80)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        
        # Show LLM log
        if Path('test_llm.log').exists():
            print("\nLLM Service Log:")
            with open('test_llm.log', 'r') as f:
                print(f.read())
        
        return False
        
    finally:
        # Cleanup
        print("\n10. Cleaning up...")
        if llm_process:
            llm_process.terminate()
            try:
                llm_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                llm_process.kill()
            print("   LLM service stopped")
        
        if logger_process:
            logger_process.terminate()
            try:
                logger_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger_process.kill()
            print("   Logger service stopped")
        
        if ollama_process:
            print("   Stopping Ollama server...")
            ollama_process.terminate()
            try:
                ollama_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ollama_process.kill()
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)


if __name__ == "__main__":
    success = test_llm_llama31()
    sys.exit(0 if success else 1)
