"""Health check client utilities for gRPC services."""
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from typing import Optional, Dict
import time


class HealthClient:
    """Client for checking service health status."""
    
    def __init__(self, host: str = "127.0.0.1", port: int = None):
        """Initialize health client.
        
        Args:
            host: Service host address
            port: Service port number
        """
        self.host = host
        self.port = port
        self.channel = None
        self.stub = None
        
        if port:
            self._connect()
    
    def _connect(self):
        """Create gRPC channel and stub."""
        if self.port:
            self.channel = grpc.insecure_channel(f"{self.host}:{self.port}")
            self.stub = health_pb2_grpc.HealthStub(self.channel)
    
    def check(self, service: str = "") -> str:
        """Check health status of a service.
        
        Args:
            service: Service name (empty for overall health)
            
        Returns:
            Health status: UNKNOWN, SERVING, NOT_SERVING, or SERVICE_UNKNOWN
        """
        if not self.stub:
            return "UNKNOWN"
        
        try:
            request = health_pb2.HealthCheckRequest(service=service)
            response = self.stub.Check(request, timeout=2.0)
            
            status_map = {
                health_pb2.HealthCheckResponse.UNKNOWN: "UNKNOWN",
                health_pb2.HealthCheckResponse.SERVING: "SERVING",
                health_pb2.HealthCheckResponse.NOT_SERVING: "NOT_SERVING",
                health_pb2.HealthCheckResponse.SERVICE_UNKNOWN: "SERVICE_UNKNOWN"
            }
            
            return status_map.get(response.status, "UNKNOWN")
            
        except grpc.RpcError as e:
            return "UNKNOWN"
        except Exception as e:
            return "UNKNOWN"
    
    def wait_for_serving(self, service: str = "", timeout: float = 30.0,
                        check_interval: float = 0.5) -> bool:
        """Wait for service to become SERVING.
        
        Args:
            service: Service name
            timeout: Maximum wait time in seconds
            check_interval: Time between health checks
            
        Returns:
            True if service became SERVING, False if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.check(service)
            if status == "SERVING":
                return True
            time.sleep(check_interval)
        
        return False
    
    def close(self):
        """Close gRPC channel."""
        if self.channel:
            self.channel.close()
            self.channel = None
            self.stub = None


class MultiHealthChecker:
    """Check health of multiple services."""
    
    def __init__(self):
        """Initialize multi-service health checker."""
        self.clients = {}
    
    def add_service(self, name: str, port: int, host: str = "127.0.0.1"):
        """Add a service to monitor.
        
        Args:
            name: Service name
            port: Service port
            host: Service host
        """
        self.clients[name] = HealthClient(host, port)
    
    def check_all(self) -> Dict[str, str]:
        """Check health of all services.
        
        Returns:
            Dictionary of service name to health status
        """
        results = {}
        for name, client in self.clients.items():
            results[name] = client.check()
        return results
    
    def wait_for_all(self, timeout: float = 30.0) -> bool:
        """Wait for all services to become SERVING.
        
        Args:
            timeout: Maximum wait time in seconds
            
        Returns:
            True if all services became SERVING, False if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            statuses = self.check_all()
            if all(status == "SERVING" for status in statuses.values()):
                return True
            time.sleep(0.5)
        
        return False
    
    def close_all(self):
        """Close all health check connections."""
        for client in self.clients.values():
            client.close()
        self.clients.clear()
