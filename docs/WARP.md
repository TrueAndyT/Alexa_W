# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a phased-parallel voice assistant system with multiple microservices communicating via gRPC. The system consists of 6 main services that handle wake word detection, speech-to-text, language model interaction, and text-to-speech, all orchestrated by a loader service and supported by centralized logging.

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

## Quick Start

### Starting the System
```bash
# Activate virtual environment
source .venv/bin/activate

# Start the entire system using main bootstrap
python main.py

# The bootstrap will:
# 1. Check environment prerequisites
# 2. Generate proto files if needed
# 3. Start the loader service
# 4. Monitor and restart services as needed
```

## Development Commands

### Main Bootstrap
```bash
# Start the entire system
python main.py

# The bootstrap automatically:
# - Checks Python version (3.11+)
# - Verifies virtual environment
# - Generates proto files if missing
# - Creates required directories (logs/, models/)
# - Checks GPU availability
# - Verifies Ollama installation
# - Starts and monitors the loader service
```

### Testing Services
```bash
# Interactive tests (say "Alexa" to trigger)
python tests/test_1_kwd_interactive.py

# Test STT (Speech-to-Text) - speak after prompt
python tests/test_2_stt_interactive.py

# Test LLM interaction
python tests/test_3_llm_interactive.py
python tests/test_3_llm_interactive_with_loader.py

# Test TTS (Text-to-Speech)
python tests/test_4_tts_interactive.py

# Test full end-to-end chain
python tests/test_e2e.py

# Test full chain with all services
python tests/test_full_chain.py

# Test KWD-TTS integration
python tests/test_kwd_tts_integration.py

# Test loader with TTS response
python tests/test_loader_tts_response.py
```

### Service Unit Tests
```bash
# Test individual services
python tests/services/test_logger.py
python tests/services/test_kwd.py
python tests/services/test_stt.py
python tests/services/test_llm.py
python tests/services/test_tts.py

# Test all services
python tests/services/test_all_services.py
```

### VRAM and Performance Testing
```bash
# Test VRAM usage across all services
python tests/test_all_vram.py

# Test LLM VRAM requirements
python tests/test_llm_vram.py
python tests/test_ollama_vram.py

# Monitor GPU usage
nvidia-smi
python common/gpu_monitor.py
```

### Regenerate gRPC Code
```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/services.proto
```

### View Service Logs
```bash
# Application logs (all services)
tail -f logs/app.log

# Dialog transcripts
ls logs/dialog_*.log
tail -f logs/dialog_*.log

# Memory/VRAM usage
tail -f logs/memory.log
```

## Architecture (IMC v1.2)

### Service Communication Flow
1. **Logger Service** (port 5001) - Centralized logging, always starts first
2. **Loader Service** (port 5002) - Service lifecycle management only (no dialog logic)
3. **KWD Service** (port 5003) - Detects wake word and initiates dialog internally
4. **STT Service** (port 5004) - Owns the entire dialog loop, orchestrates LLM→TTS flow
5. **LLM Service** (port 5005) - Streams completions (no dialog logic)
6. **TTS Service** (port 5006) - Synthesizes speech and emits playback events

### Startup Phases
The system uses a phased-parallel startup orchestrated by the loader:

**Phase 1 (Parallel)**: TTS + LLM services start simultaneously
- Both services require significant VRAM
- Parallel startup reduces overall boot time
- 8-second timeout for phase completion

**Phase 2**: STT service starts after Phase 1
- Requires VRAM allocation
- Uses Whisper model with CUDA acceleration

**Phase 3**: KWD service starts last
- Lightweight service using ONNX model
- Minimal VRAM requirements

### Key Design Patterns

#### No Controller Architecture (IMC v1.2)
- **Loader** only manages service lifecycle (start/stop/restart)
- **KWD** handles wake detection internally (TTS speak → Logger dialog → STT start → self disable)
- **STT** owns the entire dialog loop (LLM streaming → TTS playback → 4s timer → KWD re-enable)
- No central controller process exists

#### BaseService Pattern
All services inherit from `common/base_service.py` which provides:
- gRPC server setup with health checks
- Configuration loading from `config/config.ini`
- Signal handling for graceful shutdown
- Centralized logging via `logger_client.py`
- VRAM checking before startup

