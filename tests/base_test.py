"""Base class for test scripts with proper logging configuration."""
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class BaseTest:
    """Base class for all test scripts with standardized logging."""
    
    def __init__(self, test_name: str, log_to_console: bool = True):
        """Initialize test with proper logging configuration.
        
        Args:
            test_name: Name of the test (used for log file naming)
            log_to_console: Whether to also log to console
        """
        self.test_name = test_name
        self.start_time = datetime.now()
        
        # Setup logging
        self.setup_test_logging(log_to_console)
        
        # Log test start
        self.logger.info("="*60)
        self.logger.info(f"Test Started: {test_name}")
        self.logger.info(f"Timestamp: {self.start_time.isoformat()}")
        self.logger.info("="*60)
        
    def setup_test_logging(self, log_to_console: bool):
        """Setup logging configuration for tests.
        
        Args:
            log_to_console: Whether to also log to console
        """
        # Create test logs directory
        test_log_dir = Path('logs/test_logs')
        test_log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create logger for this test
        self.logger = logging.getLogger(self.test_name)
        self.logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers
        self.logger.handlers = []
        
        # Create log file with timestamp
        timestamp = self.start_time.strftime('%Y%m%d_%H%M%S')
        log_file = test_log_dir / f'{self.test_name}_{timestamp}.log'
        
        # File handler - captures everything
        file_handler = logging.FileHandler(log_file, mode='w')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - [%(name)s] - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # Console handler (optional)
        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)  # Less verbose on console
            console_formatter = logging.Formatter('[%(name)s] %(message)s')
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
            
        # Store log file path for reference
        self.log_file = log_file
        
    def log_result(self, success: bool, message: str = None):
        """Log test result.
        
        Args:
            success: Whether test passed
            message: Optional result message
        """
        self.logger.info("="*60)
        if success:
            self.logger.info("TEST RESULT: PASSED ✓")
        else:
            self.logger.error("TEST RESULT: FAILED ✗")
            
        if message:
            self.logger.info(f"Message: {message}")
            
        # Log duration
        duration = datetime.now() - self.start_time
        self.logger.info(f"Duration: {duration}")
        self.logger.info("="*60)
        
    def log_step(self, step: str, details: str = None):
        """Log a test step.
        
        Args:
            step: Step description
            details: Optional additional details
        """
        self.logger.info(f"\n>>> {step}")
        if details:
            self.logger.info(f"    {details}")
            
    def log_error(self, error: str, exception: Exception = None):
        """Log an error.
        
        Args:
            error: Error description
            exception: Optional exception object
        """
        self.logger.error(f"ERROR: {error}")
        if exception:
            self.logger.error(f"Exception: {type(exception).__name__}: {str(exception)}")
            
    def cleanup(self):
        """Cleanup test resources and log final status."""
        self.logger.info(f"\nTest log saved to: {self.log_file}")
        self.logger.info("Test cleanup complete")
        
    def __enter__(self):
        """Context manager entry."""
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with automatic cleanup."""
        if exc_type:
            self.log_error(f"Test terminated with exception", exc_val)
            self.log_result(False, f"Exception: {exc_type.__name__}")
        self.cleanup()
        return False  # Don't suppress exceptions


class VRAMTestLogger(BaseTest):
    """Extended test logger with VRAM monitoring capabilities."""
    
    def __init__(self, test_name: str, log_to_console: bool = True):
        """Initialize test with VRAM monitoring.
        
        Args:
            test_name: Name of the test
            log_to_console: Whether to also log to console
        """
        super().__init__(test_name, log_to_console)
        
        # Import VRAM logger
        from common.vram_logger import get_vram_logger
        self.vram_logger = get_vram_logger()
        
        # Log initial VRAM state
        self.log_vram("Initial state")
        
    def log_vram(self, event: str):
        """Log current VRAM status.
        
        Args:
            event: Event description
        """
        vram_info = self.vram_logger.get_vram_info()
        if vram_info:
            self.logger.info(
                f"VRAM [{event}]: "
                f"{vram_info['used_mb']:.0f}/{vram_info['total_mb']:.0f} MB "
                f"({vram_info['percent']:.1f}%) | "
                f"Free: {vram_info['free_mb']:.0f} MB"
            )
            # Also log to memory.log
            self.vram_logger.log_vram_status(event=event, service=self.test_name)
        else:
            self.logger.warning(f"VRAM info not available for: {event}")
            
    def log_vram_delta(self, before_event: str, after_event: str):
        """Log VRAM change between two points.
        
        Args:
            before_event: Description of before state
            after_event: Description of after state
        """
        before = self.vram_logger.get_vram_info()
        self.log_vram(before_event)
        
        yield  # Allow test to perform operation
        
        after = self.vram_logger.get_vram_info()
        self.log_vram(after_event)
        
        # Log delta
        if before and after:
            delta = after['used_mb'] - before['used_mb']
            self.logger.info(f"VRAM Delta: {delta:+.0f} MB")
            self.vram_logger.log_vram_delta(before, after, f"{before_event} -> {after_event}")
