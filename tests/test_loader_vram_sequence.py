#!/usr/bin/env python3
"""Test loader with VRAM monitoring - custom service startup order."""
import sys
import time
import subprocess
import signal
from pathlib import Path
from typing import Dict, Optional, List
import psutil

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.health_client import HealthClient
from common.vram_logger import get_vram_logger
from tests.base_test import VRAMTestLogger
import grpc
from proto import services_pb2, services_pb2_grpc


class VRAMMonitoredLoader:
    """Loader that monitors VRAM usage at each service startup."""
    
    def __init__(self):
        # Initialize test logger
        self.test_logger = VRAMTestLogger('loader_vram_test', log_to_console=True)
        self.logger = self.test_logger.logger
        self.vram_logger = get_vram_logger()
        
        self.processes = {}
        self.health_clients = {}
        self.start_time = time.time()
        
        # Service definitions with custom order
        self.service_order = [
            ('kwd', 5003, 'services/kwd_service.py'),
            ('stt', 5004, 'services/stt_service.py'),
            ('llm', 5005, 'services/llm_service.py'),
            ('tts', 5006, 'services/tts_service.py'),
        ]
        
        # Track VRAM usage
        self.vram_history = []
        
    def get_vram_info(self) -> Dict:
        """Get current VRAM information."""
        return self.vram_logger.get_vram_info()
        
    def print_vram_status(self, stage: str):
        """Print formatted VRAM status."""
        vram = self.get_vram_info()
        if vram:
            # Log to console and test log
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"VRAM Status - {stage}")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"  Used:  {vram['used_mb']:>6.0f} MB ({vram['percent']:>5.1f}%)")
            self.logger.info(f"  Free:  {vram['free_mb']:>6.0f} MB")
            self.logger.info(f"  Total: {vram['total_mb']:>6.0f} MB")
            self.logger.info(f"{'='*60}")
            
            # Log to memory.log
            self.vram_logger.log_vram_status(event=stage, service='loader_test')
            
            # Track history
            self.vram_history.append({
                'stage': stage,
                'used_mb': vram['used_mb'],
                'timestamp': time.time() - self.start_time
            })
        else:
            self.logger.warning(f"Could not get VRAM info at {stage}")
            
    def start_service(self, name: str, port: int, script: str) -> bool:
        """Start a single service."""
        try:
            self.logger.info(f"Starting {name} service on port {port}...")
            
            # Check if already running
            if name in self.processes and self.processes[name].poll() is None:
                self.logger.info(f"{name} service already running")
                return True
                
            # Start the service
            script_path = Path(script)
            if not script_path.exists():
                self.logger.error(f"Script not found: {script_path}")
                return False
                
            # Use virtual environment Python
            venv_python = Path('.venv/bin/python').absolute()
            
            # Start process
            process = subprocess.Popen(
                [str(venv_python), str(script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            self.processes[name] = process
            self.logger.info(f"{name} process started (PID: {process.pid})")
            
            # Wait for service to be healthy
            health_client = HealthClient(port=port)
            self.health_clients[name] = health_client
            
            self.logger.info(f"Waiting for {name} service to be healthy...")
            deadline = time.time() + 30  # 30 second timeout
            
            while time.time() < deadline:
                status = health_client.check()
                if status == "SERVING":
                    self.logger.info(f"{name} service is SERVING ✓")
                    return True
                time.sleep(0.5)
                
            self.logger.error(f"{name} service health check timeout ✗")
            return False
            
        except Exception as e:
            self.logger.error(f"Error starting {name}: {e}")
            return False
            
    def kill_orphaned_services(self):
        """Kill any orphaned service processes."""
        self.logger.info("Killing any orphaned services...")
        
        service_patterns = [
            'kwd_service.py',
            'stt_service.py', 
            'llm_service.py',
            'tts_service.py',
            'logger_service.py',
            'loader_service.py'
        ]
        
        killed_count = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline:
                    cmdline_str = ' '.join(cmdline)
                    for pattern in service_patterns:
                        if pattern in cmdline_str:
                            proc.kill()
                            killed_count += 1
                            self.logger.info(f"  Killed {pattern} (PID: {proc.pid})")
                            break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        # Also kill Ollama
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        if killed_count > 0:
            self.logger.info(f"Killed {killed_count} orphaned service(s)")
            time.sleep(2)  # Wait for GPU memory to be released
            
    def start_ollama(self) -> bool:
        """Start Ollama server."""
        self.logger.info("Starting Ollama server...")
        
        # Check if already running
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if result.returncode == 0:
            self.logger.info("Ollama already running")
            return True
            
        # Start Ollama
        ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for it to be ready
        for i in range(10):
            time.sleep(1)
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if result.returncode == 0:
                self.logger.info("Ollama server started successfully ✓")
                return True
                
        self.logger.error("Failed to start Ollama ✗")
        return False
        
    def preload_llm_model(self) -> bool:
        """Pre-load LLM model into VRAM."""
        self.logger.info("Pre-loading LLM model (llama3.1:8b-instruct-q4_K_M)...")
        self.logger.info("This may take a moment...")
        
        try:
            result = subprocess.run(
                ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "hi"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("LLM model loaded successfully ✓")
                return True
            else:
                self.logger.error(f"Failed to load LLM model: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("LLM model loading timed out")
            return False
        except Exception as e:
            self.logger.error(f"Error loading LLM model: {e}")
            return False
            
    def run(self):
        """Run the loader test with VRAM monitoring."""
        print("\n" + "="*60)
        print("VRAM Monitored Loader Test")
        print("Service Order: KWD → STT → LLM → TTS")
        print("="*60)
        
        try:
            # Initial cleanup
            self.kill_orphaned_services()
            
            # Initial VRAM status
            self.print_vram_status("Initial (Clean)")
            
            # Start Logger first (always needed)
            print("\n[LOGGER] Starting logger service (required for all services)...")
            if not self.start_service('logger', 5001, 'services/logger_service.py'):
                print("[LOGGER] ✗ Failed to start - aborting")
                return False
                
            self.print_vram_status("After Logger")
            
            # Start Ollama for LLM
            if not self.start_ollama():
                print("[OLLAMA] Warning: Ollama not available, LLM may fail")
                
            # Pre-load LLM model
            if not self.preload_llm_model():
                print("[LLM] Warning: Could not pre-load model")
                
            self.print_vram_status("After LLM Model Load")
            
            # Start services in specified order
            for name, port, script in self.service_order:
                print(f"\n{'='*60}")
                print(f"Starting {name.upper()} Service")
                print(f"{'='*60}")
                
                # VRAM before
                vram_before = self.get_vram_info()
                
                # Start service
                success = self.start_service(name, port, script)
                
                if not success:
                    print(f"\n[ERROR] Failed to start {name} service")
                    self.print_summary()
                    return False
                    
                # Wait a bit for memory to stabilize
                time.sleep(2)
                
                # VRAM after
                vram_after = self.get_vram_info()
                
                # Calculate delta
                if vram_before and vram_after:
                    delta = vram_after['used_mb'] - vram_before['used_mb']
                    print(f"\n[{name.upper()}] VRAM Delta: {delta:+.0f} MB")
                    
                self.print_vram_status(f"After {name.upper()}")
                
            # Final summary
            self.print_summary()
            
            print("\n[SUCCESS] All services started successfully!")
            print("\nServices are running. Press Ctrl+C to stop...")
            
            # Keep running until interrupted
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\n[INTERRUPT] Stopping services...")
        finally:
            self.cleanup()
            
    def print_summary(self):
        """Print VRAM usage summary."""
        print("\n" + "="*60)
        print("VRAM Usage Summary")
        print("="*60)
        
        if not self.vram_history:
            print("No VRAM history recorded")
            return
            
        print(f"{'Stage':<25} {'VRAM (MB)':>10} {'Delta (MB)':>12} {'Time (s)':>10}")
        print("-"*60)
        
        prev_vram = 0
        for entry in self.vram_history:
            delta = entry['used_mb'] - prev_vram if prev_vram > 0 else 0
            print(f"{entry['stage']:<25} {entry['used_mb']:>10.0f} {delta:>+12.0f} {entry['timestamp']:>10.1f}")
            prev_vram = entry['used_mb']
            
        print("-"*60)
        
        # Total usage
        if len(self.vram_history) >= 2:
            total_delta = self.vram_history[-1]['used_mb'] - self.vram_history[0]['used_mb']
            print(f"{'Total VRAM Increase:':<25} {total_delta:>10.0f} MB")
            
        # Final status
        final_vram = self.get_vram_info()
        if final_vram:
            print(f"{'Final VRAM Usage:':<25} {final_vram['used_mb']:>10.0f} MB ({final_vram['percent']:.1f}%)")
            print(f"{'Final VRAM Free:':<25} {final_vram['free_mb']:>10.0f} MB")
            
    def cleanup(self):
        """Clean up all processes."""
        print("\n[CLEANUP] Stopping all services...")
        
        # Stop all processes
        for name, process in self.processes.items():
            if process and process.poll() is None:
                process.terminate()
                print(f"  Stopped {name} (PID: {process.pid})")
                
        # Wait for processes to terminate
        time.sleep(1)
        
        # Force kill if needed
        for name, process in self.processes.items():
            if process and process.poll() is None:
                process.kill()
                print(f"  Force killed {name}")
                
        # Close health clients
        for client in self.health_clients.values():
            client.close()
            
        # Kill Ollama
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        print("[CLEANUP] Complete")


if __name__ == "__main__":
    # Make sure we're in virtual environment
    if not Path('.venv').exists():
        print("ERROR: Virtual environment not found. Run: uv venv")
        sys.exit(1)
        
    loader = VRAMMonitoredLoader()
    loader.run()
