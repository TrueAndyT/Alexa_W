"""VRAM monitoring and logging utility."""
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
import json
import threading

try:
    import pynvml as nvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False


class VRAMLogger:
    """Monitors and logs VRAM usage to memory.log."""
    
    def __init__(self, log_interval: float = 5.0):
        """Initialize VRAM logger.
        
        Args:
            log_interval: Interval in seconds between VRAM logging
        """
        self.log_interval = log_interval
        self.monitoring = False
        self.monitor_thread = None
        
        # Setup memory logger
        self.setup_memory_logger()
        
        # Initialize NVML if available
        self.nvml_initialized = False
        if NVML_AVAILABLE:
            try:
                nvml.nvmlInit()
                self.nvml_initialized = True
                self.device_count = nvml.nvmlDeviceGetCount()
                self.handle = nvml.nvmlDeviceGetHandleByIndex(0) if self.device_count > 0 else None
                
                # Log GPU info
                if self.handle:
                    name = nvml.nvmlDeviceGetName(self.handle)
                    self.memory_logger.info(f"GPU detected: {name}")
            except Exception as e:
                self.memory_logger.error(f"Failed to initialize NVML: {e}")
                
    def setup_memory_logger(self):
        """Setup dedicated logger for memory monitoring."""
        # Create logs directory if it doesn't exist
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        
        # Create memory logger
        self.memory_logger = logging.getLogger('vram_monitor')
        self.memory_logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers
        self.memory_logger.handlers = []
        
        # File handler for memory.log
        memory_log_file = log_dir / 'memory.log'
        file_handler = logging.FileHandler(memory_log_file, mode='a')
        file_handler.setLevel(logging.DEBUG)
        
        # Custom formatter for memory logs
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        self.memory_logger.addHandler(file_handler)
        
        # Log header
        self.memory_logger.info("="*60)
        self.memory_logger.info("VRAM Monitor Started")
        self.memory_logger.info("="*60)
        
    def get_vram_info(self) -> Optional[Dict]:
        """Get current VRAM information.
        
        Returns:
            Dictionary with VRAM info or None if unavailable
        """
        if not self.nvml_initialized or not self.handle:
            return None
            
        try:
            mem_info = nvml.nvmlDeviceGetMemoryInfo(self.handle)
            total_mb = mem_info.total / 1024 / 1024
            used_mb = mem_info.used / 1024 / 1024
            free_mb = mem_info.free / 1024 / 1024
            percent = (mem_info.used / mem_info.total) * 100
            
            # Get running processes
            processes = []
            try:
                process_info = nvml.nvmlDeviceGetComputeRunningProcesses(self.handle)
                for proc in process_info:
                    processes.append({
                        'pid': proc.pid,
                        'memory_mb': proc.usedGpuMemory / 1024 / 1024 if proc.usedGpuMemory else 0
                    })
            except:
                pass  # Process info might not be available
            
            return {
                'timestamp': datetime.now().isoformat(),
                'total_mb': round(total_mb, 2),
                'used_mb': round(used_mb, 2),
                'free_mb': round(free_mb, 2),
                'percent': round(percent, 2),
                'processes': processes
            }
        except Exception as e:
            self.memory_logger.error(f"Error getting VRAM info: {e}")
            return None
            
    def log_vram_status(self, event: str = None, service: str = None):
        """Log current VRAM status with optional event description.
        
        Args:
            event: Optional event description (e.g., "Service started")
            service: Optional service name
        """
        vram_info = self.get_vram_info()
        if not vram_info:
            return
            
        # Build log message
        msg_parts = []
        
        if event:
            msg_parts.append(f"Event: {event}")
        if service:
            msg_parts.append(f"Service: {service}")
            
        msg_parts.extend([
            f"VRAM: {vram_info['used_mb']:.0f}/{vram_info['total_mb']:.0f} MB",
            f"({vram_info['percent']:.1f}%)",
            f"Free: {vram_info['free_mb']:.0f} MB"
        ])
        
        # Add process count if available
        if vram_info['processes']:
            msg_parts.append(f"GPU Processes: {len(vram_info['processes'])}")
            
        message = " | ".join(msg_parts)
        self.memory_logger.info(message)
        
    def log_vram_delta(self, before: Dict, after: Dict, operation: str):
        """Log VRAM change for an operation.
        
        Args:
            before: VRAM info before operation
            after: VRAM info after operation
            operation: Description of operation
        """
        if not before or not after:
            return
            
        delta_mb = after['used_mb'] - before['used_mb']
        delta_percent = after['percent'] - before['percent']
        
        if abs(delta_mb) > 1:  # Only log significant changes
            self.memory_logger.info(
                f"VRAM Delta - {operation}: "
                f"{delta_mb:+.0f} MB ({delta_percent:+.1f}%) | "
                f"Now: {after['used_mb']:.0f}/{after['total_mb']:.0f} MB "
                f"({after['percent']:.1f}%)"
            )
            
    def start_monitoring(self):
        """Start continuous VRAM monitoring in background thread."""
        if self.monitoring:
            return
            
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.memory_logger.info(f"Started continuous VRAM monitoring (interval: {self.log_interval}s)")
        
    def stop_monitoring(self):
        """Stop continuous VRAM monitoring."""
        if not self.monitoring:
            return
            
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=self.log_interval + 1)
        self.memory_logger.info("Stopped continuous VRAM monitoring")
        
    def _monitor_loop(self):
        """Background monitoring loop."""
        last_vram = None
        
        while self.monitoring:
            try:
                current_vram = self.get_vram_info()
                if current_vram:
                    # Only log if there's a significant change
                    if last_vram is None or abs(current_vram['used_mb'] - last_vram['used_mb']) > 10:
                        self.log_vram_status(event="Periodic check")
                        last_vram = current_vram
                        
                time.sleep(self.log_interval)
            except Exception as e:
                self.memory_logger.error(f"Error in monitor loop: {e}")
                time.sleep(self.log_interval)
                
    def log_summary(self, vram_history: List[Dict]):
        """Log a summary of VRAM usage history.
        
        Args:
            vram_history: List of VRAM snapshots with 'stage' and 'used_mb' keys
        """
        if not vram_history:
            return
            
        self.memory_logger.info("="*60)
        self.memory_logger.info("VRAM Usage Summary")
        self.memory_logger.info("="*60)
        
        # Log each stage
        prev_used = 0
        for i, entry in enumerate(vram_history):
            stage = entry.get('stage', f'Step {i}')
            used_mb = entry.get('used_mb', 0)
            delta = used_mb - prev_used if prev_used > 0 else 0
            
            self.memory_logger.info(
                f"{stage:<30} {used_mb:>8.0f} MB  {delta:>+8.0f} MB"
            )
            prev_used = used_mb
            
        # Log totals
        if len(vram_history) >= 2:
            total_increase = vram_history[-1]['used_mb'] - vram_history[0]['used_mb']
            self.memory_logger.info("-"*60)
            self.memory_logger.info(f"{'Total VRAM Increase:':<30} {total_increase:>8.0f} MB")
            
        self.memory_logger.info("="*60)
        
    def cleanup(self):
        """Cleanup NVML resources."""
        self.stop_monitoring()
        
        if self.nvml_initialized:
            try:
                nvml.nvmlShutdown()
                self.memory_logger.info("NVML shutdown complete")
            except:
                pass
                

# Global instance for easy access
_vram_logger_instance = None

def get_vram_logger() -> VRAMLogger:
    """Get or create global VRAM logger instance."""
    global _vram_logger_instance
    if _vram_logger_instance is None:
        _vram_logger_instance = VRAMLogger()
    return _vram_logger_instance
