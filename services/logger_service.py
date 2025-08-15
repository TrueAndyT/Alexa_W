"""Logger service for centralized logging."""
import sys
import os
from pathlib import Path
import time
from datetime import datetime
import json
import threading
import grpc
from concurrent import futures

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2


class LoggerServicer(services_pb2_grpc.LoggerServiceServicer):
    """Implementation of Logger service RPCs."""
    
    def __init__(self, log_manager):
        """Initialize logger servicer.
        
        Args:
            log_manager: LogManager instance
        """
        self.log_manager = log_manager
    
    def WriteApp(self, request, context):
        """Write application log entry."""
        success = self.log_manager.write_app_log(
            service=request.service,
            event=request.event,
            message=request.message,
            level=request.level,
            timestamp_ms=request.timestamp_ms
        )
        
        return services_pb2.Status(
            success=success,
            message="Log written" if success else "Failed to write log"
        )
    
    def NewDialog(self, request, context):
        """Create new dialog log file."""
        dialog_id, file_path = self.log_manager.new_dialog(request.timestamp_ms)
        
        return services_pb2.DialogResponse(
            dialog_id=dialog_id,
            file_path=file_path
        )
    
    def WriteDialog(self, request, context):
        """Write dialog log entry."""
        success = self.log_manager.write_dialog_log(
            dialog_id=request.dialog_id,
            speaker=request.speaker,
            text=request.text,
            timestamp_ms=request.timestamp_ms
        )
        
        return services_pb2.Status(
            success=success,
            message="Dialog log written" if success else "Failed to write dialog log"
        )


class LogManager:
    """Manages application and dialog logs."""
    
    def __init__(self, config):
        """Initialize log manager.
        
        Args:
            config: ConfigLoader instance
        """
        self.config = config
        self.log_dir = Path(config.get('system', 'log_dir', 'logs'))
        self.log_dir.mkdir(exist_ok=True)
        
        self.app_log_file = self.log_dir / config.get('logger', 'app_log_file', 'app.log')
        self.dialog_prefix = config.get('logger', 'dialog_log_prefix', 'dialog_')
        
        self.rotation_size_mb = config.get_int('logger', 'rotation_size_mb', 100)
        self.rotation_count = config.get_int('logger', 'rotation_count', 5)
        
        self.lock = threading.Lock()
        self.dialog_files = {}
        
        # Reset app log on start
        self._reset_app_log()
    
    def _reset_app_log(self):
        """Reset application log file."""
        with self.lock:
            # Rotate existing log if it exists
            if self.app_log_file.exists():
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_file = self.log_dir / f"app_{timestamp}.log"
                self.app_log_file.rename(backup_file)
                
                # Clean up old backups
                self._cleanup_old_logs('app_*.log')
            
            # Create new app log
            self.app_log_file.touch()
    
    def _cleanup_old_logs(self, pattern: str):
        """Clean up old log files based on rotation count.
        
        Args:
            pattern: File pattern to match
        """
        log_files = sorted(self.log_dir.glob(pattern))
        if len(log_files) > self.rotation_count:
            for old_file in log_files[:-self.rotation_count]:
                old_file.unlink()
    
    def write_app_log(self, service: str, event: str, message: str, 
                      level: str = "INFO", timestamp_ms: int = None) -> bool:
        """Write to application log.
        
        Args:
            service: Service name
            event: Event type
            message: Log message
            level: Log level (INFO, WARN, ERROR, FATAL)
            timestamp_ms: Timestamp in milliseconds
            
        Returns:
            True if successful
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000).isoformat()
        
        log_entry = {
            'timestamp': timestamp,
            'timestamp_ms': timestamp_ms,
            'level': level,
            'service': service,
            'event': event,
            'message': message
        }
        
        try:
            with self.lock:
                with open(self.app_log_file, 'a') as f:
                    f.write(json.dumps(log_entry) + '\n')
                
                # Check rotation
                if self.app_log_file.stat().st_size > self.rotation_size_mb * 1024 * 1024:
                    self._reset_app_log()
                
                return True
        except Exception as e:
            print(f"Error writing app log: {e}")
            return False
    
    def new_dialog(self, timestamp_ms: int = None) -> tuple:
        """Create new dialog log file.
        
        Args:
            timestamp_ms: Timestamp in milliseconds
            
        Returns:
            Tuple of (dialog_id, file_path)
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000)
        dialog_id = timestamp.strftime('%Y%m%d_%H%M%S_') + str(timestamp_ms % 1000).zfill(3)
        
        file_name = f"{self.dialog_prefix}{dialog_id}.log"
        file_path = self.log_dir / file_name
        
        with self.lock:
            file_path.touch()
            self.dialog_files[dialog_id] = file_path
            
            # Write header
            with open(file_path, 'w') as f:
                f.write(f"# Dialog started at {timestamp.isoformat()}\n")
                f.write(f"# Dialog ID: {dialog_id}\n\n")
        
        return dialog_id, str(file_path)
    
    def write_dialog_log(self, dialog_id: str, speaker: str, text: str,
                        timestamp_ms: int = None) -> bool:
        """Write to dialog log.
        
        Args:
            dialog_id: Dialog identifier
            speaker: Speaker (USER or ASSISTANT)
            text: Spoken text
            timestamp_ms: Timestamp in milliseconds
            
        Returns:
            True if successful
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000).strftime('%H:%M:%S')
        
        with self.lock:
            # Find or create dialog file
            if dialog_id not in self.dialog_files:
                _, file_path = self.new_dialog(timestamp_ms)
                self.dialog_files[dialog_id] = Path(file_path)
            
            file_path = self.dialog_files[dialog_id]
            
            try:
                with open(file_path, 'a') as f:
                    f.write(f"[{timestamp}] {speaker}: {text}\n")
                return True
            except Exception as e:
                print(f"Error writing dialog log: {e}")
                return False


class LoggerService(BaseService):
    """Logger service implementation."""
    
    def __init__(self):
        """Initialize logger service."""
        super().__init__('logger')
        self.log_manager = None
        self.servicer = None
    
    def setup(self):
        """Setup logger service."""
        try:
            # Initialize log manager
            self.log_manager = LogManager(self.config)
            
            # Add logger servicer to server
            self.servicer = LoggerServicer(self.log_manager)
            services_pb2_grpc.add_LoggerServiceServicer_to_server(
                self.servicer, self.server
            )
            
            # Write initial log entry
            self.log_manager.write_app_log(
                service='logger',
                event='startup',
                message='Logger service initialized',
                level='INFO'
            )
            
            self.logger.info("Logger service setup complete")
            
        except Exception as e:
            self.logger.error(f"Failed to setup logger service: {e}")
            raise
    
    def cleanup(self):
        """Cleanup logger service."""
        if self.log_manager:
            self.log_manager.write_app_log(
                service='logger',
                event='shutdown',
                message='Logger service shutting down',
                level='INFO'
            )


if __name__ == "__main__":
    service = LoggerService()
    service.start()
