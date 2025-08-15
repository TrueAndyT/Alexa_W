#!/usr/bin/env python3
"""Test client for Loader service."""
import sys
from pathlib import Path
import grpc
import time

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


def test_loader_service():
    """Test Loader service functionality."""
    print("Testing Loader Service...")
    
    # Check health first
    health_client = HealthClient(port=5002)
    print("\nChecking health status...")
    
    # Wait for service to be ready
    if health_client.wait_for_serving(timeout=10):
        print("✓ Loader service is SERVING")
    else:
        print("✗ Loader service is not ready")
        return False
    
    # Connect to Loader service
    channel = grpc.insecure_channel('127.0.0.1:5002')
    stub = services_pb2_grpc.LoaderServiceStub(channel)
    
    try:
        # Get system status
        print("\nGetting system status...")
        status = stub.GetStatus(services_pb2.Empty())
        
        print(f"System State: {status.state}")
        print(f"Uptime: {status.uptime_ms / 1000:.1f}s")
        print(f"VRAM Used: {status.vram_used_mb}MB")
        
        print("\nService Health:")
        for service, health in status.service_health.items():
            print(f"  {service}: {health}")
            
        # Get PIDs
        print("\nGetting service PIDs...")
        pids_response = stub.GetPids(services_pb2.Empty())
        
        print("Service PIDs:")
        for service, pid in pids_response.pids.items():
            print(f"  {service}: {pid}")
            
        # Test service control (optional - be careful!)
        # print("\nTesting service control...")
        # response = stub.StopService(services_pb2.ServiceRequest(service_name="logger"))
        # print(f"Stop logger: {response.message}")
        # 
        # time.sleep(2)
        # 
        # response = stub.StartService(services_pb2.ServiceRequest(service_name="logger"))
        # print(f"Start logger: {response.message}")
        
    except grpc.RpcError as e:
        print(f"RPC error: {e}")
        return False
    finally:
        channel.close()
        health_client.close()
    
    print("\nTest complete!")
    return True


if __name__ == "__main__":
    test_loader_service()