#### Health Check System
Each service implements gRPC health checks accessible via `common/health_client.py`:
- Services report SERVING when fully operational
- Loader monitors health status every 2 seconds
- Automatic restart on service failure with backoff

#### Dialog Management (STT-Led)
- **KWD** creates dialog via Logger.NewDialog and starts STT
- **STT** manages the entire dialog flow:
  - Processes user speech to text with VAD (~2s silence)
  - Streams LLM response directly to TTS
  - Monitors TTS playback completion
  - Manages 4-second follow-up timer
  - Re-enables KWD when dialog ends
- Each conversation gets a unique `dialog_id`
- Logger service creates dialog-specific log files

### Service Dependencies
- **All services** use **Logger** for centralized logging via `logger_client.py`
- **KWD** calls TTS.Speak, Logger.NewDialog, and STT.Start internally
- **STT** calls LLM.Complete, TTS.SpeakStream, and KWD.Start for dialog orchestration
- **Loader** only manages service lifecycle (no dialog dependencies)
- All services require minimum 7640MB VRAM (configured in config.ini)

## Configuration

Main configuration is in `config/config.ini`:

### System Settings
- Minimum VRAM: 7640MB
- Log directory: logs/
- Models directory: models/

### Service Ports
- Logger: 5001
- Loader: 5002
- KWD: 5003
- STT: 5004
- LLM: 5005
- TTS: 5006

### Service-Specific Settings
- **KWD**: "Alexa" wake word with 0.6 confidence threshold, 1s cooldown, handles dialog initiation
- **STT**: Faster-Whisper small.en model with built-in Silero VAD, 2s silence detection, owns dialog loop, 4s follow-up timer
- **LLM**: Llama 3.1 8B Instruct Q4_K_M via Ollama, streaming completions
- **TTS**: Kokoro with af_heart voice, CUDA acceleration, 24kHz sample rate, playback events
- **Loader**: Service lifecycle only, phased startup, health checks every 2s, auto-restart

## Service Implementation Status

### ✅ Fully Implemented (IMC v1.2 Compliant)
- **Main Bootstrap**: System launcher with environment checks and monitoring
- **Logger Service**: Centralized logging with app.log, dialog_*.log, and memory.log
- **Loader Service**: Service lifecycle management only (no dialog logic)
- **KWD Service**: Wake detection with internal dialog initiation chain
- **STT Service**: Faster-Whisper with built-in VAD, dialog loop owner with LLM→TTS orchestration and 4s timer
- **LLM Service**: Ollama streaming completions (no dialog logic)
- **TTS Service**: Kokoro synthesis with playback event streaming

### 🔧 Features in Progress
- End-to-end dialog flow optimization
- Advanced error recovery mechanisms
- Multi-language support
- Custom wake word training
- Voice activity detection improvements

