# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a phased-parallel voice assistant system with multiple microservices communicating via gRPC. The system consists of 7 processes that handle wake word detection, speech-to-text, language model interaction, and text-to-speech.

## Virtual Environment and Dependencies

**IMPORTANT**: This project uses `uv` for dependency management and requires Python 3.11 with a virtual environment.

### Environment Setup
```bash
# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install all dependencies
uv pip install -r requirements.txt
```

## Development Commands

### Service Management
```bash
# Check status of all services
python manage_services.py status

# Start specific service
python manage_services.py start logger
python manage_services.py start kwd
python manage_services.py start stt

# Stop service
python manage_services.py stop kwd

# Restart service
python manage_services.py restart logger

# Start all implemented services
python manage_services.py start all

# Stop all services
python manage_services.py stop all
```

### Testing Services
```bash
# Test KWD (Keyword Detection) - say "Alexa" to trigger
python tests/test_kwd.py

# Test STT (Speech-to-Text) - speak after prompt
python tests/test_stt.py

# Test continuous STT recognition
python tests/test_stt.py --continuous
```

### Regenerate gRPC Code
```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/services.proto
```

### Monitor GPU Usage
```bash
nvidia-smi
python common/gpu_monitor.py
```

### View Service Logs
```bash
# Individual service logs
tail -f logger_service.log
tail -f kwd_service.log
tail -f stt_service.log

# Application logs
tail -f logs/app.log

# Dialog logs
ls logs/dialog_*.log
```

## Architecture

### Service Communication Flow
1. **Logger Service** (port 5001) - Centralized logging, always starts first
2. **KWD Service** (port 5003) - Detects "Alexa" wake word, triggers dialog start
3. **STT Service** (port 5004) - Converts speech to text using Whisper
4. **LLM Service** (port 5005) - Processes text through Ollama (not yet implemented)
5. **TTS Service** (port 5006) - Converts response to speech (not yet implemented)
6. **Loader Service** (port 5002) - Orchestrates phased startup (not yet implemented)

### Key Design Patterns

#### BaseService Pattern
All services inherit from `common/base_service.py` which provides:
- gRPC server setup with health checks
- Configuration loading from `config/config.ini`
- Signal handling for graceful shutdown
- Consistent logging format

#### Health Check System
Each service implements gRPC health checks accessible via `common/health_client.py`. Services wait for dependencies to be SERVING before proceeding.

#### Dialog Management
- Each conversation gets a unique `dialog_id`
- Logger service creates dialog-specific log files
- Services pass dialog context through gRPC messages

### Service Dependencies
- **STT** depends on **Logger** for dialog logging
- **KWD** triggers dialog creation in **Logger**
- All services require minimum 8GB VRAM (configured in config.ini)

## Configuration

Main configuration is in `config/config.ini`:
- Service ports: 5001-5006
- VRAM guardrail: 8000MB minimum
- Wake word: "Alexa" with 0.6 confidence threshold
- STT: Whisper small.en model with 2s VAD silence
- All services bind to localhost only for security

## Service Implementation Status

### Completed (✅)
- Logger Service: Full application and dialog logging
- KWD Service: OpenWakeWord integration with "Alexa" detection
- STT Service: Whisper with WebRTC VAD, CUDA acceleration

### To Be Implemented (🚧)
- LLM Service: Ollama bridge for language processing
- TTS Service: Kokoro integration for speech synthesis
- Loader Service: Phased orchestration of all services

## Performance Considerations
- Services use CUDA acceleration when available
- STT uses WebRTC VAD to optimize Whisper processing
- 16kHz audio sampling for optimal wake word detection
- Services implement cooldown periods to prevent rapid re-triggering

## Troubleshooting Common Issues

### Port conflicts
```bash
# Check what's using a port
lsof -i :5001
# Kill if needed
kill -9 <PID>
```

### Audio device issues
```bash
# List available audio devices
python -m sounddevice
```

### VRAM monitoring
```bash
# Check GPU memory before starting services
nvidia-smi
```

## Testing Individual Components

When developing new features:
1. Test service in isolation using test scripts in `tests/`
2. Check health status before testing functionality
3. Monitor service logs during development
4. Use dialog IDs to trace requests across services

## gRPC Service Definitions

All service interfaces are defined in `proto/services.proto`. Key RPCs:
- **Logger**: WriteApp, NewDialog, WriteDialog
- **KWD**: Events (stream), Enable, Disable  
- **STT**: Start, Stop, Results (stream)
- **LLM**: Complete (stream) - to be implemented
- **TTS**: Speak, SpeakStream, PlaybackEvents - to be implemented
