"""Speech-to-Text (STT) service using Whisper."""
import sys
import os
import warnings
from pathlib import Path
import time
import threading
import queue
import numpy as np
import sounddevice as sd
from typing import Optional, Dict, List
import grpc
# Suppress pkg_resources deprecation warning
warnings.filterwarnings("ignore", category=UserWarning)

# Use Faster-Whisper instead of OpenAI Whisper
from faster_whisper import WhisperModel

from collections import deque
import io

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2
from common.logger_client import LoggerClient
import logging

# Suppress some warnings
logging.getLogger('faster_whisper').setLevel(logging.WARNING)


class SttServicer(services_pb2_grpc.SttServiceServicer):
    """Implementation of STT service RPCs."""
    
    def __init__(self, speech_recognizer):
        """Initialize STT servicer.
        
        Args:
            speech_recognizer: SpeechRecognizer instance
        """
        self.speech_recognizer = speech_recognizer
    
    def Configure(self, request, context):
        """Configure STT service."""
        if request.language:
            self.speech_recognizer.language = request.language
        if request.vad_silence_ms > 0:
            self.speech_recognizer.vad_silence_ms = request.vad_silence_ms
        if request.aec_enabled is not None:
            self.speech_recognizer.aec_enabled = request.aec_enabled
        
        return services_pb2.Status(
            success=True,
            message="STT configured"
        )
    
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
        self.chunk_duration_ms = 30  # Frame duration for audio processing
        self.chunk_size = int(self.sample_rate * self.chunk_duration_ms / 1000)
        
        # Faster-Whisper configuration
        self.beam_size = 1  # Faster with less memory than beam_size > 1
        self.compute_type = "int8_float16"  # Efficient compute type for CUDA
        
        # State
        self.active_sessions = {}  # dialog_id -> session data
        self.result_queues = {}  # dialog_id -> queue
        self.audio_thread = None
        self.processing_thread = None
        self.running = False
        
        # Audio capture
        self.audio_stream = None
        self.audio_queue = queue.Queue()
        
        # Faster-Whisper model
        self.whisper_model = None
        
        # Audio buffer for each session
        self.audio_buffers = {}  # dialog_id -> deque of audio chunks
        
        # Dialog management (STT owns the dialog loop)
        self.current_dialog_id = None
        self.dialog_turn = 0
        self.follow_up_timer = None
        self.follow_up_timeout = 4.0  # 4 seconds
        
        # Service stubs for dialog orchestration
        self.llm_stub = None
        self.tts_stub = None
        self.kwd_stub = None
        self.logger_stub = None
        
        # AEC enabled flag
        self.aec_enabled = config.get('stt', 'aec_enabled', fallback=True)
        
    def initialize(self) -> bool:
        """Initialize Faster-Whisper model and audio stream.
        
        Returns:
            True if successful
        """
        try:
            # Load Faster-Whisper model
            self.logger.info(f"Loading Faster-Whisper model: {self.model_name}")
            
            # Try CUDA first, fall back to CPU if needed
            device = "cuda"
            try:
                # Try to load model on CUDA with efficient compute type
                self.whisper_model = WhisperModel(
                    self.model_name,
                    device=device,
                    compute_type=self.compute_type,
                    cpu_threads=4,
                    num_workers=1
                )
                self.logger.info(f"Faster-Whisper model loaded on CUDA: {self.model_name}")
                
                # Log device info if available
                try:
                    import torch
                    if torch.cuda.is_available():
                        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
                        self.logger.info(f"GPU: {torch.cuda.get_device_name(0)} with {vram_gb:.1f}GB VRAM")
                except:
                    pass
                    
            except (RuntimeError, Exception) as e:
                if "out of memory" in str(e).lower() or "CUDA" in str(e):
                    self.logger.warning(f"CUDA failed, falling back to CPU: {e}")
                    device = "cpu"
                    # For CPU, use float32 compute type
                    self.whisper_model = WhisperModel(
                        self.model_name,
                        device=device,
                        compute_type="float32",
                        cpu_threads=4,
                        num_workers=1
                    )
                    self.logger.info(f"Faster-Whisper model loaded on CPU: {self.model_name}")
                else:
                    raise
            
            # Start processing threads
            self.running = True
            self.audio_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
            self.audio_thread.start()
            
            self.processing_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
            self.processing_thread.start()
            
            # Start audio stream
            self._start_audio_stream()
            
            # Connect to other services for dialog orchestration
            self._connect_services()
            
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
        """Process audio buffer and check if we should finalize.
        
        Note: Faster-Whisper has built-in VAD, but we still track silence
        duration to know when to finalize the recognition.
        
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
        
        # Add to session audio buffer
        if 'audio_buffer' not in session:
            session['audio_buffer'] = []
        session['audio_buffer'].extend(audio_chunks)
        
        # Simple silence detection based on audio amplitude
        audio = np.concatenate(audio_chunks)
        audio_rms = np.sqrt(np.mean(audio**2))
        
        # If audio is quiet, consider it silence
        silence_threshold = 0.01  # Adjust based on testing
        if audio_rms < silence_threshold:
            # Update silence duration
            current_time = time.time()
            if 'last_speech_time' not in session:
                session['last_speech_time'] = current_time
        else:
            # Reset speech time if we detect sound
            session['last_speech_time'] = time.time()
            self.logger.debug(f"Speech detected for {dialog_id}, RMS: {audio_rms:.4f}")
        
        # Check if we should finalize
        current_time = time.time()
        last_speech = session.get('last_speech_time', current_time)
        silence_duration_ms = (current_time - last_speech) * 1000
        
        # Log silence duration periodically
        if len(session['audio_buffer']) > 10 and int(silence_duration_ms) % 500 < 100:
            self.logger.info(f"Dialog {dialog_id}: {len(session['audio_buffer'])} audio chunks buffered, silence for {silence_duration_ms:.0f}ms (need {self.vad_silence_ms}ms)")
        
        # If we have speech and then silence, finalize
        if (len(session['audio_buffer']) > 10 and 
            silence_duration_ms >= self.vad_silence_ms):
            
            self.logger.info(f"Finalization triggered for dialog {dialog_id} after {silence_duration_ms:.0f}ms of silence")
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
                self.logger.warning(f"No audio buffer for dialog {dialog_id}, sending empty result")
                # Send empty result so the dialog can continue
                stt_result = services_pb2.SttResult(
                    text="",
                    final=True,
                    confidence=0.0,
                    timestamp_ms=int(time.time() * 1000),
                    dialog_id=dialog_id
                )
                if dialog_id in self.result_queues:
                    self.result_queues[dialog_id].put_nowait(stt_result)
                session['finalized'] = True
                return
            
            # Concatenate all audio
            if isinstance(audio_buffer[0], np.ndarray):
                audio = np.concatenate(audio_buffer)
            else:
                # If audio_buffer contains lists
                audio = np.array(audio_buffer).flatten()
            
            # Log audio info
            audio_duration = len(audio) / self.sample_rate
            self.logger.info(f"Finalizing recognition for dialog {dialog_id}: {audio_duration:.2f}s of audio")
            
            # Clear buffer
            session['audio_buffer'] = []
            session['last_speech_time'] = time.time()
            
            # Check if we have enough audio (at least 0.5 seconds)
            if audio_duration < 0.5:
                self.logger.warning(f"Audio too short ({audio_duration:.2f}s), sending empty result")
                stt_result = services_pb2.SttResult(
                    text="",
                    final=True,
                    confidence=0.0,
                    timestamp_ms=int(time.time() * 1000),
                    dialog_id=dialog_id
                )
                if dialog_id in self.result_queues:
                    self.result_queues[dialog_id].put_nowait(stt_result)
                session['finalized'] = True
                return
            
            # Transcribe with Faster-Whisper
            self.logger.info(f"Transcribing {audio_duration:.2f}s of audio for dialog {dialog_id}")
            
            # Faster-Whisper expects int16 audio for numpy arrays
            if audio.dtype != np.int16:
                # Convert float32 [-1, 1] to int16
                audio = np.clip(audio, -1.0, 1.0)
                audio = (audio * 32767).astype(np.int16)
            
            # Transcribe with built-in VAD and efficient settings
            segments, info = self.whisper_model.transcribe(
                audio,
                beam_size=self.beam_size,
                language=self.language,
                vad_filter=True,  # Use built-in Silero VAD
                vad_parameters=dict(
                    min_silence_duration_ms=500,  # Minimum silence to split segments
                    speech_pad_ms=400,  # Padding around speech
                    threshold=0.5  # VAD threshold
                ),
                word_timestamps=False,  # Disable to save compute
                condition_on_previous_text=False,  # Disable for better real-time performance
                temperature=0  # Deterministic decoding
            )
            
            # Collect all text from segments
            text_parts = []
            for segment in segments:
                if segment.text:
                    text_parts.append(segment.text.strip())
                    self.logger.debug(f"Segment: {segment.text.strip()}")
            
            text = ' '.join(text_parts).strip()
            
            # Log detection info
            self.logger.info(f"Detected language: {info.language} with probability {info.language_probability:.2f}")
            
            # Always send a result, even if empty
            self.logger.info(f"Transcription for dialog {dialog_id}: '{text}' (empty={not text})")
            
            # Log to centralized logger
            if hasattr(self.logger, '_log_info'):
                self.logger._log_info("stt_final_text", f"Dialog {dialog_id}: '{text}'")
            
            # Create result
            stt_result = services_pb2.SttResult(
                text=text if text else "",
                final=True,
                confidence=1.0 if text else 0.0,
                timestamp_ms=int(time.time() * 1000),
                dialog_id=dialog_id
            )
            
            # Add to result queue
            if dialog_id in self.result_queues:
                try:
                    self.result_queues[dialog_id].put_nowait(stt_result)
                    self.logger.info(f"Result queued for dialog {dialog_id}")
                except queue.Full:
                    self.logger.warning(f"Result queue full for dialog {dialog_id}")
            else:
                self.logger.error(f"No result queue for dialog {dialog_id}")
            
            # Mark session as finalized
            session['finalized'] = True
            
            # Handle dialog orchestration (STT owns the dialog loop)
            if text and dialog_id == self.current_dialog_id:
                # Process user input through LLM and TTS
                threading.Thread(target=self._process_user_input, args=(dialog_id, text), daemon=True).start()
                
        except Exception as e:
            self.logger.error(f"Error in finalize recognition: {e}")
            import traceback
            traceback.print_exc()
    
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
            
            # Log STT started
            if hasattr(self.logger, '_log_info'):
                self.logger._log_info("stt_started", f"STT started for dialog {dialog_id}, turn {turn_number}")
            
            # Update dialog state
            self.current_dialog_id = dialog_id
            self.dialog_turn = turn_number
            
            # Log dialog started if first turn
            if turn_number == 1 and hasattr(self.logger, '_log_info'):
                self.logger._log_info("dialog_started", f"Dialog {dialog_id} started")
            
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
            
            # Log STT stopped
            if hasattr(self.logger, '_log_info'):
                self.logger._log_info("stt_stopped", f"STT stopped for dialog {dialog_id}")
            
            # Process any remaining audio
            if dialog_id in self.active_sessions:
                session = self.active_sessions[dialog_id]
                if not session.get('finalized', False):
                    # Force collection of all buffered audio before finalizing
                    if dialog_id in self.audio_buffers:
                        buffer = self.audio_buffers[dialog_id]
                        self.logger.info(f"Collecting {len(buffer)} buffered audio chunks for forced finalization")
                        
                        # Move all buffered audio to session
                        if 'audio_buffer' not in session:
                            session['audio_buffer'] = []
                        
                        while buffer:
                            session['audio_buffer'].append(buffer.popleft())
                    
                    # Now finalize with all collected audio
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
    
    def _connect_services(self):
        """Connect to other services for dialog orchestration."""
        try:
            import grpc
            
            # Connect to LLM service
            llm_channel = grpc.insecure_channel('localhost:5005')
            self.llm_stub = services_pb2_grpc.LlmServiceStub(llm_channel)
            
            # Connect to TTS service
            tts_channel = grpc.insecure_channel('localhost:5006')
            self.tts_stub = services_pb2_grpc.TtsServiceStub(tts_channel)
            
            # Connect to KWD service
            kwd_channel = grpc.insecure_channel('localhost:5003')
            self.kwd_stub = services_pb2_grpc.KwdServiceStub(kwd_channel)
            
            # Connect to Logger service
            logger_channel = grpc.insecure_channel('localhost:5001')
            self.logger_stub = services_pb2_grpc.LoggerServiceStub(logger_channel)
            
            self.logger.info("Connected to LLM, TTS, KWD, and Logger services")
        except Exception as e:
            self.logger.warning(f"Failed to connect to services: {e}")
    
    def _process_user_input(self, dialog_id: str, user_text: str):
        """Process user input through LLM and TTS.
        
        This is where STT owns the dialog loop.
        """
        try:
            # Cancel any existing follow-up timer
            if self.follow_up_timer:
                self.follow_up_timer.cancel()
                self.follow_up_timer = None
            
            # Log dialog turn
            if hasattr(self.logger, '_log_info'):
                self.logger._log_info("dialog_turn", f"Turn {self.dialog_turn} for dialog {dialog_id}")
            
            # Call LLM.Complete and stream to TTS
            if self.llm_stub and self.tts_stub:
                complete_request = services_pb2.CompleteRequest(
                    text=user_text,
                    dialog_id=dialog_id,
                    turn_number=self.dialog_turn,
                    conversation_history=""
                )
                
                # Stream LLM response to TTS
                def generate_tts_chunks():
                    try:
                        if hasattr(self.logger, '_log_info'):
                            self.logger._log_info("llm_stream_start", f"Starting LLM stream for dialog {dialog_id}")
                        
                        for llm_chunk in self.llm_stub.Complete(complete_request):
                            if llm_chunk.text:
                                yield services_pb2.LlmChunk(
                                    text=llm_chunk.text,
                                    eot=False,
                                    dialog_id=dialog_id
                                )
                            if llm_chunk.eot:
                                yield services_pb2.LlmChunk(
                                    text="",
                                    eot=True,
                                    dialog_id=dialog_id
                                )
                                if hasattr(self.logger, '_log_info'):
                                    self.logger._log_info("llm_stream_end", f"LLM stream ended for dialog {dialog_id}")
                                break
                    except Exception as e:
                        self.logger.error(f"LLM streaming error: {e}")
                        if hasattr(self.logger, '_log_info'):
                            self.logger._log_info("llm_error", f"LLM error for dialog {dialog_id}: {e}")
                
                # Send to TTS
                tts_response = self.tts_stub.SpeakStream(generate_tts_chunks())
                
                if tts_response.success:
                    # Subscribe to playback events
                    self._monitor_playback_and_start_timer(dialog_id)
                else:
                    self.logger.error(f"TTS streaming failed: {tts_response.message}")
                    # Try error recovery
                    self._speak_error_and_continue(dialog_id, "Sorry, I had trouble speaking.")
            
        except Exception as e:
            self.logger.error(f"Error processing user input: {e}")
            self._speak_error_and_continue(dialog_id, "Sorry, something went wrong.")
    
    def _monitor_playback_and_start_timer(self, dialog_id: str):
        """Monitor TTS playback and start follow-up timer."""
        try:
            if self.tts_stub:
                dialog_ref = services_pb2.DialogRef(
                    dialog_id=dialog_id,
                    turn_number=self.dialog_turn
                )
                
                # Monitor playback events
                for event in self.tts_stub.PlaybackEvents(dialog_ref):
                    if event.event_type == "finished":
                        # Start 4-second follow-up timer
                        if hasattr(self.logger, '_log_info'):
                            self.logger._log_info("dialog_followup_start", f"Starting 4s follow-up timer for dialog {dialog_id}")
                        
                        self.follow_up_timer = threading.Timer(
                            self.follow_up_timeout,
                            self._on_follow_up_timeout,
                            args=(dialog_id,)
                        )
                        self.follow_up_timer.start()
                        break
        except Exception as e:
            self.logger.error(f"Error monitoring playback: {e}")
    
    def _speak_error_and_continue(self, dialog_id: str, error_text: str):
        """Speak error message and continue dialog."""
        try:
            if self.tts_stub:
                self.tts_stub.Speak(services_pb2.SpeakRequest(
                    text=error_text,
                    dialog_id=dialog_id,
                    voice="af_heart"
                ))
            
            # Start follow-up timer anyway
            self.follow_up_timer = threading.Timer(
                self.follow_up_timeout,
                self._on_follow_up_timeout,
                args=(dialog_id,)
            )
            self.follow_up_timer.start()
        except Exception as e:
            self.logger.error(f"Failed to speak error: {e}")
            # Force end dialog if we can't even speak errors
            self._end_dialog(dialog_id)
    
    def _on_follow_up_timeout(self, dialog_id: str):
        """Handle follow-up timeout - end dialog."""
        if hasattr(self.logger, '_log_info'):
            self.logger._log_info("dialog_ended", f"Dialog {dialog_id} ended after follow-up timeout")
        
        self._end_dialog(dialog_id)
    
    def _end_dialog(self, dialog_id: str):
        """End the current dialog and re-enable KWD."""
        try:
            # Stop STT for this dialog
            self.stop_recognition(dialog_id)
            
            # Re-enable KWD
            if self.kwd_stub:
                self.kwd_stub.Start(services_pb2.Empty())
                self.logger.info("KWD re-enabled after dialog end")
            
            # Clear dialog state
            self.current_dialog_id = None
            self.dialog_turn = 0
            
            # Cancel timer if still running
            if self.follow_up_timer:
                self.follow_up_timer.cancel()
                self.follow_up_timer = None
            
        except Exception as e:
            self.logger.error(f"Error ending dialog: {e}")
    
    def cleanup(self):
        """Clean up resources."""
        self.running = False
        
        # Cancel follow-up timer
        if self.follow_up_timer:
            self.follow_up_timer.cancel()
            self.follow_up_timer = None
        
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
        # Enable debug logging for STT
        self.logger.setLevel(logging.DEBUG)
    
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
