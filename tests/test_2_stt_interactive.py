#!/usr/bin/env python3
"""Interactive STT test - Speak to test transcription."""

import sys
import time
import grpc
import threading
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient

def test_stt_interactive():
    print("\n" + "="*80)
    print("INTERACTIVE STT TEST - SPEECH TO TEXT")
    print("="*80)
    
    processes = {}
    
    try:
        # Clean up
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(2)
        
        venv_python = Path('.venv/bin/python').absolute()
        
        # Start Logger (required by STT)
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
        
        # Start STT service
        print("\n3. Starting STT service (Whisper)...")
        print("   Loading model - this may take a moment...")
        stt_log = open('stt_service.log', 'w')
        processes['stt'] = subprocess.Popen(
            [str(venv_python), 'services/stt/stt_service.py'],
            stdout=stt_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for STT
        stt_health = HealthClient(port=5004)
        for i in range(30):
            if stt_health.check() == "SERVING":
                print("   ✓ STT service ready")
                break
            if i % 5 == 0 and i > 0:
                print(f"   Still loading... ({i}s)")
            time.sleep(1)
        
        # Connect to STT
        channel = grpc.insecure_channel('127.0.0.1:5004')
        stt_stub = services_pb2_grpc.SttServiceStub(channel)
        
        print("\n" + "="*80)
        print("READY FOR TESTING")
        print("="*80)
        print("\n📢 INSTRUCTIONS:")
        print("   1. Press ENTER to start recording")
        print("   2. Speak clearly into your microphone")
        print("   3. Press ENTER to stop recording")
        print("   4. See your transcription")
        print("   5. Type 'quit' to exit\n")
        print("="*80)
        
        test_count = 0
        successful_tests = 0
        
        while True:
            user_input = input("\n[Press ENTER to record, or 'quit' to exit]: ")
            if user_input.lower() == 'quit':
                break
            
            test_count += 1
            print(f"\n--- Test #{test_count} ---")
            
            # Start recording
            print("🔴 RECORDING - Speak now...")
            dialog_id = f"test_{test_count}"
            
            start_response = stt_stub.Start(services_pb2.StartRequest(
                dialog_id=dialog_id,
                turn_number=1
            ))
            
            if not start_response.success:
                print(f"✗ Failed to start: {start_response.message}")
                continue
            
            # Listen for results in background
            result_text = {'text': '', 'received': False}
            stop_listening = threading.Event()
            
            def listen_results():
                try:
                    dialog_ref = services_pb2.DialogRef(
                        dialog_id=dialog_id,
                        turn_number=1
                    )
                    for result in stt_stub.Results(dialog_ref):
                        if stop_listening.is_set():
                            break
                        if result.final:
                            result_text['text'] = result.text
                            result_text['received'] = True
                            break
                        else:
                            print(f"\r   Partial: {result.text}", end='', flush=True)
                except Exception as e:
                    print(f"\n✗ Error: {e}")
            
            listener = threading.Thread(target=listen_results, daemon=True)
            listener.start()
            
            # Wait for user to stop
            input("\n[Press ENTER to stop recording]")
            
            # Stop recording
            print("⏹️  Stopping...")
            stt_stub.Stop(services_pb2.StopRequest(dialog_id=dialog_id))
            
            # Wait for result
            time.sleep(2)
            stop_listening.set()
            listener.join(timeout=1)
            
            if result_text['received']:
                print(f"\n✅ TRANSCRIPTION: \"{result_text['text']}\"")
                successful_tests += 1
            else:
                print("\n⚠️ No transcription received")
        
        print(f"\n✅ TEST COMPLETE: {successful_tests}/{test_count} successful transcriptions")
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
        
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        print("   Done")

if __name__ == "__main__":
    success = test_stt_interactive()
    sys.exit(0 if success else 1)
