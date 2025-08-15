#!/usr/bin/env python3
"""TTS Service with voice synthesis using Kokoro."""
import sys
import time
import grpc
import queue
import threading
import numpy as np
from pathlib import Path
from typing import Optional, Iterator, Generator
import logging
from concurrent import futures
import sounddevice as sd
import torch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2

try:
    from kokoro import KPipeline
    KOKORO_AVAILABLE = True
except ImportError:
    KOKORO_AVAILABLE = False
    logging.warning("Kokoro not available, using mock TTS")


class KokoroTTSEngine:
    """Kokoro TTS engine wrapper."""
    
    def __init__(self, voice: str = "af_heart", device: str = "cuda", lang_code: str = "a"):
        """
        Initialize Kokoro TTS engine.
        
        Args:
            voice: Voice to use (af_heart, af_bella, af_nicole, af_sarah, am_adam, am_michael)
            device: Device to run on (cuda/cpu)
            lang_code: Language code ('a' for American English, 'b' for British English, etc.)
        """
        self.voice = voice
        self.device = device
        self.lang_code = lang_code
        self.sample_rate = 24000
        self.logger = logging.getLogger("KokoroTTS")
        
        if KOKORO_AVAILABLE:
            try:
                # Initialize Kokoro pipeline
                self.logger.info(f"Loading Kokoro model for voice: {voice}, lang: {lang_code}")
                self.pipeline = KPipeline(lang_code=lang_code)
                self.logger.info("Kokoro model loaded successfully")
            except Exception as e:
                self.logger.error(f"Failed to load Kokoro model: {e}")
                self.pipeline = None
        else:
            self.pipeline = None
            self.logger.warning("Using mock TTS implementation")
        
    def synthesize(self, text: str) -> np.ndarray:
        """Synthesize text to audio using Kokoro.
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio array at 24kHz sample rate
        """
        if self.pipeline is None:
            # Fallback to mock implementation
            return self._mock_synthesize(text)
            
        try:
            # Use Kokoro to generate audio
            self.logger.info(f"Synthesizing {len(text)} chars with voice: {self.voice}")
            
            # Generate audio using Kokoro
            # The generator yields tuples of (grapheme_segments, phoneme_segments, audio_chunk)
            audio_chunks = []
            for i, (gs, ps, audio) in enumerate(self.pipeline(text, voice=self.voice)):
                audio_chunks.append(audio)
                self.logger.debug(f"Generated chunk {i}: {len(audio)} samples")
            
            # Concatenate all audio chunks
            if audio_chunks:
                full_audio = np.concatenate(audio_chunks)
                self.logger.info(f"Generated {len(full_audio)/self.sample_rate:.2f}s of audio")
                return full_audio.astype(np.float32)
            else:
                self.logger.warning("No audio generated")
                return np.array([], dtype=np.float32)
                
        except Exception as e:
            self.logger.error(f"Kokoro synthesis error: {e}")
            # Fallback to mock implementation
            return self._mock_synthesize(text)
    
    def synthesize_streaming(self, text: str) -> Generator[np.ndarray, None, None]:
        """Stream synthesized audio chunks.
        
        Args:
            text: Text to synthesize
            
        Yields:
            Audio chunks at 24kHz sample rate
        """
        if self.pipeline is None:
            # Fallback to mock implementation
            yield self._mock_synthesize(text)
            return
            
        try:
            # Stream audio using Kokoro
            self.logger.info(f"Streaming synthesis for {len(text)} chars with voice: {self.voice}")
            
            for i, (gs, ps, audio) in enumerate(self.pipeline(text, voice=self.voice)):
                self.logger.debug(f"Streaming chunk {i}: {len(audio)} samples")
                yield audio.astype(np.float32)
                
        except Exception as e:
            self.logger.error(f"Kokoro streaming error: {e}")
            # Fallback to mock implementation
            yield self._mock_synthesize(text)
    
    def _mock_synthesize(self, text: str) -> np.ndarray:
        """Mock synthesis for when Kokoro is not available."""
        # Mock implementation: generate simple audio
        duration = max(0.5, len(text) * 0.05)  # Rough estimate
        t = np.linspace(0, duration, int(self.sample_rate * duration))
        
        # Generate a simple modulated tone
        frequency = 440  # A4 note
        audio = np.sin(2 * np.pi * frequency * t) * 0.3
        
        # Add some variation
        modulation = np.sin(2 * np.pi * 2 * t) * 0.1
        audio = audio * (1 + modulation)
        
        self.logger.info(f"Mock synthesized {len(text)} chars to {duration:.1f}s audio")
        return audio.astype(np.float32)


