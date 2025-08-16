#!/usr/bin/env python3
"""Test script to check logger output after system is running."""
import time
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from common.logger_client import LoggerClient


def test_running_logger():
    """Test logger output when system is already running."""
    
    # Wait a moment to ensure logger service is up
    time.sleep(1)
    
    # Create a test client
    test_client = LoggerClient("test")
    
    # Send some test events that should appear in console
    print("\n" + "="*60)
    print("Testing logger events (these should appear formatted):")
    print("="*60)
    
    # Test wake word detection
    test_client.info("wake_detected", details="confidence=0.95")
    
    # Test STT
    test_client.info("stt_final_text", details="what's the time")
    
    # Test LLM
    test_client.info("llm_stream_end", details="It's 3:45 PM")
    
    # Test TTS
    test_client.info("tts_finished", "Playback complete")
    
    print("="*60)
    print("Test complete")
    
    test_client.close()


if __name__ == "__main__":
    test_running_logger()
