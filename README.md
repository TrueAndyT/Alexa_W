# Alexa Voice Assistant System

A phased-parallel voice assistant system with multiple microservices communicating via gRPC.

## Architecture

The system consists of 7 processes running on localhost:
- **Main**: Bootstrap process
- **Loader** (port 5002): Orchestrator for phased-parallel startup
- **Logger** (port 5001): Centralized logging service
- **KWD** (port 5003): Keyword detection (wake word)
- **STT** (port 5004): Speech-to-text
- **LLM** (port 5005): Language model (Ollama)
- **TTS** (port 5006): Text-to-speech

## Current Status

### ✅ Completed Services

1. **Logger Service** (port 5001)
   - Application and dialog logging
   - Log rotation support
   - gRPC RPCs: WriteApp, NewDialog, WriteDialog
   - Health check implementation

2. **KWD Service** (port 5003)
   - OpenWakeWord integration with "Alexa" wake word
   - 0.6 confidence threshold, 1s cooldown
   - Real-time audio processing at 16kHz
   - gRPC RPCs: Events (stream), Enable, Disable
   - Health check implementation

### 🚧 Services To Build

3. **STT Service** (port 5004) - Whisper integration
4. **LLM Service** (port 5005) - Ollama bridge
5. **TTS Service** (port 5006) - Kokoro integration
6. **Loader Service** (port 5002) - Phased orchestration

## Prerequisites

- Python 3.11
- CUDA-capable GPU with 8GB+ VRAM
- PortAudio (for audio capture)
- Ollama (for LLM)

## Installation

```bash
# Create virtual environment with uv
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install grpcio grpcio-tools grpcio-health-checking
uv pip install aiofiles pyyaml nvidia-ml-py3 psutil 
uv pip install sounddevice numpy scipy
uv pip install onnxruntime openwakeword tqdm
```

## Configuration

Configuration is in `config/config.ini`:
- VRAM guardrail: 8000MB minimum
- Ports: 5001-5006 (localhost only)
- Wake word: "Alexa" (threshold 0.6)

## Usage

### Service Management

```bash
# Check status of all services
python manage_services.py status

# Start individual service
python manage_services.py start logger
python manage_services.py start kwd

# Stop service
python manage_services.py stop kwd

# Restart service
python manage_services.py restart logger

# Start all services
python manage_services.py start all

# Stop all services
python manage_services.py stop all
```

### Testing Services

#### Test Logger Service
```bash
# Start logger
python manage_services.py start logger

# View logs
cat logs/app.log
```

#### Test KWD Service
```bash
# Start KWD service
python manage_services.py start kwd

# Run test client
python tests/test_kwd.py

# Say "Alexa" to trigger wake word detection
```

## Service Logs

Each service writes to its own log file:
- `logger_service.log`
- `kwd_service.log`
- Application logs: `logs/app.log`
- Dialog logs: `logs/dialog_*.log`

## Development

### Project Structure
```
Alexa_W/
├── services/           # Service implementations
│   ├── logger/
│   ├── kwd/
│   ├── stt/
│   ├── llm/
│   ├── tts/
│   └── loader/
├── common/            # Shared modules
│   ├── base_service.py
│   ├── config_loader.py
│   ├── health_client.py
│   └── gpu_monitor.py
├── proto/             # gRPC definitions
│   ├── services.proto
│   └── generated files
├── config/            # Configuration
│   ├── config.ini
│   └── Modelfile
├── models/            # ML models
│   └── alexa_v0.1.onnx
├── logs/              # Log files
└── tests/             # Test scripts
```

### Adding a New Service

1. Create service directory under `services/`
2. Inherit from `BaseService` class
3. Implement service-specific RPCs
4. Add to `manage_services.py`
5. Test with health checks

## Troubleshooting

### Port Already in Use
```bash
# Find process using port
lsof -i :5001

# Kill process
kill -9 <PID>
```

### Audio Issues
- Ensure microphone permissions are granted
- Check audio device with `python -m sounddevice`
- Verify sample rate compatibility (16kHz required)

### GPU/VRAM Issues
- Check GPU with `nvidia-smi`
- Ensure 8GB+ VRAM available
- Monitor usage with `common/gpu_monitor.py`

## Performance Targets

- Wake detection latency: <200ms
- First token latency (LLM): <800ms  
- First audio latency (TTS): <150ms
- Dialog follow-up window: 4s

## Security

- All services bind to localhost (127.0.0.1) only
- No external network calls
- Config validation on startup
- VRAM guardrails enforced
