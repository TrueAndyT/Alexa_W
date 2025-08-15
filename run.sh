#!/bin/bash
# Quick run script for the voice assistant system

echo "===========================================" 
echo "Alexa Voice Assistant System"
echo "==========================================="

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Virtual environment not found!"
    echo "Please run: uv venv"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Check if proto files exist
if [ ! -f "proto/services_pb2.py" ]; then
    echo "Generating proto files..."
    python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. proto/services.proto
fi

# Check if Ollama is running
if ! command -v ollama &> /dev/null; then
    echo "Warning: Ollama not found - LLM service will fail"
    echo "Install from: https://ollama.ai"
else
    if ! ollama list &> /dev/null; then
        echo "Starting Ollama service..."
        ollama serve &
        sleep 2
    fi
    
    # Check if model is available
    if ! ollama list | grep -q "llama3.1:8b"; then
        echo "Pulling llama3.1:8b model (this may take a while)..."
        ollama pull llama3.1:8b
    fi
fi

# Start the system
echo ""
echo "Starting voice assistant system..."
echo "Press Ctrl+C to stop"
echo ""

python main.py
