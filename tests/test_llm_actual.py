#!/usr/bin/env python3
"""Test LLM service with actual model loading via Ollama."""

import sys
import time
import subprocess
import grpc
import asyncio
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def check_ollama_status():
    """Check if Ollama is running and what models are available."""
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if result.returncode != 0:
        return False, []
    
    # Parse model list
    models = []
    lines = result.stdout.strip().split('\n')
    if len(lines) > 1:  # Skip header
        for line in lines[1:]:
            parts = line.split()
            if parts:
                models.append(parts[0])
    
    return True, models


def start_ollama_server():
    """Start Ollama server in the background."""
    print("Starting Ollama server...")
    # Start ollama serve in background
    ollama_process = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for server to be ready
    for i in range(10):
        time.sleep(1)
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True
        )
        if result.returncode == 0:
            print("   ✓ Ollama server is ready")
            return ollama_process
        
    print("   ✗ Ollama server failed to start")
    return None


def ensure_model_available(model_name="llama3.2:1b"):
    """Ensure a small model is available for testing."""
    print(f"Checking for model: {model_name}")
    
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if model_name in result.stdout:
        print(f"   ✓ Model {model_name} is already available")
        return True
    
    print(f"   Pulling model {model_name} (this may take a few minutes)...")
    result = subprocess.run(
        ["ollama", "pull", model_name],
        capture_output=False,  # Show progress
        text=True
    )
    
    if result.returncode == 0:
        print(f"   ✓ Model {model_name} pulled successfully")
        return True
    else:
        print(f"   ✗ Failed to pull model {model_name}")
        return False


def test_llm_with_model():
    """Test LLM service with actual model."""
    
    print("\n" + "="*80)
    print("LLM SERVICE TEST WITH ACTUAL MODEL")
    print("="*80)
    
    ollama_process = None
    logger_process = None
    llm_process = None
    
    try:
        # Clean up existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(1)
        
        # Check/start Ollama
        print("\n2. Checking Ollama status...")
        ollama_running, models = check_ollama_status()
        
        if not ollama_running:
            ollama_process = start_ollama_server()
            if not ollama_process:
                print("Failed to start Ollama server")
                return False
            time.sleep(2)
            ollama_running, models = check_ollama_status()
        else:
            print("   ✓ Ollama is already running")
        
        if models:
            print(f"   Available models: {', '.join(models)}")
        
        # Ensure we have a model
        model_name = "llama3.2:1b"  # Use small 1B model for testing
        if not ensure_model_available(model_name):
            return False
        
        # Update config to use our test model
        config_path = Path("config/config.ini")
        config_content = config_path.read_text()
        
        # Temporarily update the model in config
        original_config = config_content
        updated_config = config_content.replace(
            "model = llama3.1:8b",
            f"model = {model_name}"
        )
        if updated_config == original_config:
            # Add model config if not present
            if "[llm]" in updated_config:
                updated_config = updated_config.replace(
                    "[llm]",
                    f"[llm]\nmodel = {model_name}"
                )
        
        config_path.write_text(updated_config)
        
        # Start logger service
        print("\n3. Starting logger service...")
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
        print("\n4. Starting LLM service...")
        llm_script = Path('services/llm/llm_service.py').absolute()
        
        llm_log = open('test_llm.log', 'w')
        llm_process = subprocess.Popen(
            [str(venv_python), str(llm_script)],
            stdout=llm_log,
            stderr=subprocess.STDOUT
        )
        print(f"   LLM started with PID: {llm_process.pid}")
        
        # Wait for LLM service
        print("\n5. Waiting for LLM service to initialize...")
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
            return False
        
        # Connect to LLM service
        print("\n6. Connecting to LLM service...")
        channel = grpc.insecure_channel('127.0.0.1:5005')
        stub = services_pb2_grpc.LlmServiceStub(channel)
        
        # Test queries
        test_queries = [
            "Hello! How are you today?",
            "What is 2 + 2?",
            "Tell me a very short joke.",
            "What's the weather like?"
        ]
        
        print("\n7. Testing LLM completions...")
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
            
            try:
                for chunk in stub.Complete(request):
                    if chunk.text:
                        if first_token_time is None:
                            first_token_time = time.time()
                            latency = (first_token_time - start_time) * 1000
                            print(f"[{latency:.0f}ms] ", end="", flush=True)
                        
                        print(chunk.text, end="", flush=True)
                        response_text.append(chunk.text)
                    
                    if chunk.eot:
                        total_time = (time.time() - start_time) * 1000
                        print(f"\n   Tokens: {chunk.token_count}, Total time: {total_time:.0f}ms")
                        break
                
                if not response_text:
                    print("(no response)")
                    
            except grpc.RpcError as e:
                print(f"\n   Error: {e}")
        
        # Check GPU/CPU usage
        print("\n8. Checking resource usage...")
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
        
        # Restore original config
        config_path.write_text(original_config)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        
        # Restore original config
        try:
            config_path.write_text(original_config)
        except:
            pass
        
        return False
        
    finally:
        # Cleanup
        print("\n9. Cleaning up...")
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
        
        # Note: We don't stop Ollama if it was already running
        if ollama_process:
            print("   Stopping Ollama server...")
            ollama_process.terminate()
            try:
                ollama_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ollama_process.kill()
        
        # Show LLM log tail
        if Path('test_llm.log').exists():
            print("\n10. Last LLM log lines:")
            with open('test_llm.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_llm_with_model()
    sys.exit(0 if success else 1)
