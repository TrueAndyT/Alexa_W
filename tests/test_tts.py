#!/usr/bin/env python3
"""Test client for TTS service."""
import sys
from pathlib import Path
import grpc
import time
import uuid
import threading

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_tts_speak():
    """Test TTS Speak (unary) functionality."""
    print("Testing TTS Service - Speak...")
    
    # Check health first
    health_client = HealthClient(port=5006)
    print("\nChecking health status...")
    
    # Wait for service to be ready
    if health_client.wait_for_serving(timeout=10):
        print("✓ TTS service is SERVING")
    else:
        print("✗ TTS service is not ready")
        return False
    
    # Connect to TTS service
    channel = grpc.insecure_channel('127.0.0.1:5006')
    stub = services_pb2_grpc.TtsServiceStub(channel)
    
    # Create a dialog ID
    dialog_id = f"test_{uuid.uuid4().hex[:8]}"
    print(f"\nDialog ID: {dialog_id}")
    
    # Test phrases
    test_phrases = [
        "Hi, Master!",
        "Hello! How can I help you today?",
        "The quick brown fox jumps over the lazy dog."
    ]
    
    try:
        for i, text in enumerate(test_phrases, 1):
            print(f"\nTest {i}: {text}")
            
            # Call Speak
            request = services_pb2.SpeakRequest(
                text=text,
                dialog_id=dialog_id,
                voice="af_heart"
            )
            
            response = stub.Speak(request)
            
            if response.success:
                print(f"✓ Success: {response.message}")
                print(f"  Duration: {response.duration_ms:.0f}ms")
            else:
                print(f"✗ Failed: {response.message}")
                
            # Wait between tests
            time.sleep(2)
            
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
        return False
    finally:
        channel.close()
        health_client.close()
    
    print("\nSpeak test complete!")
    return True


def test_playback_events():
    """Test PlaybackEvents streaming."""
    print("\nTesting PlaybackEvents...")
    
    # Connect to TTS service
    channel = grpc.insecure_channel('127.0.0.1:5006')
    stub = services_pb2_grpc.TtsServiceStub(channel)
    
    dialog_id = f"events_{uuid.uuid4().hex[:8]}"
    
    # Start event listener in background
    events = []
    
    def listen_events():
        try:
            dialog_ref = services_pb2.DialogRef(
                dialog_id=dialog_id,
                turn_number=1
            )
            
            for event in stub.PlaybackEvents(dialog_ref):
                timestamp = time.strftime('%H:%M:%S', time.localtime(event.timestamp_ms / 1000))
                print(f"  Event: {event.event_type} at {timestamp} (chunk {event.chunk_number})")
                events.append(event)
                
                if event.event_type == "finished":
                    break
                    
        except grpc.RpcError as e:
            print(f"Events error: {e}")
            
    # Start listener thread
    listener = threading.Thread(target=listen_events, daemon=True)
    listener.start()
    
    # Give it time to subscribe
    time.sleep(0.5)
    
    # Trigger speak
    print(f"Speaking with dialog {dialog_id}...")
    request = services_pb2.SpeakRequest(
        text="This is a test of playback events.",
        dialog_id=dialog_id,
        voice="af_heart"
    )
    
    response = stub.Speak(request)
    
    # Wait for events
    listener.join(timeout=5)
    
    print(f"Received {len(events)} events")
    channel.close()
    
    return len(events) > 0


def test_speak_stream():
    """Test SpeakStream (client-streaming) functionality."""
    print("\nTesting TTS Service - SpeakStream...")
    
    # Connect to TTS service
    channel = grpc.insecure_channel('127.0.0.1:5006')
    stub = services_pb2_grpc.TtsServiceStub(channel)
    
    dialog_id = f"stream_{uuid.uuid4().hex[:8]}"
    
    # Simulate LLM chunks
    chunks = [
        "Hello! ",
        "I'm your voice assistant. ",
        "How can I ",
        "help you today?"
    ]
    
    def generate_chunks():
        """Generate stream of chunks."""
        for i, text in enumerate(chunks):
            yield services_pb2.LlmChunk(
                text=text,
                eot=False,
                dialog_id=dialog_id
            )
            time.sleep(0.2)  # Simulate LLM delay
            
        # Send EOT
        yield services_pb2.LlmChunk(
            text="",
            eot=True,
            dialog_id=dialog_id
        )
        
    try:
        print(f"Streaming {len(chunks)} chunks...")
        start_time = time.time()
        
        response = stub.SpeakStream(generate_chunks())
        
        elapsed = (time.time() - start_time) * 1000
        
        if response.success:
            print(f"✓ Success: {response.message}")
            print(f"  Total duration: {response.duration_ms:.0f}ms")
            print(f"  Elapsed time: {elapsed:.0f}ms")
        else:
            print(f"✗ Failed: {response.message}")
            
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
        return False
    finally:
        channel.close()
        
    print("\nSpeakStream test complete!")
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test TTS service')
    parser.add_argument('--events', action='store_true',
                        help='Test playback events')
    parser.add_argument('--stream', action='store_true',
                        help='Test streaming mode')
    parser.add_argument('--all', action='store_true',
                        help='Run all tests')
    
    args = parser.parse_args()
    
    if args.all:
        test_tts_speak()
        test_playback_events()
        test_speak_stream()
    elif args.events:
        test_playback_events()
    elif args.stream:
        test_speak_stream()
    else:
        test_tts_speak()
