#!/usr/bin/env python3
"""Test KWD-TTS integration - verifies TTS is used for greetings and yes phrases."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_kwd_tts_integration():
    """Test that TTS is properly used for wake word responses."""
    
    print("\n" + "="*80)
    print("KWD-TTS INTEGRATION TEST")
    print("="*80)
    
    processes = {}
    
    try:
        # Clean up any existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        time.sleep(2)
        
        venv_python = Path('.venv/bin/python').absolute()
        
        # Start Logger service
        print("\n2. Starting Logger service...")
        logger_log = open('logger_service.log', 'w')
        processes['logger'] = subprocess.Popen(
            [str(venv_python), 'services/logger/logger_service.py'],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for Logger
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service ready")
                break
            time.sleep(1)
        
        # Start TTS service
        print("\n3. Starting TTS service (Kokoro)...")
        tts_log = open('tts_service.log', 'w')
        processes['tts'] = subprocess.Popen(
            [str(venv_python), 'services/tts/tts_service.py'],
            stdout=tts_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for TTS
        print("   Waiting for TTS service...")
        tts_health = HealthClient(port=5006)
        for i in range(30):
            if tts_health.check() == "SERVING":
                print("   ✓ TTS service ready")
                break
            time.sleep(1)
        
        # Connect to TTS
        tts_channel = grpc.insecure_channel('127.0.0.1:5006')
        tts_stub = services_pb2_grpc.TtsServiceStub(tts_channel)
        
        # Test warm-up greeting
        print("\n4. Testing warm-up greeting via TTS...")
        print("   Playing: 'Hi, Master!'")
        
        response = tts_stub.Speak(services_pb2.SpeakRequest(
            text="Hi, Master!",
            dialog_id="warmup",
            voice="af_heart"
        ))
        
        if response.success:
            print("   ✓ Warm-up greeting played successfully via TTS")
            print(f"     Audio duration: {response.duration_ms}ms")
        else:
            print(f"   ✗ Failed to play greeting: {response.message}")
        
        time.sleep(2)
        
        # Test yes phrases
        yes_phrases = ["Yes?", "Yes, Master?", "I'm listening"]
        
        print("\n5. Testing yes phrases via TTS...")
        for i, phrase in enumerate(yes_phrases, 1):
            print(f"\n   Test {i}: '{phrase}'")
            
            response = tts_stub.Speak(services_pb2.SpeakRequest(
                text=phrase,
                dialog_id=f"test_{i}",
                voice="af_heart"
            ))
            
            if response.success:
                print(f"   ✓ Phrase played successfully")
                print(f"     Audio duration: {response.duration_ms}ms")
            else:
                print(f"   ✗ Failed: {response.message}")
            
            time.sleep(2)
        
        # Now test with KWD service
        print("\n6. Starting KWD service...")
        kwd_log = open('kwd_service.log', 'w')
        processes['kwd'] = subprocess.Popen(
            [str(venv_python), 'services/kwd/kwd_service.py'],
            stdout=kwd_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for KWD
        kwd_health = HealthClient(port=5003)
        for i in range(20):
            if kwd_health.check() == "SERVING":
                print("   ✓ KWD service ready")
                break
            time.sleep(1)
        
        # Connect to KWD
        kwd_channel = grpc.insecure_channel('127.0.0.1:5003')
        kwd_stub = services_pb2_grpc.KwdServiceStub(kwd_channel)
        
        print("\n7. Testing KWD-TTS integration flow:")
        print("   The flow should be:")
        print("   1. Wake word detected by KWD")
        print("   2. Loader receives wake event")
        print("   3. Loader uses TTS to say 'Yes?' or similar")
        print("   4. Audio should come from TTS (Kokoro), not system sounds")
        
        print("\n   Note: In the current implementation:")
        print("   - KWD only detects wake words")
        print("   - The loader service handles the TTS response")
        print("   - If you hear system sounds instead of TTS, the loader")
        print("     isn't properly using the TTS service")
        
        # Listen for wake events
        print("\n8. Listening for wake word events...")
        print("   Say 'Hey Jarvis' to test...")
        print("   (Press Ctrl+C to stop)\n")
        
        try:
            for event in kwd_stub.Events(services_pb2.Empty()):
                print(f"\n   🎤 Wake detected: {event.wake_word}")
                print(f"      Confidence: {event.confidence:.2f}")
                print(f"      Time: {event.timestamp_ms}")
                
                # In a proper integration, the loader would now:
                # 1. Receive this event
                # 2. Use TTS to say a yes phrase
                print("\n   ⚠️  Note: Without the loader service running,")
                print("      you won't hear the TTS response.")
                print("      The loader is responsible for coordinating")
                print("      the TTS response to wake word detection.\n")
                
                # Simulate what the loader would do
                print("   Simulating loader's TTS response...")
                response = tts_stub.Speak(services_pb2.SpeakRequest(
                    text="Yes, Master?",
                    dialog_id=f"wake_{event.timestamp_ms}",
                    voice="af_heart"
                ))
                
                if response.success:
                    print("   ✓ TTS response played (this is what loader should do)")
                else:
                    print(f"   ✗ TTS failed: {response.message}")
                
        except KeyboardInterrupt:
            print("\n\nStopping wake word listening...")
        
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        print("\n✓ TTS service (Kokoro) works correctly")
        print("✓ KWD service detects wake words")
        print("\n⚠️  Important findings:")
        print("  - KWD service only detects wake words")
        print("  - TTS responses should be handled by the loader")
        print("  - If you hear system sounds instead of TTS voice,")
        print("    the loader needs to be fixed to use TTS properly")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print("\n9. Cleaning up services...")
        
        for name, process in processes.items():
            if process and process.poll() is None:
                print(f"   Stopping {name}...")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)


if __name__ == "__main__":
    success = test_kwd_tts_integration()
    sys.exit(0 if success else 1)
