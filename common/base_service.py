"""Base service class with health check implementation."""
import os
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
from common.logger_client import LoggerClient


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
        
        # Setup logging - use centralized logger for all services except logger itself
        if service_name == 'logger':
            # Logger service uses local logging to avoid circular dependency
            self.logger = logging.getLogger(service_name)
            self.logger.setLevel(logging.DEBUG)
            self.logger.handlers = []
            
            # No console handler - logger service will handle its own console output
            self.logger_client = None
        else:
            # All other services use centralized logger
            self.logger_client = LoggerClient(service_name)
            
            # Create a wrapper logger that sends to centralized service
            self.logger = logging.getLogger(service_name)
            self.logger.setLevel(logging.DEBUG)
            self.logger.handlers = []
            
            # No console handler - all output goes through logger service
        
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
    
    def console_log(self, message):
        """Log important message to console (always shown)."""
        # Suppress all direct console output - logger service will handle formatted output
        pass
    
    def _log_info(self, event: str, message: str = None, details: str = None):
        """Log info to centralized logger."""
        if self.logger_client:
            self.logger_client.info(event, message, details)
        else:
            self.logger.info(f"{event}: {message if message else details or event}")
    
    def _log_error(self, event: str, message: str = None, details: str = None):
        """Log error to centralized logger."""
        if self.logger_client:
            self.logger_client.error(event, message, details)
        else:
            self.logger.error(f"{event}: {message if message else details or event}")
    
    def _log_warn(self, event: str, message: str = None, details: str = None):
        """Log warning to centralized logger."""
        if self.logger_client:
            self.logger_client.warn(event, message, details)
        else:
            self.logger.warning(f"{event}: {message if message else details or event}")
    
    def set_health_status(self, status):
        """Set service health status.
        
        Args:
            status: Health status (SERVING, NOT_SERVING, etc.)
        """
        self.health_servicer.set("", status)
        self.health_servicer.set(self.service_name, status)
        
        # Don't log health status for non-logger services to avoid console noise
        # Health status is monitored by the loader service anyway
        if self.service_name == 'logger':
            status_name = {
                health_pb2.HealthCheckResponse.SERVING: "SERVING",
                health_pb2.HealthCheckResponse.NOT_SERVING: "NOT_SERVING",
                health_pb2.HealthCheckResponse.UNKNOWN: "UNKNOWN"
            }.get(status, "UNKNOWN")
            self._log_info("health", f"Health status changed to: {status_name}")
    
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
            # Suppress all direct console messages
            # Don't log service_start for loader - it will log after connecting to logger service
            if self.service_name != 'loader':
                self._log_info("service_start", details=self.service_name)
            self.setup()
            
            # Bind to localhost only for security
            self.server.add_insecure_port(f'localhost:{self.port}')
            self.server.start()
            self.running = True
            
            # Suppress all direct console messages
            # Send proper event to logger for all services
            if self.service_name != 'loader':  # Loader will log this itself after logger is ready
                self._log_info("service_start", f"{self.service_name} service loaded (PID={os.getpid()}, port={self.port})")
            
            # Set health to SERVING after successful start
            self.set_health_status(health_pb2.HealthCheckResponse.SERVING)
            
            # Keep service running
            while self.running:
                time.sleep(1)
                
        except Exception as e:
            self._log_error("service_error", details=str(e))
            self.set_health_status(health_pb2.HealthCheckResponse.NOT_SERVING)
            raise
        finally:
            self.stop()
    
    def stop(self):
        """Stop the gRPC service."""
        if self.running:
            # Suppress all direct console messages
            # Don't log service_stop for loader if logger isn't connected
            if self.service_name != 'loader':
                self._log_info("service_stop", details=self.service_name)
            self.running = False
            
            # Set health to NOT_SERVING
            self.set_health_status(health_pb2.HealthCheckResponse.NOT_SERVING)
            
            # Service-specific cleanup
            self.cleanup()
            
            # Stop gRPC server
            self.server.stop(grace=5)
            # Suppress all direct console messages
            if self.service_name != 'loader':
                self._log_info("service_stop", f"{self.service_name} service stopped")
            
            # Close logger client connection
            if self.logger_client:
                self.logger_client.close()
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        # Suppress all direct console messages
        # Don't log signals for loader - it has its own handling
        if self.service_name != 'loader':
            self._log_info("signal", f"Received signal {signum}, shutting down")
        self.running = False