class AudioStreamQueue:
    """Queue for managing audio streaming with underrun protection."""
    
    def __init__(self, sample_rate: int = 24000, buffer_size_ms: int = 100):
        self.sample_rate = sample_rate
        self.buffer_size = int(sample_rate * buffer_size_ms / 1000)
        self.queue = queue.Queue()
        self.playing = False
        self.stream = None
        self.logger = logging.getLogger("AudioStreamQueue")
        
    def start_playback(self):
        """Start audio playback stream."""
        if self.playing:
            return
            
        try:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                callback=self._audio_callback,
                blocksize=self.buffer_size
            )
            self.stream.start()
            self.playing = True
            self.logger.info("Audio playback started")
        except Exception as e:
            self.logger.error(f"Failed to start audio playback: {e}")
            raise
            
    def stop_playback(self):
        """Stop audio playback stream."""
        if not self.playing:
            return
            
        self.playing = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.logger.info("Audio playback stopped")
        
    def add_audio(self, audio: np.ndarray):
        """Add audio chunk to queue."""
        self.queue.put(audio)
        
    def _audio_callback(self, outdata, frames, time_info, status):
        """Audio stream callback."""
        if status:
            self.logger.warning(f"Audio callback status: {status}")
            
        # Fill output buffer
        output = np.zeros(frames, dtype=np.float32)
        filled = 0
        
        while filled < frames and not self.queue.empty():
            try:
                chunk = self.queue.get_nowait()
                remaining = frames - filled
                to_copy = min(len(chunk), remaining)
                output[filled:filled + to_copy] = chunk[:to_copy]
                filled += to_copy
                
                # Put back remaining audio if chunk was larger
                if to_copy < len(chunk):
                    self.queue.put(chunk[to_copy:])
                    
            except queue.Empty:
                break
                
        outdata[:] = output.reshape(-1, 1)


