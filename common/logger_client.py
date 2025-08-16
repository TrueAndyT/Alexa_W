"""Client for centralized logging service."""
import grpc
import time
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc


class LoggerClient:
    """Client for sending logs to the centralized logger service."""
    
    def __init__(self, service_name: str, port: int = 5001):
        """Initialize logger client.
        
        Args:
            service_name: Name of the service using this client
            port: Logger service port (default 5001)
        """
        self.service_name = service_name
        self.port = port
        self.channel = None
        self.stub = None
        self._connect()
    
    def _connect(self):
        """Connect to logger service."""
        try:
            self.channel = grpc.insecure_channel(f'localhost:{self.port}')
            self.stub = services_pb2_grpc.LoggerServiceStub(self.channel)
        except Exception as e:
            # Fallback to console if can't connect
            print(f"[{self.service_name}] Failed to connect to logger service: {e}")
    
    def log(self, event: str, message: str = None, level: str = "INFO", details: str = None):
        """Send log to logger service.
        
        Args:
            event: Event type (startup, shutdown, error, etc.)
            message: Log message (if None, will use event as message)
            level: Log level (INFO, WARN, ERROR, FATAL)
            details: Optional details for formatting
        """
        # If no message provided, use details or event name
        if message is None:
            message = details if details else event
        
        if not self.stub:
            # Fallback to console
            print(f"[{self.service_name}] {level}: {event} - {message}")
            return
        
        try:
            request = services_pb2.AppLogRequest(
                service=self.service_name,
                event=event,
                message=message,
                level=level,
                timestamp_ms=int(time.time() * 1000)
            )
            self.stub.WriteApp(request)
        except grpc.RpcError:
            # Fallback to console if RPC fails
            print(f"[{self.service_name}] {level}: {event} - {message}")
    
    def info(self, event: str, message: str = None, details: str = None):
        """Log info message."""
        self.log(event, message, "INFO", details)
    
    def warn(self, event: str, message: str = None, details: str = None):
        """Log warning message."""
        self.log(event, message, "WARNING", details)
    
    def error(self, event: str, message: str = None, details: str = None):
        """Log error message."""
        self.log(event, message, "ERROR", details)
    
    def fatal(self, event: str, message: str = None, details: str = None):
        """Log fatal message."""
        self.log(event, message, "FATAL", details)
    
    def new_dialog(self) -> Optional[tuple]:
        """Create new dialog session.
        
        Returns:
            Tuple of (dialog_id, file_path) or None if failed
        """
        if not self.stub:
            return None
        
        try:
            request = services_pb2.NewDialogRequest(
                timestamp_ms=int(time.time() * 1000)
            )
            response = self.stub.NewDialog(request)
            return response.dialog_id, response.file_path
        except grpc.RpcError as e:
            print(f"[{self.service_name}] Failed to create dialog: {e}")
            return None
    
    def log_dialog(self, dialog_id: str, speaker: str, text: str):
        """Log dialog entry.
        
        Args:
            dialog_id: Dialog identifier
            speaker: Speaker (USER or ASSISTANT)
            text: Spoken/transcribed text
        """
        if not self.stub:
            print(f"[{self.service_name}] Dialog {dialog_id} - {speaker}: {text}")
            return
        
        try:
            request = services_pb2.DialogLogRequest(
                dialog_id=dialog_id,
                speaker=speaker,
                text=text,
                timestamp_ms=int(time.time() * 1000)
            )
            self.stub.WriteDialog(request)
        except grpc.RpcError as e:
            print(f"[{self.service_name}] Failed to log dialog: {e}")
    
    def log_memory(self, vram_used_mb: int, vram_free_mb: int, details: str = ""):
        """Log memory usage.
        
        Args:
            vram_used_mb: VRAM used in MB
            vram_free_mb: VRAM free in MB  
            details: Optional details about what's using memory
        """
        message = f"VRAM: {vram_used_mb}MB used, {vram_free_mb}MB free"
        if details:
            message += f" - {details}"
        self.log("memory", message, "INFO")
    
    def close(self):
        """Close connection to logger service."""
        if self.channel:
            self.channel.close()
