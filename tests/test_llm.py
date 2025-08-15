#!/usr/bin/env python3
"""Test client for LLM service."""
import sys
from pathlib import Path
import grpc
import time
import uuid

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_llm_service():
    """Test LLM service functionality."""
    print("Testing LLM Service...")
    
    # Check health first
    health_client = HealthClient(port=5005)
    print("\nChecking health status...")
    
    # Wait for service to be ready
    if health_client.wait_for_serving(timeout=30):
        print("✓ LLM service is SERVING")
    else:
        print("✗ LLM service is not ready")
        return False
    
    # Connect to LLM service
    channel = grpc.insecure_channel('127.0.0.1:5005')
    stub = services_pb2_grpc.LlmServiceStub(channel)
    
    # Create a dialog ID
    dialog_id = f"test_{uuid.uuid4().hex[:8]}"
    print(f"\nDialog ID: {dialog_id}")
    
    # Test streaming completion
    print("\nSending completion request...")
    request = services_pb2.CompleteRequest(
        text="Hello! What's the weather like today?",
        dialog_id=dialog_id,
        turn_number=1,
        conversation_history=""
    )
    
    try:
        # Track timing
        start_time = time.time()
        first_token_time = None
        full_response = []
        token_count = 0
        
        print("\n" + "="*50)
        print("STREAMING RESPONSE:")
        print("="*50)
        
        # Stream response
        for response in stub.Complete(request):
            if response.text:
                full_response.append(response.text)
                print(response.text, end='', flush=True)
                
                # Track first token time
                if first_token_time is None:
                    first_token_time = time.time()
                    latency = (first_token_time - start_time) * 1000
                    print(f"\n[First token latency: {latency:.0f}ms]\n", end='')
                    
            if response.eot:
                token_count = response.token_count
                total_time = response.latency_ms
                break
        
        print("\n" + "="*50)
        
        # Show statistics
        print(f"\nStatistics:")
        print(f"  Total tokens: {token_count}")
        print(f"  Total time: {total_time:.0f}ms")
        print(f"  Response length: {len(''.join(full_response))} chars")
        
        # Test second turn
        print("\n\nTesting second turn...")
        request2 = services_pb2.CompleteRequest(
            text="Thanks! Can you be more specific?",
            dialog_id=dialog_id,
            turn_number=2,
            conversation_history="User: Hello! What's the weather like today?\nAssistant: " + ''.join(full_response)
        )
        
        print("\n" + "="*50)
        print("SECOND RESPONSE:")
        print("="*50)
        
        for response in stub.Complete(request2):
            if response.text:
                print(response.text, end='', flush=True)
            if response.eot:
                break
                
        print("\n" + "="*50)
        
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
        return False
    except KeyboardInterrupt:
        print("\n\nStopped by user")
    finally:
        channel.close()
        health_client.close()
    
    print("\nTest complete!")
    return True


def test_error_handling():
    """Test LLM service error handling."""
    print("\nTesting error handling...")
    
    # Connect to LLM service
    channel = grpc.insecure_channel('127.0.0.1:5005')
    stub = services_pb2_grpc.LlmServiceStub(channel)
    
    try:
        # Test with empty request
        request = services_pb2.CompleteRequest(
            text="",
            dialog_id="test_error",
            turn_number=1
        )
        
        for response in stub.Complete(request):
            if response.text:
                print(f"Response: {response.text}")
                
    except grpc.RpcError as e:
        print(f"Expected error: {e}")
    finally:
        channel.close()
    
    print("Error handling test complete")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test LLM service')
    parser.add_argument('--error', action='store_true',
                        help='Test error handling')
    
    args = parser.parse_args()
    
    if args.error:
        test_error_handling()
    else:
        test_llm_service()
