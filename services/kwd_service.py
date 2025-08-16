"""Keyword Detection (KWD) service using openWakeWord."""
import sys
import os
from pathlib import Path
import time
import threading
import queue
import numpy as np
import sounddevice as sd
from typing import Optional
import grpc

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2
from openwakeword.model import Model
from common.logger_client import LoggerClient
import logging
import random

# Suppress openwakeword logs
logging.getLogger('openwakeword').setLevel(logging.WARNING)


class KwdServicer(services_pb2_grpc.KwdServiceServicer):
    """Implementation of KWD service RPCs."""
    
    def __init__(self, wake_detector):
        """Initialize KWD servicer.
        
        Args:
            wake_detector: WakeDetector instance
        """
        self.wake_detector = wake_detector
    
    def Configure(self, request, context):
        """Configure KWD service."""
        if request.confidence_threshold > 0:
            self.wake_detector.threshold = request.confidence_threshold
        if request.cooldown_ms > 0:
            self.wake_detector.cooldown_ms = request.cooldown_ms
        if request.yes_phrases:
            self.wake_detector.yes_phrases = list(request.yes_phrases)
        
        return services_pb2.Status(
            success=True,
            message="KWD configured"
        )
    
    def Events(self, request, context):
        """Stream wake word detection events."""
        self.wake_detector.logger.info("Client connected to Events stream")
        
        try:
            while context.is_active():
                try:
                    # Wait for wake event with timeout
                    event = self.wake_detector.get_wake_event(timeout=1.0)
                    if event:
                        yield event
                except queue.Empty:
                    # No event, continue loop to check if context is still active
                    continue
                except Exception as e:
                    self.wake_detector.logger.error(f"Error in Events stream: {e}")
                    break
        finally:
            self.wake_detector.logger.info("Client disconnected from Events stream")
    
    def Start(self, request, context):
        """Start/enable wake word detection."""
        success = self.wake_detector.start()
        return services_pb2.Status(
            success=success,
            message="Wake detection started" if success else "Failed to start"
        )
    
    def Stop(self, request, context):
        """Stop/disable wake word detection."""
        success = self.wake_detector.stop()
        return services_pb2.Status(
            success=success,
            message="Wake detection stopped" if success else "Failed to stop"
        )


