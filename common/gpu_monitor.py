"""GPU monitoring utilities for VRAM management."""
import nvidia_ml_py3 as nvml
from typing import Optional, Tuple


class GPUMonitor:
    """Monitor GPU memory usage and enforce guardrails."""
    
    def __init__(self):
        """Initialize GPU monitor."""
        try:
            nvml.nvmlInit()
            self.initialized = True
            self.device_count = nvml.nvmlDeviceGetCount()
            if self.device_count > 0:
                self.device = nvml.nvmlDeviceGetHandleByIndex(0)
            else:
                self.device = None
                self.initialized = False
        except Exception as e:
            self.initialized = False
            self.device = None
    
    def get_vram_usage(self) -> Tuple[int, int, int]:
        """Get current VRAM usage.
        
        Returns:
            Tuple of (used_mb, free_mb, total_mb)
        """
        if not self.initialized or not self.device:
            return (0, 0, 0)
        
        try:
            mem_info = nvml.nvmlDeviceGetMemoryInfo(self.device)
            used_mb = mem_info.used // (1024 * 1024)
            free_mb = mem_info.free // (1024 * 1024)
            total_mb = mem_info.total // (1024 * 1024)
            return (used_mb, free_mb, total_mb)
        except Exception as e:
            return (0, 0, 0)
    
    def check_vram_available(self, required_mb: int) -> bool:
        """Check if required VRAM is available.
        
        Args:
            required_mb: Required VRAM in megabytes
            
        Returns:
            True if enough VRAM is available
        """
        _, free_mb, _ = self.get_vram_usage()
        return free_mb >= required_mb
    
    def get_gpu_utilization(self) -> int:
        """Get GPU utilization percentage.
        
        Returns:
            GPU utilization percentage (0-100)
        """
        if not self.initialized or not self.device:
            return 0
        
        try:
            util = nvml.nvmlDeviceGetUtilizationRates(self.device)
            return util.gpu
        except Exception:
            return 0
    
    def get_gpu_temperature(self) -> int:
        """Get GPU temperature in Celsius.
        
        Returns:
            GPU temperature in Celsius
        """
        if not self.initialized or not self.device:
            return 0
        
        try:
            temp = nvml.nvmlDeviceGetTemperature(self.device, nvml.NVML_TEMPERATURE_GPU)
            return temp
        except Exception:
            return 0
    
    def get_gpu_name(self) -> str:
        """Get GPU device name.
        
        Returns:
            GPU device name
        """
        if not self.initialized or not self.device:
            return "Unknown"
        
        try:
            name = nvml.nvmlDeviceGetName(self.device)
            if isinstance(name, bytes):
                name = name.decode('utf-8')
            return name
        except Exception:
            return "Unknown"
    
    def shutdown(self):
        """Shutdown NVML."""
        if self.initialized:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
            self.initialized = False
    
    def __del__(self):
        """Cleanup on deletion."""
        self.shutdown()


def check_vram_guardrail(min_vram_mb: int = 8000) -> Tuple[bool, str]:
    """Check if system meets VRAM requirements.
    
    Args:
        min_vram_mb: Minimum required VRAM in MB
        
    Returns:
        Tuple of (meets_requirement, message)
    """
    monitor = GPUMonitor()
    
    if not monitor.initialized:
        return (False, "GPU monitoring not available")
    
    used_mb, free_mb, total_mb = monitor.get_vram_usage()
    
    if total_mb < min_vram_mb:
        return (False, f"Total VRAM {total_mb}MB < required {min_vram_mb}MB")
    
    if free_mb < min_vram_mb // 2:  # Require at least half to be free
        return (False, f"Free VRAM {free_mb}MB insufficient (used: {used_mb}MB)")
    
    gpu_name = monitor.get_gpu_name()
    monitor.shutdown()
    
    return (True, f"GPU {gpu_name} has {total_mb}MB VRAM ({free_mb}MB free)")
