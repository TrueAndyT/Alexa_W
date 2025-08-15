#!/usr/bin/env python3
"""Test client for KWD service."""
import sys
from pathlib import Path
import grpc
import time

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_kwd_service():
    """Test KWD service functionality."""
    print("Testing KWD Service...")
    
    # Check health first
    health_client = HealthClient(port=5003)
    print("\nChecking health status...")
    
    # Wait for service to be ready
    if health_client.wait_for_serving(timeout=10):
        print("✓ KWD service is SERVING")
    else:
        print("✗ KWD service is not ready")
        return False
    
    # Connect to KWD service
    channel = grpc.insecure_channel('127.0.0.1:5003')
    stub = services_pb2_grpc.KwdServiceStub(channel)
    
    # Test Enable/Disable
    print("\nTesting Enable/Disable...")
    
    # Disable
    response = stub.Disable(services_pb2.Empty())
    print(f"Disable: {response.message}")
    
    # Enable
    response = stub.Enable(services_pb2.Empty())
    print(f"Enable: {response.message}")
    
    # Stream events
    print("\nListening for wake word events (say 'Alexa' to test)...")
    print("Press Ctrl+C to stop\n")
    
    try:
        # Subscribe to events
        events_stream = stub.Events(services_pb2.Empty())
        
        for event in events_stream:
            timestamp = time.strftime('%H:%M:%S', time.localtime(event.timestamp_ms / 1000))
            print(f"[{timestamp}] Wake detected: '{event.wake_word}' (confidence: {event.confidence:.3f})")
            
    except KeyboardInterrupt:
        print("\n\nStopping event stream...")
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
    finally:
        channel.close()
        health_client.close()
    
    print("\nTest complete!")
    return True


if __name__ == "__main__":
    test_kwd_service()
