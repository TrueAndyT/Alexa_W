#!/usr/bin/env python3
"""Test client for STT service."""
import sys
from pathlib import Path
import grpc
import time
import uuid

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_stt_service():
    """Test STT service functionality."""
    print("Testing STT Service...")
    
    # Check health first
    health_client = HealthClient(port=5004)
    print("\nChecking health status...")
    
    # Wait for service to be ready
    if health_client.wait_for_serving(timeout=30):
        print("✓ STT service is SERVING")
    else:
        print("✗ STT service is not ready")
        return False
    
    # Connect to STT service
    channel = grpc.insecure_channel('127.0.0.1:5004')
    stub = services_pb2_grpc.SttServiceStub(channel)
    
    # Create a dialog ID
    dialog_id = f"test_{uuid.uuid4().hex[:8]}"
    print(f"\nDialog ID: {dialog_id}")
    
    # Start recognition
    print("\nStarting speech recognition...")
    start_request = services_pb2.StartRequest(
        dialog_id=dialog_id,
        turn_number=1
    )
    response = stub.Start(start_request)
    print(f"Start: {response.message}")
    
    if not response.success:
        print("Failed to start STT")
        return False
    
    # Stream results
    print("\n" + "="*50)
    print("SPEAK NOW! (Recognition will finalize after ~2s of silence)")
    print("="*50 + "\n")
    
    try:
        # Subscribe to results
        dialog_ref = services_pb2.DialogRef(
            dialog_id=dialog_id,
            turn_number=1
        )
        results_stream = stub.Results(dialog_ref)
        
        # Wait for result
        for result in results_stream:
            timestamp = time.strftime('%H:%M:%S', time.localtime(result.timestamp_ms / 1000))
            
            if result.final:
                print(f"\n[{timestamp}] FINAL TRANSCRIPTION:")
                print(f"  Text: \"{result.text}\"")
                print(f"  Confidence: {result.confidence:.3f}")
                break
            else:
                # For interim results (if implemented)
                print(f"[{timestamp}] Interim: {result.text}")
        
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        # Stop recognition
        print("\nStopping recognition...")
        stop_request = services_pb2.StopRequest(dialog_id=dialog_id)
        response = stub.Stop(stop_request)
        print(f"Stop: {response.message}")
        
        channel.close()
        health_client.close()
    
    print("\nTest complete!")
    return True


def test_continuous_recognition():
    """Test continuous recognition with multiple turns."""
    print("Testing Continuous Recognition...")
    
    # Connect to STT service
    channel = grpc.insecure_channel('127.0.0.1:5004')
    stub = services_pb2_grpc.SttServiceStub(channel)
    
    dialog_id = f"continuous_{uuid.uuid4().hex[:8]}"
    
    try:
        for turn in range(1, 4):
            print(f"\n--- Turn {turn} ---")
            
            # Start recognition
            start_request = services_pb2.StartRequest(
                dialog_id=dialog_id,
                turn_number=turn
            )
            response = stub.Start(start_request)
            
            if response.success:
                print(f"Speak now (turn {turn})...")
                
                # Get result
                dialog_ref = services_pb2.DialogRef(
                    dialog_id=dialog_id,
                    turn_number=turn
                )
                
                for result in stub.Results(dialog_ref):
                    if result.final:
                        print(f"Transcription: \"{result.text}\"")
                        break
                
                # Stop this turn
                stop_request = services_pb2.StopRequest(dialog_id=dialog_id)
                stub.Stop(stop_request)
                
                time.sleep(1)  # Brief pause between turns
    
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        channel.close()
    
    print("\nContinuous test complete!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test STT service')
    parser.add_argument('--continuous', action='store_true',
                        help='Test continuous recognition with multiple turns')
    
    args = parser.parse_args()
    
    if args.continuous:
        test_continuous_recognition()
    else:
        test_stt_service()
