#!/usr/bin/env python3
"""Test script to verify the new logger console output format."""
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from common.logger_client import LoggerClient


def test_console_output():
    """Test various log events to verify console output."""
    
    print("=" * 60)
    print("Testing Logger Console Output Format")
    print("=" * 60)
    print()
    
    # Create logger clients for different services
    loader_client = LoggerClient("loader")
    kwd_client = LoggerClient("kwd")
    stt_client = LoggerClient("stt")
    llm_client = LoggerClient("llm")
    tts_client = LoggerClient("tts")
    
    # Wait a moment for connections
    time.sleep(0.5)
    
    print("Testing service lifecycle events:")
    print("-" * 40)
    
    # Test service start events
    loader_client.info("service_start", details="Loader")
    loader_client.info("service_start", "Starting KWD service")
    loader_client.info("phase3_ready", "KWD service loaded (PID=1234, port=5003)")
    
    print()
    print("Testing wake word and dialog events:")
    print("-" * 40)
    
    # Test KWD events
    kwd_client.info("kwd_started", "Waiting for wake word")
    kwd_client.info("wake_detected", details="confidence=0.82")
    
    print()
    print("Testing STT and LLM events:")
    print("-" * 40)
    
    # Test STT events
    stt_client.info("stt_started", "Starting speech recognition")
    stt_client.info("stt_final_text", details="what's the weather")
    
    # Test LLM events
    llm_client.info("llm_stream_start", "Starting LLM generation")
    llm_client.info("llm_stream_end", details="It's sunny and 26°C.")
    
    print()
    print("Testing TTS events:")
    print("-" * 40)
    
    # Test TTS events
    tts_client.info("tts_stream_start", "Starting speech synthesis")
    tts_client.info("tts_finished", "Playback completed")
    
    print()
    print("Testing VRAM warning events:")
    print("-" * 40)
    
    # Test VRAM events
    loader_client.error("vram_guardrail", details="used=7900 free=292 guardrail=8000")
    
    print()
    print("Testing phase events:")
    print("-" * 40)
    
    # Test phase events
    loader_client.info("phase1_start", "Starting Phase 1")
    loader_client.info("phase1_ready", "Phase 1 services ready")
    loader_client.info("phase2_start", "Starting Phase 2")
    loader_client.info("phase2_ready", "Phase 2 services ready")
    loader_client.info("warmup_done", "System warmup complete")
    
    print()
    print("Testing non-key events (should not appear):")
    print("-" * 40)
    
    # These should not appear in console with default config
    tts_client.log("tts_chunk", "Playing chunk 12", "DEBUG")
    loader_client.log("health_poll", "Checking service health", "DEBUG")
    
    print()
    print("=" * 60)
    print("Test complete - check output above")
    print("Expected format: {SERVICE:<10}{LEVEL:<6}= {MESSAGE}")
    print("=" * 60)
    
    # Close clients
    loader_client.close()
    kwd_client.close()
    stt_client.close()
    llm_client.close()
    tts_client.close()


if __name__ == "__main__":
    test_console_output()
