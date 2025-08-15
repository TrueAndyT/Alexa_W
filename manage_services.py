#!/usr/bin/env python3
"""Service management script for the voice assistant system."""
import sys
import subprocess
import time
import signal
import os
from pathlib import Path
import psutil
import argparse

# Service configuration
SERVICES = {
    'logger': {
        'port': 5001,
        'script': 'services/logger/logger_service.py',
        'name': 'Logger Service'
    },
    'kwd': {
        'port': 5003,
        'script': 'services/kwd/kwd_service.py',
        'name': 'KWD Service'
    },
    'stt': {
        'port': 5004,
        'script': 'services/stt/stt_service.py',
        'name': 'STT Service'
    },
    'llm': {
        'port': 5005,
        'script': 'services/llm/llm_service.py',
        'name': 'LLM Service'
    },
    'tts': {
        'port': 5006,
        'script': 'services/tts/tts_service.py',
        'name': 'TTS Service'
    },
    'loader': {
        'port': 5002,
        'script': 'services/loader/loader_service.py',
        'name': 'Loader Service'
    }
}


def get_service_pid(service_script):
    """Get PID of a running service."""
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and service_script in ' '.join(cmdline):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def start_service(service_key):
    """Start a service."""
    service = SERVICES[service_key]
    script_path = service['script']
    
    # Check if already running
    pid = get_service_pid(script_path)
    if pid:
        print(f"✓ {service['name']} already running (PID: {pid})")
        return True
    
    # Start the service
    print(f"Starting {service['name']}...", end=' ')
    # Ensure logs directory exists
    Path('logs').mkdir(exist_ok=True)
    log_file = f"logs/{service_key}_service.log"
    
    try:
        # Use the virtual environment's Python
        venv_python = str(Path('.venv/bin/python').absolute())
        process = subprocess.Popen(
            [venv_python, script_path],
            stdout=open(log_file, 'w'),
            stderr=subprocess.STDOUT,
            cwd=os.getcwd()
        )
        
        # Wait a bit and check if it started
        time.sleep(2)
        if process.poll() is None:
            pid = process.pid
            print(f"✓ Started (PID: {pid})")
            return True
        else:
            print(f"✗ Failed to start (check {log_file})")
            return False
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def stop_service(service_key):
    """Stop a service."""
    service = SERVICES[service_key]
    script_path = service['script']
    
    pid = get_service_pid(script_path)
    if not pid:
        print(f"✓ {service['name']} not running")
        return True
    
    print(f"Stopping {service['name']} (PID: {pid})...", end=' ')
    try:
        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=5)
        print("✓ Stopped")
        return True
    except psutil.TimeoutExpired:
        process.kill()
        print("✓ Killed")
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def status_service(service_key):
    """Check status of a service."""
    service = SERVICES[service_key]
    script_path = service['script']
    
    pid = get_service_pid(script_path)
    if pid:
        print(f"✓ {service['name']}: Running (PID: {pid}, Port: {service['port']})")
        return True
    else:
        print(f"✗ {service['name']}: Not running")
        return False


def start_all():
    """Start all services in order."""
    print("Starting all services...")
    # Only start services that are implemented
    order = ['logger', 'kwd', 'stt']  # llm and tts not yet implemented
    
    for service in order:
        if service in SERVICES:
            if Path(SERVICES[service]['script']).exists():
                start_service(service)
                time.sleep(1)
            else:
                print(f"⚠ {SERVICES[service]['name']} not yet implemented")
    
    print("\nAll available services started!")


def stop_all():
    """Stop all services."""
    print("Stopping all services...")
    
    for service in SERVICES:
        stop_service(service)
    
    print("\nAll services stopped!")


def status_all():
    """Check status of all services."""
    print("Service Status:")
    print("-" * 50)
    
    for service in SERVICES:
        status_service(service)
    
    print("-" * 50)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Manage voice assistant services')
    parser.add_argument('command', choices=['start', 'stop', 'restart', 'status'],
                        help='Command to execute')
    parser.add_argument('service', nargs='?', default='all',
                        help='Service name or "all" (default: all)')
    
    args = parser.parse_args()
    
    if args.service == 'all':
        if args.command == 'start':
            start_all()
        elif args.command == 'stop':
            stop_all()
        elif args.command == 'restart':
            stop_all()
            time.sleep(2)
            start_all()
        elif args.command == 'status':
            status_all()
    else:
        if args.service not in SERVICES:
            print(f"Unknown service: {args.service}")
            print(f"Available services: {', '.join(SERVICES.keys())}")
            sys.exit(1)
        
        if args.command == 'start':
            start_service(args.service)
        elif args.command == 'stop':
            stop_service(args.service)
        elif args.command == 'restart':
            stop_service(args.service)
            time.sleep(1)
            start_service(args.service)
        elif args.command == 'status':
            status_service(args.service)


if __name__ == '__main__':
    main()
