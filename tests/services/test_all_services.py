#!/usr/bin/env python3
"""Master test runner - tests each service independently in sequence."""

import sys
import subprocess
import time
from pathlib import Path


def run_test(test_script: str) -> bool:
    """Run a single test script and return success status."""
    print("\n" + "="*80)
    print(f"Running {test_script}")
    print("="*80)
    
    result = subprocess.run(
        [sys.executable, test_script],
        capture_output=False,  # Show output in real-time
        text=True
    )
    
    return result.returncode == 0


def main():
    """Run all service tests sequentially."""
    print("\n" + "#"*80)
    print("# ALEXA_W SERVICE TEST SUITE")
    print("# Testing each service independently")
    print("#"*80)
    
    # Kill all services first
    print("\nKilling all existing services...")
    subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
    time.sleep(2)
    
    # Check initial GPU memory
    print("\nChecking initial GPU memory...")
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free,memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        free, used = result.stdout.strip().split(', ')
        print(f"GPU Memory - Free: {free} MB, Used: {used} MB")
    
    # Test scripts in order of dependency/complexity
    test_scripts = [
        "tests/services/test_logger.py",  # Simplest, no GPU
        "tests/services/test_kwd.py",      # Small model
        "tests/services/test_stt.py",      # Whisper model (~1.5GB VRAM)
        "tests/services/test_tts.py",      # TTS model
        "tests/services/test_llm.py",      # Largest model
    ]
    
    results = {}
    
    for test_script in test_scripts:
        script_path = Path(test_script)
        if not script_path.exists():
            print(f"\n⚠ Test script not found: {test_script}")
            results[test_script] = False
            continue
        
        success = run_test(test_script)
        results[test_script] = success
        
        if not success:
            print(f"\n✗ Test failed: {test_script}")
            print("Stopping test suite due to failure.")
            # Don't continue if a test fails
            break
        
        # Give time between tests for cleanup
        time.sleep(2)
        
        # Check GPU memory after each test
        print("\nChecking GPU memory after test...")
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            free, used = result.stdout.strip().split(', ')
            print(f"GPU Memory - Free: {free} MB, Used: {used} MB")
    
    # Print summary
    print("\n" + "#"*80)
    print("# TEST SUMMARY")
    print("#"*80)
    
    for test_script, success in results.items():
        status = "✓ PASSED" if success else "✗ FAILED"
        service_name = Path(test_script).stem.replace("test_", "").upper()
        print(f"{service_name:10} : {status}")
    
    # Overall result
    all_passed = all(results.values())
    
    if all_passed:
        print("\n" + "="*80)
        print("✓ ALL TESTS PASSED")
        print("="*80)
    else:
        print("\n" + "="*80)
        print("✗ SOME TESTS FAILED")
        print("="*80)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