class TTSServicer(services_pb2_grpc.TtsServiceServicer):
    """gRPC service implementation for TTS."""
    
    def __init__(self, config, logger_stub):
        self.config = config
        self.logger_stub = logger_stub
        self.logger = logging.getLogger("TTSService")
        
        # Get config values
        self.voice = config.get('tts', 'voice', fallback='af_heart')
        self.device = config.get('tts', 'device', fallback='cuda')
        self.sample_rate = config.get_int('tts', 'sample_rate', fallback=24000)
        self.buffer_size_ms = config.get_int('tts', 'buffer_size_ms', fallback=100)
        
        # TTS engine
        self.tts_engine = KokoroTTSEngine(voice=self.voice, device=self.device)
        
        # Audio stream queue
        self.audio_queue = AudioStreamQueue(
            sample_rate=self.sample_rate,
            buffer_size_ms=self.buffer_size_ms
        )
        
        # Playback events
        self.playback_events = {}
        self.events_lock = threading.Lock()
        
        self.logger.info(f"TTS service initialized with voice: {self.voice}")
        
    def Speak(self, request, context):
        """Synthesize and play text (unary call)."""
        text = request.text
        dialog_id = request.dialog_id
        
        self.logger.info(f"Speak request for dialog {dialog_id}: {text[:50]}...")
        
        try:
            # Log to app log
            self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                service="tts",
                event="speak_start",
                message=f"Speaking: {text[:100]}",
                level="INFO",
                timestamp_ms=int(time.time() * 1000)
            ))
            
            # Synthesize audio
            start_time = time.time()
            audio = self.tts_engine.synthesize(text)
            synthesis_time = (time.time() - start_time) * 1000
            
            # Play audio
            self.audio_queue.start_playback()
            self.audio_queue.add_audio(audio)
            
            # Add playback complete event
            with self.events_lock:
                if dialog_id not in self.playback_events:
                    self.playback_events[dialog_id] = queue.Queue()
                    
                # Add events
                self.playback_events[dialog_id].put(services_pb2.PlaybackEvent(
                    event_type="started",
                    timestamp_ms=int(time.time() * 1000),
                    chunk_number=0,
                    dialog_id=dialog_id
                ))
                
                # Simulate playback duration
                duration_ms = len(audio) / self.sample_rate * 1000
                
                # Schedule finished event
                def finish_event():
                    time.sleep(duration_ms / 1000)
                    self.playback_events[dialog_id].put(services_pb2.PlaybackEvent(
                        event_type="finished",
                        timestamp_ms=int(time.time() * 1000),
                        chunk_number=1,
                        dialog_id=dialog_id
                    ))
                    
                threading.Thread(target=finish_event, daemon=True).start()
            
            return services_pb2.SpeakResponse(
                success=True,
                message="Audio played successfully",
                duration_ms=duration_ms
            )
            
        except Exception as e:
            self.logger.error(f"Error in Speak: {e}")
            return services_pb2.SpeakResponse(
                success=False,
                message=str(e),
                duration_ms=0
            )
            
    def SpeakStream(self, request_iterator, context):
        """Stream text chunks for synthesis (client-streaming)."""
        dialog_id = None
        full_text = []
        chunk_count = 0
        first_audio_time = None
        start_time = time.time()
        
        try:
            self.audio_queue.start_playback()
            
            for chunk in request_iterator:
                if dialog_id is None:
                    dialog_id = chunk.dialog_id
                    
                if chunk.text:
                    full_text.append(chunk.text)
                    chunk_count += 1
                    
                    # Synthesize and queue audio chunk
                    audio = self.tts_engine.synthesize(chunk.text)
                    self.audio_queue.add_audio(audio)
                    
                    # Track first audio time
                    if first_audio_time is None:
                        first_audio_time = time.time()
                        latency = (first_audio_time - start_time) * 1000
                        self.logger.info(f"First audio latency: {latency:.0f}ms")
                        
                    # Add chunk played event
                    with self.events_lock:
                        if dialog_id not in self.playback_events:
                            self.playback_events[dialog_id] = queue.Queue()
                            
                        self.playback_events[dialog_id].put(services_pb2.PlaybackEvent(
                            event_type="chunk_played",
                            timestamp_ms=int(time.time() * 1000),
                            chunk_number=chunk_count,
                            dialog_id=dialog_id
                        ))
                    
                if chunk.eot:
                    break
                    
            # Add finished event
            with self.events_lock:
                if dialog_id and dialog_id in self.playback_events:
                    self.playback_events[dialog_id].put(services_pb2.PlaybackEvent(
                        event_type="finished",
                        timestamp_ms=int(time.time() * 1000),
                        chunk_number=chunk_count,
                        dialog_id=dialog_id
                    ))
                    
            total_duration = (time.time() - start_time) * 1000
            
            return services_pb2.SpeakResponse(
                success=True,
                message=f"Streamed {chunk_count} chunks",
                duration_ms=total_duration
            )
            
        except Exception as e:
            self.logger.error(f"Error in SpeakStream: {e}")
            return services_pb2.SpeakResponse(
                success=False,
                message=str(e),
                duration_ms=0
            )
            
    def PlaybackEvents(self, request, context):
        """Stream playback events (server-streaming)."""
        dialog_id = request.dialog_id
        
        self.logger.info(f"PlaybackEvents subscription for dialog {dialog_id}")
        
        # Create event queue if not exists
        with self.events_lock:
            if dialog_id not in self.playback_events:
                self.playback_events[dialog_id] = queue.Queue()
                
        event_queue = self.playback_events[dialog_id]
        
        try:
            while context.is_active():
                try:
                    # Wait for event with timeout
                    event = event_queue.get(timeout=1.0)
                    yield event
                    
                    # Clean up if finished
                    if event.event_type == "finished":
                        break
                        
                except queue.Empty:
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in PlaybackEvents: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
            
        finally:
            # Clean up event queue
            with self.events_lock:
                if dialog_id in self.playback_events:
                    del self.playback_events[dialog_id]


class TTSService(BaseService):
    """TTS service with voice synthesis."""
    
    def __init__(self):
        super().__init__('tts', 'config/config.ini')
        self.logger_channel = None
        self.logger_stub = None
        
    def setup(self):
        """Setup TTS service."""
        try:
            # Connect to logger service
            self.logger.info("Connecting to Logger service...")
            self.logger_channel = grpc.insecure_channel('127.0.0.1:5001')
            self.logger_stub = services_pb2_grpc.LoggerServiceStub(self.logger_channel)
            
            # Log startup
            self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                service="tts",
                event="startup",
                message="TTS service starting",
                level="INFO",
                timestamp_ms=int(time.time() * 1000)
            ))
            
            # Add TTS service to gRPC server
            tts_servicer = TTSServicer(self.config, self.logger_stub)
            services_pb2_grpc.add_TtsServiceServicer_to_server(
                tts_servicer, self.server
            )
            
            self.logger.info("TTS service setup complete")
            
        except Exception as e:
            self.logger.error(f"Failed to setup TTS service: {e}")
            raise
            
    def cleanup(self):
        """Cleanup TTS service resources."""
        if self.logger_stub:
            try:
                self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                    service="tts",
                    event="shutdown",
                    message="TTS service stopping",
                    level="INFO",
                    timestamp_ms=int(time.time() * 1000)
                ))
            except:
                pass
                
        if self.logger_channel:
            self.logger_channel.close()


if __name__ == "__main__":
    service = TTSService()
    service.start()
