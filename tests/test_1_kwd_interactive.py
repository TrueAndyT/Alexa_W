#!/usr/bin/env python3
"""Interactive KWD test - Say 'Alexa' to test wake word detection."""

import sys
import time
import grpc
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient

def test_kwd_interactive():
    print("\n" + "="*80)
    print("INTERACTIVE KWD TEST - ALEXA WAKE WORD")
    print("="*80)
    
    kwd_process = None
    
    try:
        # Clean up
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        time.sleep(2)
        
        # Start KWD service
        print("\n2. Starting KWD service...")
        venv_python = Path('.venv/bin/python').absolute()
        kwd_log = open('kwd_service.log', 'w')
        kwd_process = subprocess.Popen(
            [str(venv_python), 'services/kwd/kwd_service.py'],
            stdout=kwd_log,
            stderr=subprocess.STDOUT
        )
        print(f"   Started with PID: {kwd_process.pid}")
        
        # Wait for service to be ready
        print("\n3. Waiting for KWD service to initialize...")
        kwd_health = HealthClient(port=5003)
        for i in range(20):
            if kwd_health.check() == "SERVING":
                print("   ✓ KWD service ready")
                break
            time.sleep(1)
        
        # Connect to KWD
        channel = grpc.insecure_channel('127.0.0.1:5003')
        kwd_stub = services_pb2_grpc.KwdServiceStub(channel)
        
        print("\n" + "="*80)
        print("READY FOR TESTING")
        print("="*80)
        print("\n📢 INSTRUCTIONS:")
        print("   1. Say 'ALEXA' clearly (not 'Hey Jarvis')")
        print("   2. Watch for detection confirmation")
        print("   3. You can say 'Alexa' multiple times")
        print("   4. Press Ctrl+C when done\n")
        print("="*80)
        print("\nListening for 'Alexa' wake word...\n")
        
        detection_count = 0
        
        try:
            # Listen for wake events
            for event in kwd_stub.Events(services_pb2.Empty()):
                detection_count += 1
                print(f"\n🎤 WAKE WORD DETECTED #{detection_count}")
                print(f"   Wake word: {event.wake_word}")
                print(f"   Confidence: {event.confidence:.3f}")
                print(f"   Time: {time.strftime('%H:%M:%S', time.localtime(event.timestamp_ms/1000))}")
                print("\n   ✓ Detection successful! Say 'Alexa' again or press Ctrl+C to finish.")
                
        except KeyboardInterrupt:
            pass
        
        if detection_count > 0:
            print(f"\n✅ TEST PASSED: Detected 'Alexa' {detection_count} time(s)")
        else:
            print("\n⚠️ TEST INCOMPLETE: No wake word detected")
        
        return detection_count > 0
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        return False
        
    finally:
        print("\n4. Cleaning up...")
        if kwd_process:
            kwd_process.terminate()
            try:
                kwd_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                kwd_process.kill()
        
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        print("   Done")

if __name__ == "__main__":
    success = test_kwd_interactive()
    sys.exit(0 if success else 1)
