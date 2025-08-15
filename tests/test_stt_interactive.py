#!/usr/bin/env python3
"""Interactive STT test - speak and see transcription in real-time."""

import sys
import time
import grpc
import threading
import subprocess
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_stt_interactive():
    """Test STT service with interactive recording and transcription."""
    
    print("\n" + "="*80)
    print("INTERACTIVE STT SERVICE TEST")
    print("="*80)
    
    stt_process = None
    logger_process = None
    
    try:
        # Clean up any existing services
        print("\n1. Cleaning up existing services...")
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        time.sleep(2)
        
        # Start Logger service (required by STT)
        print("\n2. Starting Logger service...")
        venv_python = Path('.venv/bin/python').absolute()
        logger_log = open('logger_service.log', 'w')
        logger_process = subprocess.Popen(
            [str(venv_python), 'services/logger/logger_service.py'],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for Logger to be ready
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service ready")
                break
            time.sleep(1)
        
        # Start STT service
        print("\n3. Starting STT service (Whisper)...")
        print("   Loading Whisper model - this may take a moment...")
        stt_log = open('stt_service.log', 'w')
        stt_process = subprocess.Popen(
            [str(venv_python), 'services/stt/stt_service.py'],
            stdout=stt_log,
            stderr=subprocess.STDOUT
        )
        
        # Wait for STT to be ready
        print("   Waiting for STT service to be ready...")
        stt_health = HealthClient(port=5004)
        for i in range(30):
            status = stt_health.check()
            if status == "SERVING":
                print("   ✓ STT service ready")
                break
            elif i % 5 == 0:
                print(f"   Still loading... ({i}s)")
            time.sleep(1)
        else:
            print("   ✗ STT service failed to start")
            # Check log for errors
            with open('stt_service.log', 'r') as f:
                print("\nLast lines from STT log:")
                for line in f.readlines()[-20:]:
                    print(f"  {line.strip()}")
            return False
        
        # Connect to STT service
        print("\n4. Connecting to STT service...")
        channel = grpc.insecure_channel('127.0.0.1:5004')
        stt_stub = services_pb2_grpc.SttServiceStub(channel)
        
        # Test loop
        print("\n" + "="*80)
        print("READY FOR TESTING")
        print("="*80)
        print("\nInstructions:")
        print("  1. Press ENTER to start recording")
        print("  2. Speak clearly into your microphone")
        print("  3. Press ENTER again to stop recording")
        print("  4. See the transcription")
        print("  5. Type 'quit' to exit\n")
        
        test_num = 0
        while True:
            user_input = input("\n[Press ENTER to start recording, or 'quit' to exit]: ")
            if user_input.lower() == 'quit':
                break
            
            test_num += 1
            dialog_id = f"test_{test_num}"
            
            print(f"\n--- Test #{test_num} ---")
            
            # Start recording
            print("🎤 RECORDING - Speak now...")
            print("   (Press ENTER when done speaking)")
            
            start_response = stt_stub.Start(services_pb2.StartRequest(
                dialog_id=dialog_id,
                turn_number=1
            ))
            
            if not start_response.success:
                print(f"✗ Failed to start recording: {start_response.message}")
                continue
            
            # Create a thread to listen for results
            transcription_result = {'text': '', 'final': False}
            stop_listening = threading.Event()
            
            def listen_for_results():
                """Listen for STT results in background."""
                try:
                    dialog_ref = services_pb2.DialogRef(
                        dialog_id=dialog_id,
                        turn_number=1
                    )
                    
                    for result in stt_stub.Results(dialog_ref):
                        if stop_listening.is_set():
                            break
                            
                        if result.final:
                            transcription_result['text'] = result.text
                            transcription_result['final'] = True
                            print(f"\n📝 Final transcription: \"{result.text}\"")
                            break
                        else:
                            # Show partial results
                            print(f"\r   Partial: {result.text}", end='', flush=True)
                            
                except Exception as e:
                    print(f"\n✗ Error listening for results: {e}")
            
            # Start listening thread
            listener_thread = threading.Thread(target=listen_for_results, daemon=True)
            listener_thread.start()
            
            # Wait for user to stop recording
            input()  # Wait for ENTER press
            
            # Stop recording
            print("\n⏹️  Stopping recording...")
            stop_response = stt_stub.Stop(services_pb2.StopRequest(
                dialog_id=dialog_id
            ))
            
            if not stop_response.success:
                print(f"✗ Failed to stop recording: {stop_response.message}")
            
            # Wait for final transcription
            timeout = 5
            start_wait = time.time()
            while not transcription_result['final'] and (time.time() - start_wait) < timeout:
                time.sleep(0.1)
            
            # Stop the listener thread
            stop_listening.set()
            listener_thread.join(timeout=1)
            
            if transcription_result['final']:
                print(f"\n✓ Transcription complete!")
                print(f"  Text: \"{transcription_result['text']}\"")
                print(f"  Length: {len(transcription_result['text'])} characters")
            else:
                print("\n✗ No transcription received (timeout)")
                print("\nChecking STT service log...")
                with open('stt_service.log', 'r') as f:
                    lines = f.readlines()
                    print("Last 10 lines from STT log:")
                    for line in lines[-10:]:
                        print(f"  {line.strip()}")
        
        print("\n" + "="*80)
        print("Test completed!")
        return True
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return False
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        
        # Show STT log on error
        if Path('stt_service.log').exists():
            print("\nSTT service log:")
            with open('stt_service.log', 'r') as f:
                for line in f.readlines()[-20:]:
                    print(f"  {line.strip()}")
        
        return False
        
    finally:
        # Cleanup
        print("\n5. Cleaning up...")
        
        if stt_process:
            stt_process.terminate()
            try:
                stt_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                stt_process.kill()
        
        if logger_process:
            logger_process.terminate()
            try:
                logger_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger_process.kill()
        
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)


if __name__ == "__main__":
    success = test_stt_interactive()
    sys.exit(0 if success else 1)
