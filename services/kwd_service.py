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
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2
from openwakeword.model import Model
import logging

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
    
    def Enable(self, request, context):
        """Enable wake word detection."""
        success = self.wake_detector.enable()
        return services_pb2.Status(
            success=success,
            message="Wake detection enabled" if success else "Failed to enable"
        )
    
    def Disable(self, request, context):
        """Disable wake word detection."""
        success = self.wake_detector.disable()
        return services_pb2.Status(
            success=success,
            message="Wake detection disabled" if success else "Failed to disable"
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
                        
                        # Create wake event
                        event = services_pb2.WakeEvent(
                            confidence=float(score),
                            timestamp_ms=int(current_time),
                            wake_word=self.wake_word_name
                        )
                        
                        # Add to event queue
                        try:
                            self.event_queue.put_nowait(event)
                        except queue.Full:
                            self.logger.warning("Event queue full, dropping wake event")
                        
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
    
    def enable(self) -> bool:
        """Enable wake word detection.
        
        Returns:
            True if successful
        """
        self.enabled = True
        self.logger.info("Wake detection enabled")
        
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
    
    def disable(self) -> bool:
        """Disable wake word detection.
        
        Returns:
            True if successful
        """
        self.enabled = False
        self.logger.info("Wake detection disabled")
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