class WakeDetector:
    """Manages wake word detection using openWakeWord."""
    
    def __init__(self, config, logger):
        """Initialize wake detector.
        
        Args:
            config: ConfigLoader instance
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Configuration
        self.model_path = Path(config.get('kwd', 'model_path', 'models/alexa_v0.1.onnx'))
        self.threshold = config.get_float('kwd', 'confidence_threshold', 0.6)
        self.cooldown_ms = config.get_int('kwd', 'cooldown_ms', 1000)
        self.sample_rate = 16000  # openWakeWord requires 16kHz
        self.chunk_size = 1280  # 80ms chunks at 16kHz
        self.yes_phrases = config.get('kwd', 'yes_phrases', 'Yes?;Yes, Master?;Sup?;Yo').split(';')
        
        # Service stubs for internal dialog handling
        self.tts_stub = None
        self.stt_stub = None
        self.logger_stub = None
        
        # State
        self.enabled = False
        self.running = False
        self.last_detection_time = 0
        self.audio_thread = None
        self.event_queue = queue.Queue(maxsize=100)
        
        # Audio stream
        self.audio_stream = None
        self.audio_queue = queue.Queue()
        
        # OpenWakeWord model
        self.model = None
        self.wake_word_name = None
        
        # Dialog state
        self.in_dialog = False
        
    def initialize(self) -> bool:
        """Initialize wake word model and audio stream.
        
        Returns:
            True if successful
        """
        try:
            # Load openWakeWord model
            self.logger.info(f"Loading wake word model: {self.model_path}")
            
            if not self.model_path.exists():
                self.logger.error(f"Model file not found: {self.model_path}")
                return False
            
            # Initialize model with specific paths and inference framework
            self.model = Model(
                wakeword_models=[str(self.model_path)],
                inference_framework='onnx',  # Specify ONNX framework
                enable_speex_noise_suppression=False  # We'll handle our own audio processing
            )
            
            # Get wake word name from model
            self.wake_word_name = self.model_path.stem.replace('_v0.1', '').replace('_', ' ').title()
            self.logger.info(f"Wake word model loaded: {self.wake_word_name}")
            
            # Start audio processing thread
            self.running = True
            self.audio_thread = threading.Thread(target=self._audio_processing_loop, daemon=True)
            self.audio_thread.start()
            
            # Start audio capture
            self._start_audio_stream()
            
            # Connect to other services
            self._connect_services()
            
            # Enable detection by default
            self.enabled = True
            
            self.logger.info("Wake detector initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize wake detector: {e}")
            return False
    
    def _start_audio_stream(self):
        """Start audio input stream."""
        try:
            # Audio callback to capture microphone input
            def audio_callback(indata, frames, time_info, status):
                if status:
                    self.logger.warning(f"Audio status: {status}")
                
                # Convert to mono if needed and put in queue
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
            
            self.logger.info(f"Audio stream started: {self.sample_rate}Hz, {self.chunk_size} samples/chunk")
            
        except Exception as e:
            self.logger.error(f"Failed to start audio stream: {e}")
            raise
    
    def _audio_processing_loop(self):
        """Process audio chunks for wake word detection."""
        self.logger.info("Audio processing loop started")
        
        while self.running:
            try:
                # Get audio chunk from queue
                audio_chunk = self.audio_queue.get(timeout=1.0)
                
                if not self.enabled:
                    continue
                
                # Check cooldown
                current_time = time.time() * 1000  # ms
                if current_time - self.last_detection_time < self.cooldown_ms:
                    continue
                
                # Process with openWakeWord
                # Model expects int16 audio
                audio_int16 = (audio_chunk * 32767).astype(np.int16)
                
                # Get predictions
                prediction = self.model.predict(audio_int16)
                
                # Check for wake word detection
                for model_name, score in prediction.items():
                    if score >= self.threshold:
                        self.logger.info(f"Wake word detected: {model_name}, confidence: {score:.3f}")
                        
                        # Handle wake detection internally
                        self._handle_wake_detection(score, current_time)
                        
                        # Update last detection time
                        self.last_detection_time = current_time
                        
                        # Reset model state after detection
                        self.model.reset()
                        break
                
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error in audio processing loop: {e}")
                time.sleep(0.1)
        
        self.logger.info("Audio processing loop stopped")
    
    def get_wake_event(self, timeout: float = None) -> Optional[services_pb2.WakeEvent]:
        """Get next wake event from queue.
        
        Args:
            timeout: Maximum time to wait for event
            
        Returns:
            Wake event or None if timeout
        """
        try:
            return self.event_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def _connect_services(self):
        """Connect to other services for dialog handling."""
        try:
            import grpc
            
            # Connect to TTS service
            tts_channel = grpc.insecure_channel('localhost:5006')
            self.tts_stub = services_pb2_grpc.TtsServiceStub(tts_channel)
            
            # Connect to STT service
            stt_channel = grpc.insecure_channel('localhost:5004')
            self.stt_stub = services_pb2_grpc.SttServiceStub(stt_channel)
            
            # Connect to Logger service
            logger_channel = grpc.insecure_channel('localhost:5001')
            self.logger_stub = services_pb2_grpc.LoggerServiceStub(logger_channel)
            
            self.logger.info("Connected to TTS, STT, and Logger services")
        except Exception as e:
            self.logger.warning(f"Failed to connect to services: {e}")
    
    def _handle_wake_detection(self, confidence: float, timestamp_ms: int):
        """Handle wake word detection internally.
        
        Args:
            confidence: Detection confidence score
            timestamp_ms: Timestamp in milliseconds
        """
        try:
            # Log wake detection
            if self.logger._log_info:
                self.logger._log_info("wake_detected", f"Wake word detected with confidence {confidence:.3f}")
            
            # 1. Speak random confirmation via TTS
            if self.tts_stub:
                yes_phrase = random.choice(self.yes_phrases)
                self.logger.info(f"Speaking confirmation: {yes_phrase}")
                
                speak_response = self.tts_stub.Speak(services_pb2.SpeakRequest(
                    text=yes_phrase,
                    dialog_id="",  # Will be set by STT
                    voice="af_heart"
                ))
                
                if not speak_response.success:
                    self.logger.error(f"Failed to speak confirmation: {speak_response.message}")
            
            # 2. Create new dialog via Logger
            dialog_id = ""
            if self.logger_stub:
                dialog_response = self.logger_stub.NewDialog(services_pb2.NewDialogRequest(
                    timestamp_ms=timestamp_ms
                ))
                dialog_id = dialog_response.dialog_id
                self.logger.info(f"Created dialog: {dialog_id}")
            
            # 3. Start STT with dialog ID
            if self.stt_stub:
                start_response = self.stt_stub.Start(services_pb2.StartRequest(
                    dialog_id=dialog_id,
                    turn_number=1
                ))
                
                if start_response.success:
                    self.logger.info("STT started successfully")
                else:
                    self.logger.error(f"Failed to start STT: {start_response.message}")
            
            # 4. Disable KWD during dialog
            self.stop()
            self.in_dialog = True
            
            # Create and emit wake event for backward compatibility
            event = services_pb2.WakeEvent(
                confidence=float(confidence),
                timestamp_ms=int(timestamp_ms),
                wake_word=self.wake_word_name,
                dialog_id=dialog_id
            )
            
            # Add to event queue
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                self.logger.warning("Event queue full, dropping wake event")
            
        except Exception as e:
            self.logger.error(f"Error handling wake detection: {e}")
    
    def start(self) -> bool:
        """Start/enable wake word detection.
        
        Returns:
            True if successful
        """
        self.enabled = True
        self.in_dialog = False
        
        if self.logger._log_info:
            self.logger._log_info("kwd_started", "Wake word detection started")
        
        # Clear any pending events
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
        
        # Reset model state
        if self.model:
            self.model.reset()
        
        return True
    
    def stop(self) -> bool:
        """Stop/disable wake word detection.
        
        Returns:
            True if successful
        """
        self.enabled = False
        
        if self.logger._log_info:
            self.logger._log_info("kwd_stopped", "Wake word detection stopped")
        
        return True
    
    def cleanup(self):
        """Clean up resources."""
        self.running = False
        
        # Stop audio stream
        if self.audio_stream:
            self.audio_stream.stop()
            self.audio_stream.close()
            self.audio_stream = None
        
        # Wait for processing thread
        if self.audio_thread:
            self.audio_thread.join(timeout=2.0)
        
        self.logger.info("Wake detector cleaned up")


class KwdService(BaseService):
    """KWD service implementation."""
    
    def __init__(self):
        """Initialize KWD service."""
        super().__init__('kwd')
        self.wake_detector = None
        self.servicer = None
    
    def setup(self):
        """Setup KWD service."""
        try:
            # Initialize wake detector
            self.wake_detector = WakeDetector(self.config, self.logger)
            
            if not self.wake_detector.initialize():
                raise RuntimeError("Failed to initialize wake detector")
            
            # Add KWD servicer to server
            self.servicer = KwdServicer(self.wake_detector)
            services_pb2_grpc.add_KwdServiceServicer_to_server(
                self.servicer, self.server
            )
            
            self.logger.info("KWD service setup complete")
            
        except Exception as e:
            self.logger.error(f"Failed to setup KWD service: {e}")
            raise
    
    def cleanup(self):
        """Cleanup KWD service."""
        if self.wake_detector:
            self.wake_detector.cleanup()


if __name__ == "__main__":
    service = KwdService()
    service.start()
