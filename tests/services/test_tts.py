#!/usr/bin/env python3
"""Test script for TTS service - starts and tests it independently."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_tts_service():
    """Test TTS service independently."""
    print("\n" + "="*60)
    print("TESTING TTS SERVICE")
    print("="*60)
    
    logger_process = None
    service_process = None
    
    try:
        # Kill any existing services
        print("\n1. Killing any existing services...")
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(1)
        
        # Start logger service first (TTS depends on it)
        print("\n2. Starting logger service (TTS dependency)...")
        venv_python = Path('.venv/bin/python').absolute()
        logger_script = Path('services/logger/logger_service.py').absolute()
        
        logger_log = open('test_logger_for_tts.log', 'w')
        logger_process = subprocess.Popen(
            [str(venv_python), str(logger_script)],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        print(f"   Logger started with PID: {logger_process.pid}")
        
        # Wait for logger to be ready
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(5):
            if logger_health.check() == "SERVING":
                print("   Logger service is ready")
                break
            time.sleep(1)
        else:
            print("   ✗ Logger failed to start")
            return False
        
        # Check GPU memory before starting
        print("\n3. Checking GPU memory...")
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            free_mem = int(result.stdout.strip())
            print(f"   Free GPU memory: {free_mem} MB")
            if free_mem < 2000:
                print("   ⚠ Warning: Low GPU memory, TTS may fail to load model")
        
        # Start the tts service
        print("\n4. Starting tts service...")
        venv_python = Path('.venv/bin/python').absolute()
        service_script = Path('services/tts/tts_service.py').absolute()
        
        log_file = open('test_tts.log', 'w')
        service_process = subprocess.Popen(
            [str(venv_python), str(service_script)],
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        
        print(f"   Started with PID: {service_process.pid}")
        
        # Wait for service to initialize
        print("\n5. Waiting for service to initialize (loading TTS model)...")
        time.sleep(10)  # TTS needs time to load model
        
        # Check health
        print("\n6. Checking health status...")
        health_client = HealthClient(port=5006)
        
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
        print("\n7. Testing gRPC methods...")
        channel = grpc.insecure_channel('127.0.0.1:5006')
        stub = services_pb2_grpc.TtsServiceStub(channel)
        
        # Test Speak (non-streaming)
        print("   - Testing Speak...")
        response = stub.Speak(services_pb2.SpeakRequest(
            text="Hello, this is a test of the text to speech service.",
            dialog_id="test_dialog",
            voice="af_heart"
        ))
        print(f"     Success: {response.success}")
        if response.duration_ms:
            print(f"     Audio duration: {response.duration_ms} ms")
        
        # Note: TTS service doesn't have a GetVoices method
        # Voices are configured via config.ini
        
        # Test SpeakStream (streaming)
        print("   - Testing SpeakStream...")
        def generate_chunks():
            yield services_pb2.LlmChunk(
                text="This is the first chunk of text. ",
                eot=False,
                dialog_id="test_dialog"
            )
            yield services_pb2.LlmChunk(
                text="And this is the second chunk. ",
                eot=False,
                dialog_id="test_dialog"
            )
            yield services_pb2.LlmChunk(
                text="",
                eot=True,
                dialog_id="test_dialog"
            )
        
        try:
            response = stub.SpeakStream(generate_chunks())
            print(f"     Success: {response.success}")
            print(f"     Message: {response.message}")
        except grpc.RpcError as e:
            print(f"     Streaming error: {e}")
        
        print("\n✓ TTS SERVICE TEST PASSED")
        return True
        
    except Exception as e:
        print(f"\n✗ TTS SERVICE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if service_process:
            print("\n8. Stopping TTS service...")
            service_process.terminate()
            try:
                service_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                service_process.kill()
            print("   TTS service stopped")
        
        if logger_process:
            print("\n9. Stopping logger service...")
            logger_process.terminate()
            try:
                logger_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger_process.kill()
            print("   Logger service stopped")
        
        # Show last log lines
        if Path('test_tts.log').exists():
            print("\n10. Last TTS log lines:")
            with open('test_tts.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-40:]:  # Show more lines for debugging
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_tts_service()
    sys.exit(0 if success else 1)
