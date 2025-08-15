"""Speech-to-Text (STT) service using Whisper."""
import sys
import os
from pathlib import Path
import time
import threading
import queue
import numpy as np
import sounddevice as sd
from typing import Optional, Dict, List
import grpc
import torch
import whisper
import webrtcvad
from collections import deque
import io

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2
import logging

# Suppress some whisper warnings
logging.getLogger('whisper').setLevel(logging.WARNING)


class SttServicer(services_pb2_grpc.SttServiceServicer):
    """Implementation of STT service RPCs."""
    
    def __init__(self, speech_recognizer):
        """Initialize STT servicer.
        
        Args:
            speech_recognizer: SpeechRecognizer instance
        """
        self.speech_recognizer = speech_recognizer
    
    def Start(self, request, context):
        """Start speech recognition for a dialog."""
        success = self.speech_recognizer.start_recognition(
            dialog_id=request.dialog_id,
            turn_number=request.turn_number
        )
        
        return services_pb2.Status(
            success=success,
            message="STT started" if success else "Failed to start STT"
        )
    
    def Stop(self, request, context):
        """Stop speech recognition."""
        success = self.speech_recognizer.stop_recognition(request.dialog_id)
        
        return services_pb2.Status(
            success=success,
            message="STT stopped" if success else "Failed to stop STT"
        )
    
    def Results(self, request, context):
        """Stream recognition results."""
        self.speech_recognizer.logger.info(
            f"Client connected to Results stream for dialog {request.dialog_id}"
        )
        
        try:
            while context.is_active():
                try:
                    # Get result from queue
                    result = self.speech_recognizer.get_result(
                        dialog_id=request.dialog_id,
                        timeout=1.0
                    )
                    if result:
                        yield result
                        # If this was a final result, stop streaming
                        if result.final:
                            break
                except queue.Empty:
                    continue
                except Exception as e:
                    self.speech_recognizer.logger.error(f"Error in Results stream: {e}")
                    break
        finally:
            self.speech_recognizer.logger.info(
                f"Client disconnected from Results stream for dialog {request.dialog_id}"
            )


