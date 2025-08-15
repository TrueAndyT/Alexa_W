#!/usr/bin/env python3
"""Test the complete dialog chain: KWD → TTS → STT → LLM → TTS."""

import sys
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime

class DialogChainTest:
    def __init__(self):
        self.results = {
            'services_started': [],
            'services_failed': [],
            'chain_events': [],
            'errors': []
        }
        self.loader_process = None
        self.monitoring = True
        
    def log_event(self, event: str, status: str = "INFO"):
        """Log an event with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{timestamp}] {status}: {event}"
        print(msg)
        self.results['chain_events'].append(msg)
        
    def monitor_log_file(self, filename: str, service_name: str):
        """Monitor a service log file for important events."""
        log_path = Path(filename)
        if not log_path.exists():
            return
            
        with open(log_path, 'r') as f:
            # Go to end of file
            f.seek(0, 2)
            
            while self.monitoring:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                    
                # Check for important events
                line_lower = line.lower()
                
                # TTS related
                if service_name == "TTS":
                    if "kokoro" in line_lower and "loaded" in line_lower:
                        self.log_event("✓ Kokoro TTS model loaded", "SUCCESS")
                    elif "mock" in line_lower and "tts" in line_lower:
                        self.log_event("⚠ Using mock TTS (not Kokoro!)", "WARNING")
                    elif "synthesizing" in line_lower:
                        self.log_event(f"TTS synthesizing: {line.strip()}", "TTS")
                    elif "playing" in line_lower or "playback" in line_lower:
                        self.log_event(f"TTS playback: {line.strip()}", "TTS")
                        
                # KWD related
                elif service_name == "KWD":
                    if "wake" in line_lower and "detected" in line_lower:
                        self.log_event("🎤 WAKE WORD DETECTED", "KWD")
                        
                # STT related
                elif service_name == "STT":
                    if "recording" in line_lower or "start" in line_lower:
                        self.log_event("🔴 STT Recording started", "STT")
                    elif "transcription" in line_lower or "result" in line_lower:
                        self.log_event(f"STT result: {line.strip()}", "STT")
                        
                # Loader related
                elif service_name == "LOADER":
                    if "wake detected" in line_lower:
                        self.log_event(">>> WAKE EVENT IN LOADER <<<", "CHAIN")
                    elif "yes?" in line_lower or "yes, master?" in line_lower:
                        self.log_event(">>> TTS 'YES' PHRASE TRIGGERED <<<", "CHAIN")
                    elif "stt" in line_lower and "start" in line_lower:
                        self.log_event(">>> STT STARTED BY LOADER <<<", "CHAIN")
                    elif "user said:" in line_lower:
                        self.log_event(f">>> USER INPUT: {line.strip()} <<<", "CHAIN")
                    elif "llm" in line_lower and "complete" in line_lower:
                        self.log_event(">>> LLM PROCESSING <<<", "CHAIN")
                    elif "streaming" in line_lower and "tts" in line_lower:
                        self.log_event(">>> STREAMING LLM TO TTS <<<", "CHAIN")
                        
    def run_test(self):
        """Run the complete integration test."""
        print("\n" + "="*80)
        print("FULL DIALOG CHAIN INTEGRATION TEST")
        print("="*80)
        print("\nThis test verifies the complete chain:")
        print("  1. KWD detects wake word")
        print("  2. TTS plays 'Yes?' (should be Kokoro voice, not system sound)")
        print("  3. STT starts recording")
        print("  4. User speaks, STT transcribes")
        print("  5. LLM processes input")
        print("  6. TTS speaks LLM response")
        print("="*80 + "\n")
        
        try:
            # Clean up first
            self.log_event("Cleaning up existing services...")
            subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            time.sleep(3)
            
            # Start monitoring threads for each service log
            monitors = []
            for service, filename in [
                ("LOADER", "loader_service.log"),
                ("TTS", "tts_service.log"),
                ("KWD", "kwd_service.log"),
                ("STT", "stt_service.log"),
                ("LLM", "llm_service.log")
            ]:
                thread = threading.Thread(
                    target=self.monitor_log_file,
                    args=(filename, service),
                    daemon=True
                )
                thread.start()
                monitors.append(thread)
            
            # Start the loader
            self.log_event("Starting loader service...")
            venv_python = Path('.venv/bin/python').absolute()
            
            loader_log = open('loader_service.log', 'w')
            self.loader_process = subprocess.Popen(
                [str(venv_python), 'services/loader/loader_service.py'],
                stdout=loader_log,
                stderr=subprocess.STDOUT
            )
            
            # Wait for services to start
            self.log_event("Waiting for all services to initialize...")
            time.sleep(15)  # Give time for all services to load
            
            print("\n" + "="*80)
            print("SERVICES READY - START TESTING")
            print("="*80)
            print("\n📋 TEST INSTRUCTIONS:")
            print("  1. Say 'Hey Jarvis' clearly")
            print("  2. Listen for response - should be TTS voice, NOT system beep")
            print("  3. When you hear the response, speak a question")
            print("  4. Listen for the AI response")
            print("\n  Press Ctrl+C when done testing\n")
            print("="*80 + "\n")
            
            # Keep running and monitoring
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.log_event("\nTest stopped by user", "INFO")
            
        finally:
            self.monitoring = False
            
            # Stop loader
            if self.loader_process:
                self.loader_process.terminate()
                try:
                    self.loader_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.loader_process.kill()
            
            # Cleanup
            subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            
            # Show results
            self.show_results()
            
    def show_results(self):
        """Display test results."""
        print("\n" + "="*80)
        print("TEST RESULTS")
        print("="*80)
        
        # Check for critical chain events
        chain_status = {
            'wake_detected': False,
            'tts_yes_phrase': False,
            'stt_started': False,
            'user_input': False,
            'llm_processing': False,
            'tts_response': False,
            'using_kokoro': False,
            'using_mock': False
        }
        
        for event in self.results['chain_events']:
            if "WAKE EVENT IN LOADER" in event:
                chain_status['wake_detected'] = True
            if "TTS 'YES' PHRASE" in event:
                chain_status['tts_yes_phrase'] = True
            if "STT STARTED BY LOADER" in event:
                chain_status['stt_started'] = True
            if "USER INPUT:" in event:
                chain_status['user_input'] = True
            if "LLM PROCESSING" in event:
                chain_status['llm_processing'] = True
            if "STREAMING LLM TO TTS" in event:
                chain_status['tts_response'] = True
            if "Kokoro TTS model loaded" in event:
                chain_status['using_kokoro'] = True
            if "Using mock TTS" in event:
                chain_status['using_mock'] = True
        
        print("\n🔗 DIALOG CHAIN STATUS:")
        print(f"  {'✓' if chain_status['wake_detected'] else '✗'} Wake word detected by KWD")
        print(f"  {'✓' if chain_status['tts_yes_phrase'] else '✗'} TTS 'Yes?' phrase triggered")
        print(f"  {'✓' if chain_status['stt_started'] else '✗'} STT recording started")
        print(f"  {'✓' if chain_status['user_input'] else '✗'} User input transcribed")
        print(f"  {'✓' if chain_status['llm_processing'] else '✗'} LLM processed input")
        print(f"  {'✓' if chain_status['tts_response'] else '✗'} TTS spoke LLM response")
        
        print("\n🔊 TTS STATUS:")
        if chain_status['using_kokoro']:
            print("  ✓ Using Kokoro TTS (correct)")
        elif chain_status['using_mock']:
            print("  ⚠ Using mock TTS (Kokoro not loaded)")
        else:
            print("  ✗ TTS status unknown")
        
        # Check what you heard
        print("\n🎧 AUDIO CHECK:")
        print("  Did you hear:")
        print("    1. System beep/bell sound? → TTS not being used properly")
        print("    2. Simple sine wave tones? → Mock TTS (Kokoro not loaded)")
        print("    3. Natural voice? → Kokoro TTS working correctly")
        
        print("\n📊 CHAIN COMPLETENESS:")
        working_steps = sum([
            chain_status['wake_detected'],
            chain_status['tts_yes_phrase'],
            chain_status['stt_started'],
            chain_status['user_input'],
            chain_status['llm_processing'],
            chain_status['tts_response']
        ])
        print(f"  {working_steps}/6 steps working")
        
        if working_steps < 6:
            print("\n⚠️ INTEGRATION ISSUES DETECTED:")
            if not chain_status['tts_yes_phrase']:
                print("  - Loader not triggering TTS for 'Yes?' phrase")
            if not chain_status['stt_started']:
                print("  - STT not starting after wake word")
            if not chain_status['user_input']:
                print("  - STT not capturing/transcribing speech")
            if not chain_status['llm_processing']:
                print("  - LLM not processing user input")
            if not chain_status['tts_response']:
                print("  - TTS not speaking LLM response")
                
        print("\n" + "="*80)


if __name__ == "__main__":
    test = DialogChainTest()
    test.run_test()
