#!/usr/bin/env python3
"""Test LLM service with VRAM measurements."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient
from common.gpu_monitor import GPUMonitor


def get_vram_usage():
    """Get current VRAM usage in MB."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        used, free = result.stdout.strip().split(', ')
        return int(used.strip().replace(' MiB', '')), int(free.strip().replace(' MiB', ''))
    return 0, 0


def test_llm_vram():
    """Test LLM service with VRAM measurements."""
    
    print("\n" + "="*80)
    print("LLM SERVICE VRAM USAGE TEST")
    print("="*80)
    
    ollama_process = None
    logger_process = None
    llm_process = None
    
    # Track VRAM usage
    vram_measurements = {}
    
    try:
        # Clean up existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(3)
        
        # Initial VRAM measurement
        used_initial, free_initial = get_vram_usage()
        vram_measurements['initial'] = {'used': used_initial, 'free': free_initial}
        print(f"\n2. Initial VRAM: {used_initial} MB used, {free_initial} MB free")
        
        # Start Ollama server
        print("\n3. Starting Ollama server...")
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
                break
        
        # VRAM after Ollama start
        used_ollama, free_ollama = get_vram_usage()
        vram_measurements['ollama_started'] = {'used': used_ollama, 'free': free_ollama}
        vram_delta = used_ollama - used_initial
        print(f"\n4. VRAM after Ollama start: {used_ollama} MB used (+{vram_delta} MB)")
        
        # Load model directly with Ollama to measure model VRAM
        print("\n5. Loading llama3.1:8b-instruct-q4_K_M model...")
        start_time = time.time()
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "Hi"],
            capture_output=True,
            text=True,
            timeout=60
        )
        load_time = time.time() - start_time
        
        # VRAM after model load
        used_model, free_model = get_vram_usage()
        vram_measurements['model_loaded'] = {'used': used_model, 'free': free_model}
        model_vram = used_model - used_ollama
        print(f"   Model loaded in {load_time:.1f}s")
        print(f"   VRAM after model load: {used_model} MB used (+{model_vram} MB for model)")
        print(f"   Model VRAM usage: {model_vram} MB")
        
        # Load and display Modelfile
        print("\n6. Loading system prompt from Modelfile...")
        modelfile_path = Path("config/Modelfile")
        system_prompt = ""
        if modelfile_path.exists():
            content = modelfile_path.read_text()
            # Extract SYSTEM section
            if 'SYSTEM """' in content:
                start = content.index('SYSTEM """') + len('SYSTEM """')
                end = content.index('"""', start)
                system_prompt = content[start:end].strip()
                print(f"   System prompt loaded ({len(system_prompt)} chars):")
                print("   ---")
                for line in system_prompt.split('\n')[:5]:  # Show first 5 lines
                    print(f"   {line}")
                if len(system_prompt.split('\n')) > 5:
                    print("   ...")
                print("   ---")
        else:
            print("   ✗ Modelfile not found")
        
        # Start logger service
        print("\n7. Starting logger service...")
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
        print("\n8. Starting LLM service...")
        llm_script = Path('services/llm/llm_service.py').absolute()
        
        llm_log = open('test_llm.log', 'w')
        llm_process = subprocess.Popen(
            [str(venv_python), str(llm_script)],
            stdout=llm_log,
            stderr=subprocess.STDOUT
        )
        print(f"   LLM started with PID: {llm_process.pid}")
        
        # Wait for LLM service
        print("\n9. Waiting for LLM service to initialize...")
        llm_health = HealthClient(port=5005)
        
        for i in range(20):
            status = llm_health.check()
            if status == "SERVING":
                print("   ✓ LLM service is ready!")
                break
            time.sleep(1)
        else:
            print("   ✗ LLM service failed to become ready")
            return False
        
        # VRAM after LLM service start
        used_llm_service, free_llm_service = get_vram_usage()
        vram_measurements['llm_service_started'] = {'used': used_llm_service, 'free': free_llm_service}
        llm_service_vram = used_llm_service - used_model
        print(f"\n10. VRAM after LLM service: {used_llm_service} MB used (+{llm_service_vram} MB)")
        
        # Connect to LLM service
        print("\n11. Testing LLM with system prompt...")
        channel = grpc.insecure_channel('127.0.0.1:5005')
        stub = services_pb2_grpc.LlmServiceStub(channel)
        
        # Test query that should use the system prompt
        test_query = "Who are you?"
        print(f"    Query: '{test_query}'")
        print("    Response: ", end="", flush=True)
        
        request = services_pb2.CompleteRequest(
            text=test_query,
            dialog_id=f"test_{int(time.time())}",
            turn_number=1,
            conversation_history=""
        )
        
        response_text = []
        start_time = time.time()
        first_token_time = None
        
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
                    print(f"\n    Total time: {total_time:.0f}ms")
                    break
        except grpc.RpcError as e:
            print(f"\n    Error: {e}")
        
        # Final VRAM measurement after inference
        used_final, free_final = get_vram_usage()
        vram_measurements['after_inference'] = {'used': used_final, 'free': free_final}
        
        # Print VRAM summary
        print("\n" + "="*80)
        print("VRAM USAGE SUMMARY")
        print("="*80)
        print(f"Initial state:           {vram_measurements['initial']['used']:5d} MB used")
        print(f"After Ollama start:      {vram_measurements['ollama_started']['used']:5d} MB used (+{vram_measurements['ollama_started']['used'] - vram_measurements['initial']['used']} MB)")
        print(f"After model load:        {vram_measurements['model_loaded']['used']:5d} MB used (+{vram_measurements['model_loaded']['used'] - vram_measurements['ollama_started']['used']} MB for model)")
        print(f"After LLM service:       {vram_measurements['llm_service_started']['used']:5d} MB used (+{vram_measurements['llm_service_started']['used'] - vram_measurements['model_loaded']['used']} MB)")
        print(f"After inference:         {vram_measurements['after_inference']['used']:5d} MB used (+{vram_measurements['after_inference']['used'] - vram_measurements['llm_service_started']['used']} MB)")
        print("-"*80)
        print(f"LLAMA 3.1 8B Q4 MODEL:   {vram_measurements['model_loaded']['used'] - vram_measurements['ollama_started']['used']:5d} MB")
        print(f"Peak VRAM usage:         {vram_measurements['after_inference']['used']:5d} MB")
        print(f"Free VRAM remaining:     {vram_measurements['after_inference']['free']:5d} MB")
        print("="*80)
        
        # Test if system prompt is being used
        print("\n12. Testing if system prompt affects responses...")
        test_queries = [
            "What's your name?",
            "Are you Alexa?",
            "Tell me about yourself in one sentence."
        ]
        
        for query in test_queries:
            print(f"\n    Query: '{query}'")
            print("    Response: ", end="", flush=True)
            
            request = services_pb2.CompleteRequest(
                text=query,
                dialog_id=f"test_{int(time.time())}",
                turn_number=1,
                conversation_history=""
            )
            
            response_text = []
            try:
                for chunk in stub.Complete(request):
                    if chunk.text:
                        print(chunk.text, end="", flush=True)
                        response_text.append(chunk.text)
                    if chunk.eot:
                        break
                print()  # New line after response
            except grpc.RpcError as e:
                print(f"Error: {e}")
        
        print("\n" + "="*80)
        print("✓ LLM VRAM TEST COMPLETED SUCCESSFULLY!")
        print("="*80)
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print("\n13. Cleaning up...")
        if llm_process:
            llm_process.terminate()
            try:
                llm_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                llm_process.kill()
            print("    LLM service stopped")
        
        if logger_process:
            logger_process.terminate()
            try:
                logger_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger_process.kill()
            print("    Logger service stopped")
        
        if ollama_process:
            print("    Stopping Ollama server...")
            ollama_process.terminate()
            try:
                ollama_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ollama_process.kill()
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        # Final VRAM check
        time.sleep(2)
        used_cleanup, free_cleanup = get_vram_usage()
        print(f"\n    Final VRAM after cleanup: {used_cleanup} MB used, {free_cleanup} MB free")


if __name__ == "__main__":
    success = test_llm_vram()
    sys.exit(0 if success else 1)
