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
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2


class ConsoleFormatter:
    """Formats log entries for console output according to spec."""
    
    # Valid service names
    VALID_SERVICES = {'MAIN', 'LOADER', 'KWD', 'STT', 'LLM', 'TTS', 'LOGGER'}
    
    # Valid log levels
    VALID_LEVELS = {'INFO', 'ERROR', 'WARNING', 'DEBUG'}
    
    # Key events that should be echoed to console
    KEY_EVENTS = {
        # Service lifecycle
        'service_start', 'service_stop', 'service_error',
        # Phase milestones
        'phase1_start', 'phase1_ready', 'phase2_start', 'phase2_ready',
        'phase3_start', 'phase3_ready', 'warmup_done',
        # KWD highlights
        'kwd_started', 'wake_detected', 'kwd_stopped',
        # STT highlights
        'stt_started', 'stt_final_text', 'stt_stopped',
        # LLM highlights
        'llm_stream_start', 'llm_stream_end',
        # TTS highlights
        'tts_stream_start', 'tts_finished', 'tts_error',
        # Memory guardrail violations
        'vram_warning', 'vram_error', 'vram_guardrail'
    }
    
    def __init__(self, config):
        """Initialize console formatter.
        
        Args:
            config: ConfigLoader instance
        """
        self.console_echo = config.get('logger', 'console_echo', 'key_events')
        self.show_time = config.get_bool('logger', 'console_show_time', False)
        self.use_colors = config.get_bool('logger', 'console_colors', False)
    
    def should_echo(self, event: str) -> bool:
        """Determine if event should be echoed to console.
        
        Args:
            event: Event name
            
        Returns:
            True if event should be echoed
        """
        if self.console_echo == 'none':
            return False
        elif self.console_echo == 'all':
            return True
        else:  # key_events
            return event in self.KEY_EVENTS
    
    def format_service(self, service: str) -> str:
        """Format service name.
        
        Args:
            service: Service name
            
        Returns:
            Formatted service name
        """
        # Convert to uppercase
        service_upper = service.upper()
        # Allow TEST for testing, otherwise validate
        if service_upper == 'TEST':
            return service_upper
        elif service_upper not in self.VALID_SERVICES:
            # Default to LOGGER for unknown services
            return 'LOGGER'
        return service_upper
    
    def format_level(self, level: str) -> str:
        """Format log level.
        
        Args:
            level: Log level
            
        Returns:
            Formatted log level
        """
        # Convert to uppercase and validate
        level_upper = level.upper()
        if level_upper not in self.VALID_LEVELS:
            level_upper = 'INFO'
        return level_upper
    
    def format_message(self, event: str, message: str, details: str = None) -> str:
        """Format message based on event type.
        
        Args:
            event: Event name
            message: Original message
            details: Optional details
            
        Returns:
            Formatted message
        """
        # Use details if provided, otherwise use message
        if details:
            content = details
        else:
            content = message
        
        # Special formatting for known events
        if event == 'service_start':
            # Try to extract target service from message/details
            import re
            # Check for "Starting X service" pattern
            match = re.search(r'Starting\s+(\w+)\s+service', content, re.IGNORECASE)
            if match:
                return f"Starting {match.group(1)} service"
            # Check for "X service loaded (PID=..." pattern
            match = re.search(r'(\w+)\s+service\s+loaded\s+\(PID', content, re.IGNORECASE)
            if match:
                # Extract full details including PID and port
                return content
            # Check for just service name
            if content.lower() in ['kwd', 'stt', 'llm', 'tts', 'loader', 'logger']:
                return f"Starting {content} service"
            return content
        elif event == 'service_stop':
            # Try to extract target service
            import re
            match = re.search(r'(\w+)\s+service', content, re.IGNORECASE)
            if match:
                return f"Stopping {match.group(1)} service"
            return content
        elif event == 'service_error':
            return f"Service error: {content}"
        elif event.startswith('phase') and ('_start' in event or '_ready' in event):
            # Extract phase number
            import re
            match = re.search(r'phase(\d+)', event)
            if match:
                phase_num = match.group(1)
                if '_start' in event:
                    return f"Phase {phase_num} start"
                else:
                    return f"Phase {phase_num} ready"
            return content
        elif event == 'wake_detected':
            # Try to extract confidence
            import re
            match = re.search(r'confidence[=:]?\s*([0-9.]+)', content, re.IGNORECASE)
            if match:
                return f"Wake word detected (confidence {match.group(1)})"
            return "Wake word detected"
        elif event == 'stt_final_text':
            # Format as user text
            return f"User: {content}"
        elif event == 'llm_stream_end':
            # Format as assistant text
            return f"Assistant: {content}"
        elif event == 'tts_stream_start':
            return "Speaking…"
        elif event == 'tts_finished':
            return "Playback finished"
        elif event.startswith('vram_'):
            # Try to extract VRAM details
            import re
            match = re.search(r'used[=:]?\s*(\d+).*free[=:]?\s*(\d+).*guardrail[=:]?\s*(\d+)', content, re.IGNORECASE)
            if match:
                return f"VRAM low: used={match.group(1)} free={match.group(2)} guardrail={match.group(3)}"
            return content
        else:
            # Default: return content as-is
            return content
    
    def format_console_line(self, service: str, level: str, event: str, message: str, 
                           timestamp_ms: int = None, details: str = None) -> str:
        """Format a complete console line.
        
        Args:
            service: Service name
            level: Log level
            event: Event type
            message: Log message
            timestamp_ms: Timestamp in milliseconds
            details: Optional details
            
        Returns:
            Formatted console line
        """
        # Format components
        service_fmt = self.format_service(service)
        level_fmt = self.format_level(level)
        message_fmt = self.format_message(event, message, details)
        
        # Build console line
        line_parts = []
        
        # Optional timestamp prefix
        if self.show_time and timestamp_ms:
            dt = datetime.fromtimestamp(timestamp_ms / 1000)
            time_str = dt.strftime('%d-%m-%y %H:%M:%S')
            line_parts.append(f"{time_str}  ")
        
        # Main format: {SERVICE:<10}{LEVEL:<6}= {MESSAGE}
        line_parts.append(f"{service_fmt:<10}{level_fmt:<6}= {message_fmt}")
        
        line = ''.join(line_parts)
        
        # Optional ANSI colors
        if self.use_colors:
            # ANSI color codes
            RESET = '\033[0m'
            YELLOW = '\033[33m'
            RED_BOLD = '\033[1;31m'
            DIM = '\033[2m'
            CYAN = '\033[36m'
            
            # Apply colors based on level
            if level_fmt == 'WARNING':
                line = f"{YELLOW}{line}{RESET}"
            elif level_fmt == 'ERROR':
                line = f"{RED_BOLD}{line}{RESET}"
            elif level_fmt == 'DEBUG':
                line = f"{DIM}{line}{RESET}"
        
        return line


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
        # Extract details from message if it looks like it contains details
        details = None
        if request.message and '=' in request.message and len(request.message) < 100:
            # Likely a details string like "confidence=0.95"
            details = request.message
        
        success = self.log_manager.write_app_log(
            service=request.service,
            event=request.event,
            message=request.message,
            level=request.level,
            timestamp_ms=request.timestamp_ms,
            details=details
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
        self.memory_log_file = self.log_dir / 'memory.log'
        
        self.rotation_size_mb = config.get_int('logger', 'rotation_size_mb', 100)
        self.rotation_count = config.get_int('logger', 'rotation_count', 5)
        
        # Initialize console formatter
        self.console_formatter = ConsoleFormatter(config)
        
        self.lock = threading.Lock()
        self.dialog_files = {}
        
        # Reset app log on start
        self._reset_app_log()
        # Reset memory log on start
        self._reset_memory_log()
    
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
    
    def _reset_memory_log(self):
        """Reset memory log file."""
        with self.lock:
            # Rotate existing log if it exists
            if self.memory_log_file.exists():
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_file = self.log_dir / f"memory_{timestamp}.log"
                self.memory_log_file.rename(backup_file)
                
                # Clean up old backups
                self._cleanup_old_logs('memory_*.log')
            
            # Create new memory log with header
            with open(self.memory_log_file, 'w') as f:
                f.write("# Memory usage log\n")
                f.write("# Format: timestamp,service,vram_used_mb,vram_free_mb,details\n")
                f.write("timestamp,service,vram_used_mb,vram_free_mb,details\n")
    
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
                      level: str = "INFO", timestamp_ms: int = None, details: str = None) -> bool:
        """Write to application log and optionally console.
        
        Args:
            service: Service name
            event: Event type
            message: Log message
            level: Log level (INFO, WARN, ERROR, FATAL)
            timestamp_ms: Timestamp in milliseconds
            details: Optional details for better event formatting
            
        Returns:
            True if successful
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000).isoformat()
        
        # Extract details from message if not provided separately
        if not details:
            details = message
        
        # Output to console if appropriate
        if self.console_formatter.should_echo(event):
            console_line = self.console_formatter.format_console_line(
                service=service,
                level=level,
                event=event,
                message=message,
                timestamp_ms=timestamp_ms,
                details=details
            )
            print(console_line)
        
        # Special handling for memory events - also write to memory.log
        if event == "memory" or event.startswith('vram_'):
            self._write_memory_log(service, message, timestamp)
        
        # Special handling for dialog events from STT/LLM - also write to dialog.log
        if event == 'stt_final_text' and service.upper() == 'STT':
            # Try to extract dialog_id from message
            import re
            dialog_match = re.search(r'dialog[_\s]*(\d+_\d+_\d+)', message)
            if dialog_match:
                self.write_dialog_log(dialog_match.group(1), "USER", details)
            # Also mirror to dialog log if we have the text directly
            elif details and details != message:
                # Try to get current dialog from somewhere
                pass
        elif event == 'llm_stream_end' and service.upper() == 'LLM':
            # Try to extract dialog_id from message
            import re
            dialog_match = re.search(r'dialog[_\s]*(\d+_\d+_\d+)', message)
            if dialog_match:
                self.write_dialog_log(dialog_match.group(1), "ASSISTANT", details)
        
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
    
    def _write_memory_log(self, service: str, message: str, timestamp: str):
        """Write to memory log.
        
        Args:
            service: Service name
            message: Memory message containing VRAM info
            timestamp: ISO format timestamp
        """
        import re
        # Parse VRAM info from message
        # Expected format: "VRAM: 1234MB used, 5678MB free - details"
        match = re.search(r'VRAM:\s*(\d+)MB\s+used,\s*(\d+)MB\s+free(?:\s+-\s+(.+))?', message)
        if match:
            vram_used = match.group(1)
            vram_free = match.group(2)
            details = match.group(3) or ""
            
            try:
                with open(self.memory_log_file, 'a') as f:
                    f.write(f"{timestamp},{service},{vram_used},{vram_free},{details}\n")
            except Exception as e:
                print(f"Error writing memory log: {e}")


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
