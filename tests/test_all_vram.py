#!/usr/bin/env python3
"""Test all services loaded together to verify VRAM usage."""

import sys
import time
import subprocess
import grpc
from pathlib import Path

# Add directories to path
sys.path.insert(0, str(Path(__file__).parent))

from proto import services_pb2, services_pb2_grpc
from common.health_client import HealthClient


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


def test_all_services_vram():
    """Test all services loaded together."""
    
    print("\n" + "="*80)
    print("FULL SYSTEM VRAM TEST - ALL SERVICES")
    print("="*80)
    
    # Track all processes
    processes = {}
    vram_measurements = {}
    
    try:
        # Clean up everything
        print("\n1. Cleaning up all existing services...")
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(3)
        
        # Initial VRAM
        used_initial, free_initial = get_vram_usage()
        total_vram = used_initial + free_initial
        vram_measurements['initial'] = {'used': used_initial, 'free': free_initial}
        print(f"\n2. Initial state:")
        print(f"   Total VRAM: {total_vram} MB")
        print(f"   Used: {used_initial} MB, Free: {free_initial} MB")
        
        venv_python = Path('.venv/bin/python').absolute()
        
        # Start Ollama first (needed for LLM)
        print("\n3. Starting Ollama server...")
        processes['ollama'] = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        for i in range(10):
            time.sleep(1)
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if result.returncode == 0:
                print("   ✓ Ollama server ready")
                break
        
        used_ollama, free_ollama = get_vram_usage()
        vram_measurements['ollama'] = {'used': used_ollama, 'free': free_ollama}
        print(f"   VRAM after Ollama: {used_ollama} MB used (+{used_ollama - used_initial} MB)")
        
        # Load LLM model
        print("\n4. Loading LLM model (llama3.1:8b-instruct-q4_K_M)...")
        print("   Pre-loading model into VRAM...")
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "hi"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        used_llm_model, free_llm_model = get_vram_usage()
        vram_measurements['llm_model'] = {'used': used_llm_model, 'free': free_llm_model}
        llm_vram = used_llm_model - used_ollama
        print(f"   ✓ LLM model loaded")
        print(f"   VRAM after LLM model: {used_llm_model} MB used (+{llm_vram} MB)")
        print(f"   Free VRAM remaining: {free_llm_model} MB")
        
        # Start Logger service (dependency for others)
        print("\n5. Starting Logger service...")
        logger_log = open('logger_service.log', 'w')
        processes['logger'] = subprocess.Popen(
            [str(venv_python), 'services/logger/logger_service.py'],
            stdout=logger_log,
            stderr=subprocess.STDOUT
        )
        print(f"   Logger PID: {processes['logger'].pid}")
        
        # Wait for logger
        time.sleep(2)
        logger_health = HealthClient(port=5001)
        for i in range(10):
            if logger_health.check() == "SERVING":
                print("   ✓ Logger service ready")
                break
            time.sleep(1)
        
        used_logger, free_logger = get_vram_usage()
        vram_measurements['logger'] = {'used': used_logger, 'free': free_logger}
        print(f"   VRAM after Logger: {used_logger} MB used (+{used_logger - used_llm_model} MB)")
        
        # Start KWD service
        print("\n6. Starting KWD service...")
        kwd_log = open('kwd_service.log', 'w')
        processes['kwd'] = subprocess.Popen(
            [str(venv_python), 'services/kwd/kwd_service.py'],
            stdout=kwd_log,
            stderr=subprocess.STDOUT
        )
        print(f"   KWD PID: {processes['kwd'].pid}")
        
        # Wait for KWD
        time.sleep(3)
        kwd_health = HealthClient(port=5003)
        for i in range(20):
            if kwd_health.check() == "SERVING":
                print("   ✓ KWD service ready")
                break
            time.sleep(1)
        
        used_kwd, free_kwd = get_vram_usage()
        vram_measurements['kwd'] = {'used': used_kwd, 'free': free_kwd}
        print(f"   VRAM after KWD: {used_kwd} MB used (+{used_kwd - used_logger} MB)")
        print(f"   Free VRAM remaining: {free_kwd} MB")
        
        # Start STT service (Whisper - significant VRAM user)
        print("\n7. Starting STT service (Whisper)...")
        stt_log = open('stt_service.log', 'w')
        processes['stt'] = subprocess.Popen(
            [str(venv_python), 'services/stt/stt_service.py'],
            stdout=stt_log,
            stderr=subprocess.STDOUT
        )
        print(f"   STT PID: {processes['stt'].pid}")
        print("   Loading Whisper model (this uses ~1.5GB VRAM)...")
        
        # Wait for STT
        time.sleep(10)
        stt_health = HealthClient(port=5004)
        for i in range(30):
            if stt_health.check() == "SERVING":
                print("   ✓ STT service ready")
                break
            time.sleep(2)
        
        used_stt, free_stt = get_vram_usage()
        vram_measurements['stt'] = {'used': used_stt, 'free': free_stt}
        stt_vram = used_stt - used_kwd
        print(f"   VRAM after STT: {used_stt} MB used (+{stt_vram} MB)")
        print(f"   Free VRAM remaining: {free_stt} MB")
        
        # Start LLM service
        print("\n8. Starting LLM service...")
        llm_log = open('llm_service.log', 'w')
        processes['llm'] = subprocess.Popen(
            [str(venv_python), 'services/llm/llm_service.py'],
            stdout=llm_log,
            stderr=subprocess.STDOUT
        )
        print(f"   LLM PID: {processes['llm'].pid}")
        
        # Wait for LLM
        time.sleep(3)
        llm_health = HealthClient(port=5005)
        for i in range(20):
            if llm_health.check() == "SERVING":
                print("   ✓ LLM service ready")
                break
            time.sleep(1)
        
        used_llm, free_llm = get_vram_usage()
        vram_measurements['llm'] = {'used': used_llm, 'free': free_llm}
        print(f"   VRAM after LLM service: {used_llm} MB used (+{used_llm - used_stt} MB)")
        print(f"   Free VRAM remaining: {free_llm} MB")
        
        # Start TTS service (Kokoro)
        print("\n9. Starting TTS service (Kokoro)...")
        tts_log = open('tts_service.log', 'w')
        processes['tts'] = subprocess.Popen(
            [str(venv_python), 'services/tts/tts_service.py'],
            stdout=tts_log,
            stderr=subprocess.STDOUT
        )
        print(f"   TTS PID: {processes['tts'].pid}")
        print("   Loading Kokoro TTS model...")
        
        # Wait for TTS
        time.sleep(10)
        tts_health = HealthClient(port=5006)
        for i in range(30):
            if tts_health.check() == "SERVING":
                print("   ✓ TTS service ready")
                break
            time.sleep(2)
        
        used_tts, free_tts = get_vram_usage()
        vram_measurements['tts'] = {'used': used_tts, 'free': free_tts}
        tts_vram = used_tts - used_llm
        print(f"   VRAM after TTS: {used_tts} MB used (+{tts_vram} MB)")
        print(f"   Free VRAM remaining: {free_tts} MB")
        
        # Final check with all services running
        print("\n10. All services loaded - checking stability...")
        time.sleep(5)
        
        used_final, free_final = get_vram_usage()
        vram_measurements['final'] = {'used': used_final, 'free': free_final}
        
        # Verify all services are still healthy
        print("\n11. Verifying all services are healthy...")
        all_healthy = True
        
        services_status = [
            ("Logger", 5001),
            ("KWD", 5003),
            ("STT", 5004),
            ("LLM", 5005),
            ("TTS", 5006)
        ]
        
        for name, port in services_status:
            health = HealthClient(port=port)
            status = health.check()
            if status == "SERVING":
                print(f"   ✓ {name}: HEALTHY")
            else:
                print(f"   ✗ {name}: {status}")
                all_healthy = False
        
        # Print comprehensive VRAM report
        print("\n" + "="*80)
        print("VRAM USAGE REPORT - ALL SERVICES LOADED")
        print("="*80)
        print(f"System Configuration:")
        print(f"  Total VRAM:              {total_vram:6d} MB")
        print(f"  Config min_vram_mb:      {7640:6d} MB")
        print()
        print(f"Service VRAM Usage:")
        print(f"  Initial state:           {vram_measurements['initial']['used']:6d} MB")
        print(f"  Ollama server:           {vram_measurements['ollama']['used'] - vram_measurements['initial']['used']:6d} MB")
        print(f"  LLM model (llama3.1:8b): {vram_measurements['llm_model']['used'] - vram_measurements['ollama']['used']:6d} MB")
        print(f"  Logger service:          {vram_measurements['logger']['used'] - vram_measurements['llm_model']['used']:6d} MB")
        print(f"  KWD service:             {vram_measurements['kwd']['used'] - vram_measurements['logger']['used']:6d} MB")
        print(f"  STT (Whisper):           {vram_measurements['stt']['used'] - vram_measurements['kwd']['used']:6d} MB")
        print(f"  LLM service:             {vram_measurements['llm']['used'] - vram_measurements['stt']['used']:6d} MB")
        print(f"  TTS (Kokoro):            {vram_measurements['tts']['used'] - vram_measurements['llm']['used']:6d} MB")
        print("-"*80)
        print(f"TOTAL VRAM USED:           {used_final:6d} MB")
        print(f"FREE VRAM REMAINING:       {free_final:6d} MB")
        print(f"UTILIZATION:               {(used_final/total_vram)*100:6.1f} %")
        print("="*80)
        
        # Final verdict
        if free_final > 500 and all_healthy:
            print("\n✓ SUCCESS: All services loaded and healthy!")
            print(f"  {free_final} MB VRAM available for inference overhead")
            if free_final < 1000:
                print("  ⚠ Warning: Low VRAM margin, system may be unstable under load")
        elif not all_healthy:
            print("\n✗ FAILURE: Some services are not healthy")
        else:
            print("\n✗ FAILURE: Insufficient VRAM!")
            print(f"  Only {free_final} MB free (need at least 500 MB buffer)")
        
        # Keep services running for observation
        print("\n12. Services will remain running for 30 seconds...")
        print("    Use 'nvidia-smi' in another terminal to observe")
        print("    Press Ctrl+C to stop early")
        
        for i in range(30):
            print(f"    {30-i} seconds remaining...", end='\r')
            time.sleep(1)
        
        return free_final > 500 and all_healthy
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return False
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup all services
        print("\n\n13. Cleaning up all services...")
        
        for name, process in processes.items():
            if process and process.poll() is None:
                print(f"    Stopping {name}...")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        
        # Extra cleanup
        subprocess.run(["pkill", "-f", "loader_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "logger_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "kwd_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "stt_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "llm_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "tts_service.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        time.sleep(3)
        used_cleanup, free_cleanup = get_vram_usage()
        print(f"\n    Final VRAM after cleanup: {used_cleanup} MB used, {free_cleanup} MB free")


if __name__ == "__main__":
    success = test_all_services_vram()
    sys.exit(0 if success else 1)
