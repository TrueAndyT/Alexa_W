# Quick Start Guide

## Prerequisites
```bash
# Setup environment
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
```

## Start Everything
```bash
# Start the full system
python manage_services.py start loader

# Wait ~20 seconds for all services to load
# You'll hear "Hi, Master!" when ready
```

## Test It
Say "Alexa" and wait for the response, then speak your request.

## Stop Everything
```bash
python manage_services.py stop all
pkill -f ollama  # Stop Ollama server
```

## Check Status
```bash
python manage_services.py status
```

## View Logs
```bash
# Watch real-time logs
tail -f logs/loader_service.log

# Check for errors
grep ERROR logs/*.log
```

## Common Issues

### Nothing happens when I say "Alexa"
1. Check if services are running: `python manage_services.py status`
2. Check microphone: `python tests/test_kwd.py`
3. Check logs: `tail -50 logs/kwd_service.log`

### No response from assistant
1. Check Ollama: `ollama list`
2. Start Ollama if needed: `ollama serve &`
3. Check LLM logs: `tail -50 logs/llm_service.log`

### Audio issues
```bash
# List audio devices
python -m sounddevice

# Test microphone
python tests/test_1_kwd_interactive.py
```

### Out of GPU memory
```bash
# Check GPU usage
nvidia-smi

# Stop all services and restart
python manage_services.py stop all
python manage_services.py start loader
```

## Test Individual Components
```bash
# Test wake word detection
python tests/test_1_kwd_interactive.py

# Test speech recognition
python tests/test_2_stt_interactive.py

# Test language model
python tests/test_3_llm_interactive.py

# Test text-to-speech
python tests/test_4_tts_interactive.py
```

## Service Ports
- 5001: Logger
- 5002: Loader (orchestrator)
- 5003: KWD (wake word)
- 5004: STT (speech-to-text)
- 5005: LLM (language model)
- 5006: TTS (text-to-speech)

## Required Models
- **Wake Word**: alexa_v0.1.onnx (included)
- **STT**: Whisper small.en (auto-downloads)
- **LLM**: llama3.1:8b-instruct-q4_K_M (pull with `ollama pull`)
- **TTS**: Kokoro (auto-downloads)

## Tips
- All logs are in `logs/` directory
- All tests are in `tests/` directory
- Configuration in `config/config.ini`
- Use `uv` for all dependency management
- Services bind to 127.0.0.1 for security