class SpeechRecognizer:
    """Manages speech recognition using Whisper with VAD."""
    
    def __init__(self, config, logger):
        """Initialize speech recognizer.
        
        Args:
            config: ConfigLoader instance
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Configuration
        self.model_name = config.get('stt', 'model_name', 'small.en')
        self.language = config.get('stt', 'language', 'en')
        self.vad_silence_ms = config.get_int('stt', 'vad_silence_ms', 2000)
        self.sample_rate = 16000  # Whisper requires 16kHz
        self.chunk_duration_ms = 30  # VAD frame duration
        self.chunk_size = int(self.sample_rate * self.chunk_duration_ms / 1000)
        
        # State
        self.active_sessions = {}  # dialog_id -> session data
        self.result_queues = {}  # dialog_id -> queue
        self.audio_thread = None
        self.processing_thread = None
        self.running = False
        
        # Audio capture
        self.audio_stream = None
        self.audio_queue = queue.Queue()
        
        # Whisper model
        self.whisper_model = None
        
        # VAD
        self.vad = webrtcvad.Vad(2)  # Aggressiveness level 2
        
        # Audio buffer for each session
        self.audio_buffers = {}  # dialog_id -> deque of audio chunks
        
    def initialize(self) -> bool:
        """Initialize Whisper model and audio stream.
        
        Returns:
            True if successful
        """
        try:
            # Load Whisper model
            self.logger.info(f"Loading Whisper model: {self.model_name}")
            
            # Check if CUDA is available
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.logger.info(f"Using device: {device}")
            
            # Load model
            self.whisper_model = whisper.load_model(self.model_name, device=device)
            self.logger.info(f"Whisper model loaded: {self.model_name}")
            
            # Start processing threads
            self.running = True
            self.audio_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
            self.audio_thread.start()
            
            self.processing_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
            self.processing_thread.start()
            
            # Start audio stream
            self._start_audio_stream()
            
            self.logger.info("Speech recognizer initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize speech recognizer: {e}")
            return False
    
    def _start_audio_stream(self):
        """Start audio input stream."""
        try:
            # Audio callback
            def audio_callback(indata, frames, time_info, status):
                if status:
                    self.logger.warning(f"Audio status: {status}")
                
                # Convert to mono if needed
                audio = indata[:, 0] if len(indata.shape) > 1 else indata
                self.audio_queue.put(audio.copy())
            
            # Open audio stream
            self.audio_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='float32',
                blocksize=self.chunk_size,
                callback=audio_callback
            )
            self.audio_stream.start()
            
            self.logger.info(f"Audio stream started: {self.sample_rate}Hz")
            
        except Exception as e:
            self.logger.error(f"Failed to start audio stream: {e}")
            raise
    
    def _audio_capture_loop(self):
        """Capture audio and distribute to active sessions."""
        self.logger.info("Audio capture loop started")
        
        while self.running:
            try:
                # Get audio chunk
                audio_chunk = self.audio_queue.get(timeout=1.0)
                
                # Distribute to all active sessions
                for dialog_id in list(self.active_sessions.keys()):
                    if dialog_id in self.audio_buffers:
                        self.audio_buffers[dialog_id].append(audio_chunk)
                
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error in audio capture loop: {e}")
                time.sleep(0.1)
        
        self.logger.info("Audio capture loop stopped")
    
    def _audio_processing_loop(self):
        """Process audio with VAD and Whisper."""
        self.logger.info("Audio processing loop started")
        
        while self.running:
            try:
                # Process each active session
                for dialog_id in list(self.active_sessions.keys()):
                    if dialog_id not in self.audio_buffers:
                        continue
                    
                    session = self.active_sessions.get(dialog_id)
                    if not session:
                        continue
                    
                    # Check if we have enough audio
                    buffer = self.audio_buffers[dialog_id]
                    if len(buffer) < 10:  # Need at least 300ms of audio
                        continue
                    
                    # Process with VAD
                    self._process_audio_with_vad(dialog_id, session)
                
                time.sleep(0.1)  # Small delay between processing cycles
                
            except Exception as e:
                self.logger.error(f"Error in audio processing loop: {e}")
                time.sleep(0.1)
        
        self.logger.info("Audio processing loop stopped")
    
    def _process_audio_with_vad(self, dialog_id: str, session: Dict):
        """Process audio with VAD to detect speech end.
        
        Args:
            dialog_id: Dialog identifier
            session: Session data
        """
        buffer = self.audio_buffers[dialog_id]
        
        # Collect recent audio chunks
        audio_chunks = []
        while len(buffer) > 0 and len(audio_chunks) < 100:  # Max 3 seconds
            audio_chunks.append(buffer.popleft())
        
        if not audio_chunks:
            return
        
        # Concatenate audio
        audio = np.concatenate(audio_chunks)
        
        # Convert to int16 for VAD
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Check for speech with VAD
        speech_frames = 0
        silence_frames = 0
        frame_duration_ms = 30
        frame_size = int(self.sample_rate * frame_duration_ms / 1000)
        
        for i in range(0, len(audio_int16) - frame_size, frame_size):
            frame = audio_int16[i:i + frame_size].tobytes()
            if self.vad.is_speech(frame, self.sample_rate):
                speech_frames += 1
                silence_frames = 0
                session['last_speech_time'] = time.time()
            else:
                silence_frames += 1
        
        # Add to session audio buffer
        if 'audio_buffer' not in session:
            session['audio_buffer'] = []
        session['audio_buffer'].extend(audio_chunks)
        
        # Check if we should finalize
        current_time = time.time()
        last_speech = session.get('last_speech_time', current_time)
        silence_duration_ms = (current_time - last_speech) * 1000
        
        # If we have speech and then silence, finalize
        if (len(session['audio_buffer']) > 10 and 
            silence_duration_ms >= self.vad_silence_ms):
            
            self.logger.info(f"VAD finalization triggered for dialog {dialog_id}")
            self._finalize_recognition(dialog_id, session)
    
    def _finalize_recognition(self, dialog_id: str, session: Dict):
        """Finalize recognition and send result.
        
        Args:
            dialog_id: Dialog identifier
            session: Session data
        """
        try:
            # Get all audio from session
            audio_buffer = session.get('audio_buffer', [])
            if not audio_buffer:
                return
            
            # Concatenate all audio
            if isinstance(audio_buffer[0], np.ndarray):
                audio = np.concatenate(audio_buffer)
            else:
                # If audio_buffer contains lists
                audio = np.array(audio_buffer).flatten()
            
            # Clear buffer
            session['audio_buffer'] = []
            session['last_speech_time'] = time.time()
            
            # Transcribe with Whisper
            self.logger.info(f"Transcribing audio for dialog {dialog_id}")
            
            # Whisper expects float32 normalized audio
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            
            # Ensure audio is in range [-1, 1]
            audio = np.clip(audio, -1.0, 1.0)
            
            # Transcribe
            result = self.whisper_model.transcribe(
                audio,
                language=self.language,
                fp16=torch.cuda.is_available()
            )
            
            text = result.get('text', '').strip()
            
            if text:
                self.logger.info(f"Transcription for dialog {dialog_id}: {text}")
                
                # Create result
                stt_result = services_pb2.SttResult(
                    text=text,
                    final=True,
                    confidence=1.0,  # Whisper doesn't provide confidence
                    timestamp_ms=int(time.time() * 1000),
                    dialog_id=dialog_id
                )
                
                # Add to result queue
                if dialog_id in self.result_queues:
                    try:
                        self.result_queues[dialog_id].put_nowait(stt_result)
                    except queue.Full:
                        self.logger.warning(f"Result queue full for dialog {dialog_id}")
                
                # Mark session as finalized
                session['finalized'] = True
                
        except Exception as e:
            self.logger.error(f"Error in finalize recognition: {e}")
    
    def start_recognition(self, dialog_id: str, turn_number: int) -> bool:
        """Start recognition for a dialog.
        
        Args:
            dialog_id: Dialog identifier
            turn_number: Turn number in dialog
            
        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Starting recognition for dialog {dialog_id}, turn {turn_number}")
            
            # Create session
            self.active_sessions[dialog_id] = {
                'dialog_id': dialog_id,
                'turn_number': turn_number,
                'start_time': time.time(),
                'last_speech_time': time.time(),
                'audio_buffer': [],
                'finalized': False
            }
            
            # Create audio buffer
            self.audio_buffers[dialog_id] = deque(maxlen=1000)  # ~30 seconds max
            
            # Create result queue
            self.result_queues[dialog_id] = queue.Queue(maxsize=10)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start recognition: {e}")
            return False
    
    def stop_recognition(self, dialog_id: str) -> bool:
        """Stop recognition for a dialog.
        
        Args:
            dialog_id: Dialog identifier
            
        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Stopping recognition for dialog {dialog_id}")
            
            # Process any remaining audio
            if dialog_id in self.active_sessions:
                session = self.active_sessions[dialog_id]
                if not session.get('finalized', False):
                    self._finalize_recognition(dialog_id, session)
            
            # Clean up
            if dialog_id in self.active_sessions:
                del self.active_sessions[dialog_id]
            if dialog_id in self.audio_buffers:
                del self.audio_buffers[dialog_id]
            if dialog_id in self.result_queues:
                del self.result_queues[dialog_id]
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to stop recognition: {e}")
            return False
    
    def get_result(self, dialog_id: str, timeout: float = None) -> Optional[services_pb2.SttResult]:
        """Get next result for a dialog.
        
        Args:
            dialog_id: Dialog identifier
            timeout: Maximum time to wait
            
        Returns:
            STT result or None
        """
        if dialog_id not in self.result_queues:
            return None
        
        try:
            return self.result_queues[dialog_id].get(timeout=timeout)
        except queue.Empty:
            return None
    
    def cleanup(self):
        """Clean up resources."""
        self.running = False
        
        # Stop audio stream
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None
        
        # Wait for threads
        if self.audio_thread:
            self.audio_thread.join(timeout=2.0)
        if self.processing_thread:
            self.processing_thread.join(timeout=2.0)
        
        # Clear sessions
        self.active_sessions.clear()
        self.audio_buffers.clear()
        self.result_queues.clear()
        
        self.logger.info("Speech recognizer cleaned up")


class SttService(BaseService):
    """STT service implementation."""
    
    def __init__(self):
        """Initialize STT service."""
        super().__init__('stt')
        self.speech_recognizer = None
        self.servicer = None
    
    def setup(self):
        """Setup STT service."""
        try:
            # Initialize speech recognizer
            self.speech_recognizer = SpeechRecognizer(self.config, self.logger)
            
            if not self.speech_recognizer.initialize():
                raise RuntimeError("Failed to initialize speech recognizer")
            
            # Add STT servicer to server
            self.servicer = SttServicer(self.speech_recognizer)
            services_pb2_grpc.add_SttServiceServicer_to_server(
                self.servicer, self.server
            )
            
            self.logger.info("STT service setup complete")
            
        except Exception as e:
            self.logger.error(f"Failed to setup STT service: {e}")
            raise
    
    def cleanup(self):
        """Cleanup STT service."""
        if self.speech_recognizer:
            self.speech_recognizer.cleanup()


if __name__ == "__main__":
    service = SttService()
    service.start()
