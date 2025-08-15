#!/usr/bin/env python3
"""Interactive TTS test - Type text to hear it spoken."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient

def test_tts_interactive():
    print("\n" + "="*80)
    print("INTERACTIVE TTS TEST - TEXT TO SPEECH")
    print("="*80)
    
    processes = {}
    
    try:
        # Clean up
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(2)
        
        venv_python = Path('.venv/bin/python').absolute()
        
        # Start Logger (required by TTS)
        print("\n2. Starting Logger service...")
        logger_log = open('logger_service.log', 'w')
        processes['logger'] = subprocess.Popen(
            [str(venv_python), 'services/logger/logger_service.py'],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service ready")
                break
            time.sleep(1)
        
        # Start TTS service
        print("\n3. Starting TTS service (Kokoro)...")
        print("   Loading model - this may take a moment...")
        tts_log = open('tts_service.log', 'w')
        processes['tts'] = subprocess.Popen(
            [str(venv_python), 'services/tts/tts_service.py'],
            stdout=tts_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for TTS
        tts_health = HealthClient(port=5006)
        for i in range(30):
            if tts_health.check() == "SERVING":
                print("   ✓ TTS service ready")
                break
            if i % 5 == 0 and i > 0:
                print(f"   Still loading... ({i}s)")
            time.sleep(1)
        
        # Check if using Kokoro or mock
        with open('tts_service.log', 'r') as f:
            log_content = f.read()
            if "Kokoro model loaded" in log_content:
                print("   ✓ Using Kokoro TTS (natural voice)")
            elif "mock" in log_content.lower():
                print("   ⚠️ Using mock TTS (synthetic tones)")
        
        # Connect to TTS
        channel = grpc.insecure_channel('127.0.0.1:5006')
        tts_stub = services_pb2_grpc.TtsServiceStub(channel)
        
        print("\n" + "="*80)
        print("READY FOR TESTING")
        print("="*80)
        print("\n📢 INSTRUCTIONS:")
        print("   1. Type text you want to hear spoken")
        print("   2. Press ENTER to hear it")
        print("   3. Listen to the audio output")
        print("   4. Type 'quit' to exit\n")
        print("   VOICE OPTIONS: af_heart (default), af_bella, am_adam, am_michael")
        print("="*80)
        
        test_count = 0
        successful_tests = 0
        current_voice = "af_heart"
        
        # Test greeting first
        print("\n🔊 Playing test greeting...")
        response = tts_stub.Speak(services_pb2.SpeakRequest(
            text="Hello! TTS service is ready for testing.",
            dialog_id="test_greeting",
            voice=current_voice
        ))
        
        if response.success:
            print(f"   ✓ Greeting played ({response.duration_ms}ms)")
        else:
            print(f"   ✗ Failed: {response.message}")
        
        while True:
            user_input = input(f"\n📝 Text to speak (voice: {current_voice}): ")
            
            if user_input.lower() == 'quit':
                break
            
            # Check for voice change
            if user_input.startswith('/voice '):
                new_voice = user_input[7:].strip()
                if new_voice in ['af_heart', 'af_bella', 'af_nicole', 'af_sarah', 'am_adam', 'am_michael']:
                    current_voice = new_voice
                    print(f"   ✓ Voice changed to: {current_voice}")
                else:
                    print("   ⚠️ Invalid voice. Use: af_heart, af_bella, am_adam, am_michael")
                continue
            
            if not user_input.strip():
                print("   (Please type something)")
                continue
            
            test_count += 1
            
            # Send to TTS
            print(f"\n🔊 Speaking: \"{user_input}\"")
            
            try:
                response = tts_stub.Speak(services_pb2.SpeakRequest(
                    text=user_input,
                    dialog_id=f"test_{test_count}",
                    voice=current_voice
                ))
                
                if response.success:
                    successful_tests += 1
                    print(f"   ✓ Speech complete ({response.duration_ms}ms)")
                    print("\n   AUDIO CHECK:")
                    print("   • Natural voice? → Kokoro working")
                    print("   • Simple tones? → Mock TTS (Kokoro not loaded)")
                    print("   • Nothing? → Audio issue")
                else:
                    print(f"   ✗ Failed: {response.message}")
                    
            except Exception as e:
                print(f"   ✗ Error: {e}")
        
        print(f"\n✅ TEST COMPLETE: {successful_tests}/{test_count} successful")
        return successful_tests > 0
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        return False
        
    finally:
        print("\n4. Cleaning up...")
        for name, proc in processes.items():
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        print("   Done")

if __name__ == "__main__":
    success = test_tts_interactive()
    sys.exit(0 if success else 1)
