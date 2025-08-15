#!/usr/bin/env python3
"""Test TTS service with specific text and audio playback."""

import sys
import time
import subprocess
import grpc
import numpy as np
import sounddevice as sd
import soundfile as sf
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_tts_with_audio():
    """Test TTS service and play the generated audio."""
    
    test_text = """Test successful. Ya-da ya-da. Great! The TTS service is working now! 
    I can see it's downloading the Kokoro model and required dependencies. 
    Now let's check the GPU memory usage to see if the model is actually being loaded to GPU."""
    
    print("\n" + "="*80)
    print("TTS SERVICE TEST WITH AUDIO PLAYBACK")
    print("="*80)
    
    logger_process = None
    tts_process = None
    
    try:
        # Kill any existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(1)
        
        # Start logger service (TTS dependency)
        print("\n2. Starting logger service...")
        venv_python = Path('.venv/bin/python').absolute()
        logger_script = Path('services/logger/logger_service.py').absolute()
        
        logger_log = open('test_logger.log', 'w')
        logger_process = subprocess.Popen(
            [str(venv_python), str(logger_script)],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        print(f"   Logger started with PID: {logger_process.pid}")
        
        # Wait for logger to be ready
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service is ready")
                break
            time.sleep(1)
        
        # Start TTS service
        print("\n3. Starting TTS service...")
        tts_script = Path('services/tts/tts_service.py').absolute()
        
        tts_log = open('test_tts.log', 'w')
        tts_process = subprocess.Popen(
            [str(venv_python), str(tts_script)],
            stdout=tts_log,
            stderr=subprocess.STDOUT
        )
        print(f"   TTS started with PID: {tts_process.pid}")
        
        # Wait for TTS to initialize (model loading)
        print("\n4. Waiting for TTS model to load...")
        print("   (This may take a moment on first run as it downloads the Kokoro model)")
        tts_health = HealthClient(port=5006)
        
        for i in range(60):  # Give up to 60 seconds for model download/load
            status = tts_health.check()
            if status == "SERVING":
                print("   ✓ TTS service is ready!")
                break
            elif i % 5 == 0:  # Print progress every 5 seconds
                print(f"   Still loading... ({i}s)")
            time.sleep(1)
        else:
            print("   ✗ TTS service failed to become ready")
            return False
        
        # Connect to TTS service
        print("\n5. Connecting to TTS service...")
        channel = grpc.insecure_channel('127.0.0.1:5006')
        stub = services_pb2_grpc.TtsServiceStub(channel)
        
        # Synthesize speech
        print("\n6. Synthesizing speech...")
        print(f"   Text: {test_text[:100]}...")
        
        start_time = time.time()
        response = stub.Speak(services_pb2.SpeakRequest(
            text=test_text,
            dialog_id="test_audio",
            voice="af_heart"  # Female voice
        ))
        synthesis_time = time.time() - start_time
        
        if response.success:
            print(f"   ✓ Synthesis successful!")
            print(f"   Synthesis time: {synthesis_time:.2f}s")
            print(f"   Audio duration: {response.duration_ms/1000:.2f}s")
            
            # Note: The audio is already being played by the TTS service
            # through its AudioStreamQueue. If you want to save it to file,
            # you would need to modify the TTS service to return the audio data
            print("\n7. Audio should be playing through your speakers...")
            print("   (Make sure your audio output is not muted)")
            
            # Wait for playback to complete
            time.sleep(response.duration_ms/1000 + 1)
            
        else:
            print(f"   ✗ Synthesis failed: {response.message}")
            return False
        
        # Test with a different voice (male)
        print("\n8. Testing with male voice...")
        response = stub.Speak(services_pb2.SpeakRequest(
            text="This is a test with a male voice. Testing one, two, three.",
            dialog_id="test_audio_male",
            voice="am_adam"  # Male voice
        ))
        
        if response.success:
            print(f"   ✓ Male voice synthesis successful!")
            time.sleep(response.duration_ms/1000 + 1)
        
        print("\n" + "="*80)
        print("✓ TTS TEST COMPLETED SUCCESSFULLY!")
        print("="*80)
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print("\n9. Cleaning up...")
        if tts_process:
            tts_process.terminate()
            try:
                tts_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                tts_process.kill()
            print("   TTS service stopped")
            
        if logger_process:
            logger_process.terminate()
            try:
                logger_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger_process.kill()
            print("   Logger service stopped")
        
        # Show TTS log tail
        if Path('test_tts.log').exists():
            print("\n10. Last TTS log lines:")
            with open('test_tts.log', 'r') as f:
                lines = f.readlines()
                for line in lines[-20:]:
                    print(f"   {line.rstrip()}")


if __name__ == "__main__":
    success = test_tts_with_audio()
    sys.exit(0 if success else 1)
