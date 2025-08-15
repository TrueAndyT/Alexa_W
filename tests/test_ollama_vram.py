#!/usr/bin/env python3
"""Test Ollama directly with VRAM measurements."""

import sys
import time
import subprocess
from pathlib import Path


def get_vram_usage():
    """Get current VRAM usage in MB."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        used, free = result.stdout.strip().split(', ')
        return int(used.strip()), int(free.strip())
    return 0, 0


def test_ollama_vram():
    """Test Ollama with VRAM measurements."""
    
    print("\n" + "="*80)
    print("OLLAMA LLAMA 3.1 8B Q4 VRAM MEASUREMENT")
    print("="*80)
    
    ollama_process = None
    
    try:
        # Clean up
        print("\n1. Cleaning up...")
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(3)
        
        # Initial VRAM
        used_initial, free_initial = get_vram_usage()
        print(f"\n2. Initial VRAM: {used_initial} MB used, {free_initial} MB free")
        print(f"   Total VRAM: {used_initial + free_initial} MB")
        
        # Start Ollama
        print("\n3. Starting Ollama server...")
        ollama_process = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for ready
        for i in range(10):
            time.sleep(1)
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if result.returncode == 0:
                print("   ✓ Ollama server ready")
                break
        
        # VRAM after Ollama
        used_ollama, free_ollama = get_vram_usage()
        print(f"\n4. VRAM after Ollama start: {used_ollama} MB used (+{used_ollama - used_initial} MB)")
        
        # Load model
        print("\n5. Loading llama3.1:8b-instruct-q4_K_M model...")
        print("   First inference to load model into VRAM...")
        start_time = time.time()
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "Say hello"],
            capture_output=True,
            text=True,
            timeout=60
        )
        load_time = time.time() - start_time
        print(f"   Model loaded in {load_time:.1f}s")
        print(f"   Response: {result.stdout.strip()}")
        
        # VRAM after model load
        used_model, free_model = get_vram_usage()
        model_vram = used_model - used_ollama
        print(f"\n6. VRAM after model load: {used_model} MB used")
        print(f"   Model VRAM usage: {model_vram} MB")
        
        # Load system prompt from Modelfile
        print("\n7. Testing with system prompt from Modelfile...")
        modelfile_path = Path("config/Modelfile")
        if modelfile_path.exists():
            content = modelfile_path.read_text()
            if 'SYSTEM """' in content:
                start = content.index('SYSTEM """') + len('SYSTEM """')
                end = content.index('"""', start)
                system_prompt = content[start:end].strip()
                print(f"   System prompt: {len(system_prompt)} chars")
                print(f"   First line: {system_prompt.split(chr(10))[0][:80]}...")
        
        # Test various prompts
        print("\n8. Testing inference with different prompts...")
        test_prompts = [
            ("Short", "Hi"),
            ("Medium", "What is the capital of France?"),
            ("Long", "Explain quantum computing in simple terms"),
            ("System test", "What is your name and purpose?"),
        ]
        
        for name, prompt in test_prompts:
            print(f"\n   {name} prompt: '{prompt}'")
            start_time = time.time()
            result = subprocess.run(
                ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", prompt],
                capture_output=True,
                text=True,
                timeout=30
            )
            inference_time = time.time() - start_time
            response = result.stdout.strip()
            
            # Measure VRAM during inference
            used_inference, free_inference = get_vram_usage()
            
            print(f"   Response ({inference_time:.1f}s): {response[:100]}...")
            print(f"   VRAM during inference: {used_inference} MB (+{used_inference - used_model} MB)")
        
        # Final measurements
        print("\n" + "="*80)
        print("VRAM USAGE SUMMARY")
        print("="*80)
        print(f"Ollama server overhead:     {used_ollama - used_initial:5d} MB")
        print(f"LLAMA 3.1 8B Q4 MODEL:      {model_vram:5d} MB")
        print(f"Inference overhead (max):   {used_inference - used_model:5d} MB")
        print(f"Peak VRAM usage:            {used_inference:5d} MB")
        print(f"Free VRAM at peak:          {free_inference:5d} MB")
        print("="*80)
        
        # Check if we have enough VRAM for full system
        whisper_vram = 1500  # Whisper small.en
        kokoro_vram = 100    # Kokoro TTS (small)
        other_vram = 500     # Other services
        total_needed = used_inference + whisper_vram + kokoro_vram + other_vram
        
        print(f"\nFull system VRAM estimate:")
        print(f"  LLM (llama3.1:8b-q4):     {used_inference:5d} MB")
        print(f"  STT (Whisper small.en):   {whisper_vram:5d} MB")
        print(f"  TTS (Kokoro):             {kokoro_vram:5d} MB")
        print(f"  Other services:           {other_vram:5d} MB")
        print(f"  TOTAL ESTIMATED:          {total_needed:5d} MB")
        print(f"  Available VRAM:           {used_initial + free_initial:5d} MB")
        
        if total_needed < (used_initial + free_initial):
            print(f"  ✓ Sufficient VRAM (margin: {(used_initial + free_initial) - total_needed} MB)")
        else:
            print(f"  ✗ Insufficient VRAM (need {total_needed - (used_initial + free_initial)} MB more)")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print("\n9. Cleaning up...")
        if ollama_process:
            ollama_process.terminate()
            try:
                ollama_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ollama_process.kill()
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        time.sleep(2)
        used_cleanup, free_cleanup = get_vram_usage()
        print(f"   Final VRAM: {used_cleanup} MB used, {free_cleanup} MB free")


if __name__ == "__main__":
    success = test_ollama_vram()
    sys.exit(0 if success else 1)
