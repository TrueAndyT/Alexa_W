"""Base service class with health check implementation."""
import grpc
from concurrent import futures
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
import time
import signal
import sys
import logging
from typing import Optional
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.config_loader import ConfigLoader


class BaseService:
    """Base class for all gRPC services with health check."""
    
    def __init__(self, service_name: str, config_path: str = "config/config.ini"):
        """Initialize base service.
        
        Args:
            service_name: Name of the service (e.g., 'logger', 'tts')
            config_path: Path to configuration file
        """
        self.service_name = service_name
        self.config = ConfigLoader(config_path)
        self.port = self.config.get_int(service_name, 'port')
        
        # Setup logging
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(logging.INFO)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            f'[{service_name}] %(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # gRPC server setup
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        
        # Health service
        self.health_servicer = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self.health_servicer, self.server)
        
        # Initially NOT_SERVING
        self.set_health_status(health_pb2.HealthCheckResponse.NOT_SERVING)
        
        # Signal handling for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.running = False
    
    def set_health_status(self, status):
        """Set service health status.
        
        Args:
            status: Health status (SERVING, NOT_SERVING, etc.)
        """
        self.health_servicer.set("", status)
        self.health_servicer.set(self.service_name, status)
        
        status_name = {
            health_pb2.HealthCheckResponse.SERVING: "SERVING",
            health_pb2.HealthCheckResponse.NOT_SERVING: "NOT_SERVING",
            health_pb2.HealthCheckResponse.UNKNOWN: "UNKNOWN"
        }.get(status, "UNKNOWN")
        
        self.logger.info(f"Health status changed to: {status_name}")
    
    def setup(self):
        """Setup service-specific initialization. Override in subclasses."""
        pass
    
    def cleanup(self):
        """Cleanup service resources. Override in subclasses."""
        pass
    
    def start(self):
        """Start the gRPC service."""
        try:
            # Service-specific setup
            self.logger.info(f"Starting {self.service_name} service...")
            self.setup()
            
            # Bind to localhost only for security
            self.server.add_insecure_port(f'127.0.0.1:{self.port}')
            self.server.start()
            self.running = True
            
            self.logger.info(f"{self.service_name} service started on port {self.port}")
            
            # Set health to SERVING after successful start
            self.set_health_status(health_pb2.HealthCheckResponse.SERVING)
            
            # Keep service running
            while self.running:
                time.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Failed to start service: {e}")
            self.set_health_status(health_pb2.HealthCheckResponse.NOT_SERVING)
            raise
        finally:
            self.stop()
    
    def stop(self):
        """Stop the gRPC service."""
        if self.running:
            self.logger.info(f"Stopping {self.service_name} service...")
            self.running = False
            
            # Set health to NOT_SERVING
            self.set_health_status(health_pb2.HealthCheckResponse.NOT_SERVING)
            
            # Service-specific cleanup
            self.cleanup()
            
            # Stop gRPC server
            self.server.stop(grace=5)
            self.logger.info(f"{self.service_name} service stopped")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