## Performance Considerations
- Services use CUDA acceleration when available
- STT uses Faster-Whisper with int8_float16 compute for efficient GPU usage
- Built-in Silero VAD in Faster-Whisper reduces unnecessary transcription
- Beam size=1 for faster inference with lower memory usage
- 16kHz audio sampling for optimal wake word detection
- Services implement cooldown periods to prevent rapid re-triggering
- Phased startup minimizes peak VRAM usage
- Streaming architecture reduces latency
- Buffer management for smooth audio playback

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
# Monitor continuously
watch -n 1 nvidia-smi
```

### Ollama issues
```bash
# Check Ollama status
ollama list
# Pull required model
ollama pull llama3.1:8b-instruct-q4_K_M
# Test Ollama
ollama run llama3.1:8b-instruct-q4_K_M "Hello"
```

### Service crashes
```bash
# Check logs for errors
tail -f logs/app.log
# Check specific dialog logs
tail -f logs/dialog_*.log
# Manually restart via main.py (it handles restarts)
```

## Testing Individual Components

When developing new features:
1. Test service in isolation using test scripts in `tests/`
2. Check health status before testing functionality
3. Monitor service logs during development
4. Use dialog IDs to trace requests across services
5. Verify VRAM usage doesn't exceed limits

## gRPC Service Definitions

All service interfaces are defined in `proto/services.proto`. Key RPCs:

### Logger Service
- `WriteApp`: Write application-level logs
- `NewDialog`: Create new dialog session
- `WriteDialog`: Write dialog-specific logs

### KWD Service
- `Configure`: Runtime configuration
- `Start`: Enable wake word detection
- `Stop`: Disable wake word detection
- `Events` (stream): Wake word detection events

### STT Service
- `Configure`: Runtime configuration
- `Start`: Start speech recognition for dialog
- `Stop`: Stop speech recognition
- `Results` (stream): Transcription results

### LLM Service
- `Configure`: Runtime configuration
- `Complete` (stream): Stream completion tokens

### TTS Service
- `Configure`: Runtime configuration
- `Speak`: Unary text-to-speech
- `SpeakStream`: Stream text chunks for synthesis
- `PlaybackEvents` (stream): Audio playback status

### Loader Service
- `StartService`: Start a specific service
- `StopService`: Stop a specific service
- `GetPids`: Get service PIDs
- `GetStatus`: System status and health

## Directory Structure
```
Alexa_W/
├── main.py                 # Bootstrap launcher
├── config/
│   ├── config.ini         # Main configuration
│   └── Modelfile          # Ollama model configuration
├── services/
│   ├── loader_service.py  # Orchestrator
│   ├── logger_service.py  # Logging service
│   ├── kwd_service.py     # Wake word detection
│   ├── stt_service.py     # Speech-to-text
│   ├── llm_service.py     # Language model
│   └── tts_service.py     # Text-to-speech
├── common/
│   ├── base_service.py    # Base service class
│   ├── config_loader.py   # Configuration utilities
│   ├── gpu_monitor.py     # GPU monitoring
│   └── health_client.py   # Health check client
├── proto/
│   └── services.proto     # gRPC definitions
├── tests/
│   ├── test_*.py          # Integration tests
│   └── services/          # Unit tests
├── models/                # Model files
└── logs/                  # Log files
```

## Development Workflow

1. **Always activate virtual environment first**:
   ```bash
   source .venv/bin/activate
   ```

2. **Start the system**:
   ```bash
   python main.py
   ```

3. **Monitor logs in separate terminal**:
   ```bash
   tail -f logs/app.log  # All service logs
   tail -f logs/dialog_*.log  # Dialog transcripts
   tail -f logs/memory.log  # VRAM usage
   ```

4. **Test the dialog flow**:
   - Say "Alexa" to trigger wake word
   - KWD will speak confirmation and start STT
   - Speak your request
   - STT processes and sends to LLM→TTS
   - 4s window for follow-up after response
   - Silence ends dialog, KWD re-enabled

5. **Debug issues**:
   - Check logs/app.log for all service events
   - Look for dialog_* events from STT service
   - Verify health status with loader
   - Monitor VRAM in memory.log

## Important Notes

- **VRAM Management**: The system requires ~8GB VRAM minimum. Monitor usage to prevent OOM errors.
- **Phased Startup**: Services start in phases to manage VRAM allocation efficiently.
- **Health Monitoring**: All services implement health checks; unhealthy services are automatically restarted.
- **Dialog Flow**: STT service owns the entire dialog loop per IMC v1.2 spec.
- **No Controller**: Loader only manages service lifecycle, not dialog orchestration.
- **Streaming Architecture**: All services use streaming where possible to minimize latency.
- **Error Recovery**: The loader implements automatic restart with exponential backoff.
- **Virtual Environment**: Always use the virtual environment to ensure correct dependencies.
- **Dependency Management**: Use `uv` for all package installations and updates.
- **Centralized Logging**: All services log to app.log via logger service, no individual service logs.

## Common Development Tasks

### Adding a New Service
1. Create service file in `services/` directory
2. Inherit from `BaseService` class
3. Define gRPC interface in `proto/services.proto`
4. Regenerate proto files
5. Add configuration section in `config/config.ini`
6. Update loader service to manage new service
7. Create test files in `tests/`

### Debugging Service Communication
1. Enable debug logging in service
2. Use dialog IDs to trace requests
3. Monitor gRPC health checks
4. Check inter-service connectivity
5. Verify proto message formats

### Optimizing Performance
1. Monitor VRAM usage patterns
2. Profile service startup times
3. Optimize model loading
4. Tune buffer sizes for streaming
5. Adjust timeout values based on hardware

## Additional Resources

- Proto documentation: See `proto/services.proto` for detailed message formats
- Configuration reference: See `config/config.ini` for all available settings
- Test examples: See `tests/` directory for usage examples
- Service logs: Check `logs/` directory for detailed debugging information
