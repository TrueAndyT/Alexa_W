#!/usr/bin/env python3
"""Test that the loader service uses TTS for wake word responses."""

import sys
import time
import subprocess
from pathlib import Path

print("\n" + "="*80)
print("LOADER TTS RESPONSE TEST")
print("="*80)
print("\nThis test will start the full system via the loader service")
print("and verify that TTS (Kokoro voice) is used for wake word responses,")
print("not system sounds.\n")

print("Starting loader service...")
print("When you hear 'Hi, Master!' - that should be the Kokoro TTS voice.")
print("When you say 'Hey Jarvis', the response should also be Kokoro TTS,")
print("not a system beep or bell sound.\n")

print("Press Ctrl+C to stop the test.\n")

# Start the loader service
try:
    venv_python = Path('.venv/bin/python').absolute()
    loader_process = subprocess.Popen(
        [str(venv_python), 'services/loader/loader_service.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1  # Line buffered
    )
    
    # Monitor output
    print("="*80)
    print("LOADER OUTPUT (watching for TTS usage):")
    print("="*80 + "\n")
    
    tts_responses = []
    
    for line in loader_process.stdout:
        print(line, end='')
        
        # Look for TTS-related messages
        if "warm-up greeting" in line.lower():
            tts_responses.append("✓ Warm-up greeting via TTS")
        if "tts service ready" in line.lower():
            tts_responses.append("✓ TTS service loaded")
        if "yes?" in line.lower() or "yes, master?" in line.lower():
            tts_responses.append("✓ Yes phrase sent to TTS")
        if "wake detected" in line.lower():
            print("\n>>> WAKE WORD DETECTED - Listen for TTS response! <<<\n")
            
except KeyboardInterrupt:
    print("\n\nTest stopped by user.")
    
finally:
    if 'loader_process' in locals():
        loader_process.terminate()
        try:
            loader_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            loader_process.kill()
    
    # Cleanup
    subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
    
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    if tts_responses:
        print("\nTTS responses detected:")
        for response in tts_responses:
            print(f"  {response}")
    
    print("\n📝 Notes:")
    print("  - If you heard system beeps/bells instead of Kokoro voice,")
    print("    there may be an audio device conflict or Kokoro isn't loading properly.")
    print("  - Check tts_service.log for any errors.")
    print("  - The TTS should produce a natural voice, not synthetic tones.")
