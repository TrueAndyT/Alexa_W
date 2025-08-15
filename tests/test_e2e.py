#!/usr/bin/env python3
"""End-to-end test for the complete voice assistant system."""
import sys
import time
import subprocess
import grpc
from pathlib import Path
from typing import Optional
import signal
import threading

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


class E2ETestRunner:
    """End-to-end test runner for the voice assistant system."""
    
    def __init__(self):
        self.main_process: Optional[subprocess.Popen] = None
        self.test_passed = False
        
    def start_system(self) -> bool:
        """Start the complete system via main.py."""
        print("=" * 60)
        print("E2E TEST: Cold Boot")
        print("=" * 60)
        print("\n1. Starting system via main.py...")
        
        # Use virtual environment Python
        venv_python = Path('.venv/bin/python').absolute()
        main_script = Path('main.py').absolute()
        
        if not main_script.exists():
            print("✗ main.py not found")
            return False
            
        try:
            # Start main process
            self.main_process = subprocess.Popen(
                [str(venv_python), str(main_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            print(f"✓ Main process started (PID: {self.main_process.pid})")
            
            # Monitor startup output
            print("\n2. Monitoring startup phases...")
            start_time = time.time()
            timeout = 60  # 60 seconds for full startup
            
            phases_seen = {
                "PHASE_1": False,
                "PHASE_2": False,
                "PHASE_3": False,
                "IDLE": False
            }
            
            def monitor_output():
                for line in self.main_process.stdout:
                    print(f"   {line.rstrip()}")
                    
                    # Check for phase markers
                    if "PHASE 1" in line:
                        phases_seen["PHASE_1"] = True
                        print("   ✓ Phase 1 (TTS + LLM) started")
                    elif "PHASE 2" in line:
                        phases_seen["PHASE_2"] = True
                        print("   ✓ Phase 2 (STT) started")
                    elif "PHASE 3" in line:
                        phases_seen["PHASE_3"] = True
                        print("   ✓ Phase 3 (KWD) started")
                    elif "System ready in IDLE state" in line:
                        phases_seen["IDLE"] = True
                        print("   ✓ System reached IDLE state")
                        break
                    elif "ERROR" in line or "Failed" in line:
                        print(f"   ✗ Error detected: {line}")
                        
                    # Check timeout
                    if time.time() - start_time > timeout:
                        print("   ✗ Startup timeout")
                        break
                        
            # Start monitoring in thread
            monitor_thread = threading.Thread(target=monitor_output, daemon=True)
            monitor_thread.start()
            monitor_thread.join(timeout=timeout)
            
            # Check if all phases completed
            if all(phases_seen.values()):
                print("\n✓ All startup phases completed successfully")
                elapsed = time.time() - start_time
                print(f"✓ Total startup time: {elapsed:.1f}s")
                return True
            else:
                print("\n✗ Startup incomplete:")
                for phase, seen in phases_seen.items():
                    status = "✓" if seen else "✗"
                    print(f"   {status} {phase}")
                return False
                
        except Exception as e:
            print(f"✗ Failed to start system: {e}")
            return False
            
    def test_service_health(self) -> bool:
        """Test that all services are healthy."""
        print("\n" + "=" * 60)
        print("E2E TEST: Service Health Check")
        print("=" * 60)
        
        services = {
            'loader': 5002,
            'logger': 5001,
            'kwd': 5003,
            'stt': 5004,
            'llm': 5005,
            'tts': 5006
        }
        
        all_healthy = True
        
        for name, port in services.items():
            health_client = HealthClient(port=port)
            status = health_client.check_health()
            health_client.close()
            
            if status == 1:  # SERVING
                print(f"✓ {name:8} service: SERVING (port {port})")
            else:
                print(f"✗ {name:8} service: NOT SERVING (port {port})")
                all_healthy = False
                
        return all_healthy
        
    def test_loader_status(self) -> bool:
        """Test loader service status and PIDs."""
        print("\n" + "=" * 60)
        print("E2E TEST: Loader Status")
        print("=" * 60)
        
        try:
            channel = grpc.insecure_channel('127.0.0.1:5002')
            stub = services_pb2_grpc.LoaderServiceStub(channel)
            
            # Get system status
            status = stub.GetStatus(services_pb2.Empty())
            
            print(f"System State: {status.state}")
            print(f"Uptime: {status.uptime_ms / 1000:.1f}s")
            print(f"VRAM Used: {status.vram_used_mb}MB")
            
            # Check if in IDLE state
            if status.state != "IDLE":
                print(f"✗ System not in IDLE state (current: {status.state})")
                channel.close()
                return False
                
            # Get PIDs
            pids_response = stub.GetPids(services_pb2.Empty())
            print("\nService PIDs:")
            for service, pid in pids_response.pids.items():
                print(f"  {service}: {pid}")
                
            channel.close()
            
            if len(pids_response.pids) >= 5:  # At least 5 services
                print("✓ All services have PIDs")
                return True
            else:
                print(f"✗ Only {len(pids_response.pids)} services running")
                return False
                
        except Exception as e:
            print(f"✗ Failed to check loader status: {e}")
            return False
            
    def test_dialog_happy_path(self) -> bool:
        """Test a simple dialog interaction (manual)."""
        print("\n" + "=" * 60)
        print("E2E TEST: Dialog Happy Path (Manual)")
        print("=" * 60)
        
        print("\nThis test requires manual interaction:")
        print("1. Say 'Alexa' to trigger wake word")
        print("2. Wait for response (e.g., 'Yes?')")
        print("3. Ask a question (e.g., 'What time is it?')")
        print("4. Wait for assistant response")
        print("5. Stay silent for 4+ seconds to end dialog")
        
        print("\nPress Enter when ready to start...")
        input()
        
        print("Listening for dialog activity...")
        print("(You have 30 seconds to complete the interaction)")
        
        # In a real test, we would monitor events programmatically
        # For now, this is a manual test
        time.sleep(30)
        
        print("\nDid the dialog work correctly? (y/n): ", end='')
        response = input().strip().lower()
        
        return response == 'y'
        
    def test_failure_recovery(self) -> bool:
        """Test service failure and recovery."""
        print("\n" + "=" * 60)
        print("E2E TEST: Failure Recovery")
        print("=" * 60)
        
        try:
            channel = grpc.insecure_channel('127.0.0.1:5002')
            stub = services_pb2_grpc.LoaderServiceStub(channel)
            
            # Stop a service
            print("\n1. Stopping logger service to test recovery...")
            response = stub.StopService(services_pb2.ServiceRequest(service_name="logger"))
            print(f"   Stop response: {response.message}")
            
            time.sleep(2)
            
            # Check if it's stopped
            health_client = HealthClient(port=5001)
            status = health_client.check_health()
            health_client.close()
            
            if status != 1:  # NOT SERVING
                print("   ✓ Logger service stopped")
            else:
                print("   ✗ Logger service still running")
                channel.close()
                return False
                
            # Restart the service
            print("\n2. Restarting logger service...")
            response = stub.StartService(services_pb2.ServiceRequest(service_name="logger"))
            print(f"   Start response: {response.message}")
            
            # Wait for it to be ready
            time.sleep(3)
            
            # Check if it's running again
            health_client = HealthClient(port=5001)
            if health_client.wait_for_serving(timeout=10):
                print("   ✓ Logger service recovered")
                health_client.close()
                channel.close()
                return True
            else:
                print("   ✗ Logger service failed to recover")
                health_client.close()
                channel.close()
                return False
                
        except Exception as e:
            print(f"✗ Failure recovery test error: {e}")
            return False
            
    def stop_system(self):
        """Stop the system gracefully."""
        print("\n" + "=" * 60)
        print("E2E TEST: Shutdown")
        print("=" * 60)
        
        if self.main_process:
            print("Sending SIGTERM to main process...")
            self.main_process.terminate()
            
            try:
                print("Waiting for graceful shutdown...")
                self.main_process.wait(timeout=10)
                print("✓ System stopped gracefully")
            except subprocess.TimeoutExpired:
                print("Timeout - sending SIGKILL...")
                self.main_process.kill()
                print("✓ System force stopped")
                
    def run_all_tests(self) -> bool:
        """Run all E2E tests."""
        print("\n" + "=" * 60)
        print("VOICE ASSISTANT END-TO-END TEST SUITE")
        print("=" * 60)
        
        test_results = {}
        
        try:
            # Test 1: Cold boot
            if self.start_system():
                test_results["Cold Boot"] = True
                
                # Wait for system to stabilize
                time.sleep(5)
                
                # Test 2: Service health
                test_results["Service Health"] = self.test_service_health()
                
                # Test 3: Loader status
                test_results["Loader Status"] = self.test_loader_status()
                
                # Test 4: Dialog (manual)
                # test_results["Dialog Happy Path"] = self.test_dialog_happy_path()
                print("\n(Skipping manual dialog test - set up audio devices for full test)")
                
                # Test 5: Failure recovery
                test_results["Failure Recovery"] = self.test_failure_recovery()
                
            else:
                test_results["Cold Boot"] = False
                print("\n✗ Cold boot failed - skipping remaining tests")
                
        finally:
            # Always try to stop the system
            self.stop_system()
            
        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        
        for test_name, passed in test_results.items():
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"{status}: {test_name}")
            
        # Overall result
        all_passed = all(test_results.values()) if test_results else False
        
        print("\n" + "=" * 60)
        if all_passed:
            print("✓ ALL TESTS PASSED")
        else:
            print("✗ SOME TESTS FAILED")
        print("=" * 60)
        
        return all_passed


def main():
    """Main entry point for E2E tests."""
    runner = E2ETestRunner()
    
    # Handle Ctrl+C gracefully
    def signal_handler(signum, frame):
        print("\n\nInterrupted - stopping system...")
        runner.stop_system()
        sys.exit(1)
        
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run tests
    success = runner.run_all_tests()
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
