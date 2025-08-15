#!/usr/bin/env python3
"""Main bootstrap process for the voice assistant system."""
import sys
import os
import time
import signal
import subprocess
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[MAIN] %(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class Bootstrap:
    """Bootstrap process that starts the loader service."""
    
    def __init__(self):
        self.loader_process = None
        self.running = False
        
        # Signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def check_environment(self):
        """Check environment prerequisites."""
        logger.info("Checking environment...")
        
        # Check Python version
        if sys.version_info < (3, 11):
            logger.error(f"Python 3.11+ required, found {sys.version}")
            return False
            
        # Check virtual environment
        venv_path = Path('.venv')
        if not venv_path.exists():
            logger.error("Virtual environment not found. Run: uv venv")
            return False
            
        # Check config file
        config_path = Path('config/config.ini')
        if not config_path.exists():
            logger.error("Config file not found: config/config.ini")
            return False
            
        # Check proto files
        proto_path = Path('proto/services_pb2.py')
        if not proto_path.exists():
            logger.warning("Proto files not generated. Run: python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/services.proto")
            # Try to generate them
            try:
                result = subprocess.run([
                    sys.executable, '-m', 'grpc_tools.protoc',
                    '-I.', '--python_out=.', '--grpc_python_out=.',
                    'proto/services.proto'
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    logger.info("Proto files generated successfully")
                else:
                    logger.error(f"Failed to generate proto files: {result.stderr}")
                    return False
            except Exception as e:
                logger.error(f"Failed to generate proto files: {e}")
                return False
                
        # Check GPU
        try:
            import torch
            if torch.cuda.is_available():
                logger.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
            else:
                logger.warning("CUDA not available - will use CPU (slower)")
        except ImportError:
            logger.warning("PyTorch not installed - GPU check skipped")
            
        # Check Ollama
        try:
            result = subprocess.run(['ollama', '--version'], capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("Ollama installed")
            else:
                logger.warning("Ollama not found - LLM service will fail")
        except FileNotFoundError:
            logger.warning("Ollama not installed - LLM service will fail")
            
        # Create required directories
        for dir_name in ['logs', 'models']:
            dir_path = Path(dir_name)
            if not dir_path.exists():
                dir_path.mkdir(parents=True)
                logger.info(f"Created directory: {dir_name}")
                
        logger.info("Environment check complete")
        return True
        
    def start_loader(self):
        """Start the loader service."""
        logger.info("Starting loader service...")
        
        # Use virtual environment Python
        venv_python = Path('.venv/bin/python').absolute()
        loader_script = Path('services/loader/loader_service.py')
        
        if not loader_script.exists():
            logger.error(f"Loader script not found: {loader_script}")
            return False
            
        try:
            # Start loader process
            log_file = open('loader_service.log', 'w')
            self.loader_process = subprocess.Popen(
                [str(venv_python), str(loader_script)],
                stdout=log_file,
                stderr=subprocess.STDOUT
            )
            
            logger.info(f"Loader started (PID: {self.loader_process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start loader: {e}")
            return False
            
    def monitor_loader(self):
        """Monitor loader process and restart if needed."""
        restart_count = 0
        max_restarts = 3
        
        while self.running:
            if self.loader_process:
                # Check if loader is still running
                poll_result = self.loader_process.poll()
                
                if poll_result is not None:
                    # Loader crashed
                    logger.error(f"Loader exited with code {poll_result}")
                    
                    if restart_count < max_restarts:
                        restart_count += 1
                        logger.info(f"Restarting loader (attempt {restart_count}/{max_restarts})...")
                        
                        # Wait before restart
                        time.sleep(2 * restart_count)
                        
                        if self.start_loader():
                            logger.info("Loader restarted successfully")
                        else:
                            logger.error("Failed to restart loader")
                            break
                    else:
                        logger.error("Max restart attempts reached - giving up")
                        self.running = False
                        break
                        
            time.sleep(5)  # Check every 5 seconds
            
    def run(self):
        """Main run loop."""
        logger.info("="*50)
        logger.info("Voice Assistant System Bootstrap")
        logger.info("="*50)
        
        # Check environment
        if not self.check_environment():
            logger.error("Environment check failed - exiting")
            return 1
            
        # Start loader
        if not self.start_loader():
            logger.error("Failed to start loader - exiting")
            return 1
            
        # Monitor loader
        self.running = True
        logger.info("System running - press Ctrl+C to stop")
        
        try:
            self.monitor_loader()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            
        # Cleanup
        self.stop()
        return 0
        
    def stop(self):
        """Stop the system."""
        logger.info("Stopping system...")
        self.running = False
        
        if self.loader_process:
            try:
                # Send SIGTERM to loader
                self.loader_process.terminate()
                
                # Wait for graceful shutdown
                try:
                    self.loader_process.wait(timeout=10)
                    logger.info("Loader stopped gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if needed
                    self.loader_process.kill()
                    logger.warning("Loader force killed")
                    
            except Exception as e:
                logger.error(f"Error stopping loader: {e}")
                
        logger.info("System stopped")
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        if not self.running:
            return  # Avoid multiple calls
        logger.info(f"Received signal {signum} - stopping immediately")
        self.running = False
        self.stop()  # Stop immediately
        sys.exit(0)


if __name__ == "__main__":
    bootstrap = Bootstrap()
    sys.exit(bootstrap.run())
