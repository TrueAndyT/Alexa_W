#!/usr/bin/env python3
"""Loader Service - Phased-parallel orchestrator for all services."""
import sys
import time
import grpc
import subprocess
import signal
import threading
import queue
import random
import psutil
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import logging
from concurrent import futures
from enum import Enum

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from common.health_client import HealthClient
from common.gpu_monitor import GPUMonitor
from grpc_health.v1 import health_pb2


class SystemState(Enum):
    """System state enumeration."""
    INITIALIZING = "INITIALIZING"
    PHASE_1 = "PHASE_1"  # TTS + LLM parallel
    PHASE_2 = "PHASE_2"  # STT
    PHASE_3 = "PHASE_3"  # KWD
    IDLE = "IDLE"
    DIALOG = "DIALOG"
    ERROR = "ERROR"


class ServiceInfo:
    """Information about a managed service."""
    
    def __init__(self, name: str, port: int, script: str):
        self.name = name
        self.port = port
        self.script = script
        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.health_client: Optional[HealthClient] = None
        self.health_status = health_pb2.HealthCheckResponse.NOT_SERVING
        self.restart_count = 0
        self.last_restart_time = 0
        

class PhaseController:
    """Controls phased-parallel startup."""
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.gpu_monitor = GPUMonitor()
        
        # Config values
        self.parallel_timeout_ms = config.get_int('loader', 'parallel_phase_timeout_ms', fallback=8000)
        self.min_vram_mb = config.get_int('system', 'min_vram_mb', fallback=8000)
        self.restart_backoff = [1000, 3000, 5000]  # ms
        
    def check_vram(self) -> bool:
        """Check if VRAM meets minimum requirement."""
        try:
            vram_info = self.gpu_monitor.get_gpu_memory()
            if vram_info and vram_info['free_mb'] >= self.min_vram_mb:
                self.logger.info(f"VRAM check passed: {vram_info['free_mb']}MB free")
                return True
            else:
                self.logger.error(f"VRAM check failed: need {self.min_vram_mb}MB, have {vram_info.get('free_mb', 0)}MB")
                return False
        except Exception as e:
            self.logger.error(f"VRAM check error: {e}")
            return False
            
    def wait_for_services(self, services: List[ServiceInfo], timeout_ms: int) -> bool:
        """Wait for multiple services to be SERVING."""
        deadline = time.time() + (timeout_ms / 1000)
        
        while time.time() < deadline:
            all_serving = True
            
            for service in services:
                if service.health_client:
                    status_str = service.health_client.check()
                    # Convert string status to enum value
                    if status_str == "SERVING":
                        service.health_status = health_pb2.HealthCheckResponse.SERVING
                    elif status_str == "NOT_SERVING":
                        service.health_status = health_pb2.HealthCheckResponse.NOT_SERVING
                    else:
                        service.health_status = health_pb2.HealthCheckResponse.UNKNOWN
                    
                    if service.health_status != health_pb2.HealthCheckResponse.SERVING:
                        all_serving = False
                        
            if all_serving:
                return True
                
            time.sleep(0.5)
            
        return False


class DialogManager:
    """Manages dialog state and flow."""
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        
        # Dialog state
        self.current_dialog_id: Optional[str] = None
        self.turn_number = 0
        self.dialog_active = False
        self.follow_up_timer: Optional[threading.Timer] = None
        
        # Config
        self.follow_up_timeout_s = 4.0
        self.yes_phrases = config.get('kwd', 'yes_phrases', fallback='Yes?;Yes, Master?').split(';')
        self.warmup_greeting = config.get('kwd', 'warmup_greeting', fallback='Hi, Master!')
        
    def start_dialog(self, dialog_id: str):
        """Start a new dialog."""
        self.current_dialog_id = dialog_id
        self.turn_number = 1
        self.dialog_active = True
        self.logger.info(f"Dialog started: {dialog_id}")
        
    def increment_turn(self):
        """Increment turn number."""
        self.turn_number += 1
        self.logger.info(f"Dialog turn {self.turn_number}")
        
    def end_dialog(self):
        """End current dialog."""
        if self.follow_up_timer:
            self.follow_up_timer.cancel()
            self.follow_up_timer = None
            
        self.logger.info(f"Dialog ended: {self.current_dialog_id}")
        self.current_dialog_id = None
        self.turn_number = 0
        self.dialog_active = False
        
    def get_random_yes_phrase(self) -> str:
        """Get a random yes phrase."""
        return random.choice(self.yes_phrases)
        
    def start_follow_up_timer(self, callback):
        """Start follow-up timer."""
        if self.follow_up_timer:
            self.follow_up_timer.cancel()
            
        self.follow_up_timer = threading.Timer(self.follow_up_timeout_s, callback)
        self.follow_up_timer.start()
        self.logger.info(f"Follow-up timer started ({self.follow_up_timeout_s}s)")
        
    def cancel_follow_up_timer(self):
        """Cancel follow-up timer."""
        if self.follow_up_timer:
            self.follow_up_timer.cancel()
            self.follow_up_timer = None
            self.logger.info("Follow-up timer cancelled")


class LoaderServicer(services_pb2_grpc.LoaderServiceServicer):
    """gRPC service implementation for Loader."""
    
    def __init__(self, loader_instance):
        self.loader = loader_instance
        self.logger = logging.getLogger("LoaderServicer")
        
    def StartService(self, request, context):
        """Start a specific service."""
        service_name = request.service_name
        
        if service_name in self.loader.services:
            success = self.loader.start_service(service_name)
            return services_pb2.Status(
                success=success,
                message=f"Service {service_name} {'started' if success else 'failed to start'}",
                code=0 if success else 1
            )
        else:
            return services_pb2.Status(
                success=False,
                message=f"Unknown service: {service_name}",
                code=404
            )
            
    def StopService(self, request, context):
        """Stop a specific service."""
        service_name = request.service_name
        
        if service_name in self.loader.services:
            success = self.loader.stop_service(service_name)
            return services_pb2.Status(
                success=success,
                message=f"Service {service_name} {'stopped' if success else 'failed to stop'}",
                code=0 if success else 1
            )
        else:
            return services_pb2.Status(
                success=False,
                message=f"Unknown service: {service_name}",
                code=404
            )
            
    def GetPids(self, request, context):
        """Get PIDs of all services."""
        pids = {}
        
        for name, service in self.loader.services.items():
            if service.pid:
                pids[name] = service.pid
                
        return services_pb2.PidsResponse(pids=pids)
        
    def GetStatus(self, request, context):
        """Get system status."""
        # Get service health
        service_health = {}
        for name, service in self.loader.services.items():
            if service.health_status == health_pb2.HealthCheckResponse.SERVING:
                service_health[name] = "SERVING"
            elif service.health_status == health_pb2.HealthCheckResponse.NOT_SERVING:
                service_health[name] = "NOT_SERVING"
            else:
                service_health[name] = "UNKNOWN"
                
        # Get VRAM usage
        vram_used = 0
        try:
            gpu_info = self.loader.phase_controller.gpu_monitor.get_gpu_memory()
            if gpu_info:
                vram_used = gpu_info['used_mb']
        except:
            pass
            
        return services_pb2.SystemStatus(
            state=self.loader.system_state.value,
            service_health=service_health,
            vram_used_mb=vram_used,
            uptime_ms=int((time.time() - self.loader.start_time) * 1000)
        )


class LoaderService(BaseService):
    """Loader service - orchestrates all other services."""
    
    def __init__(self):
        super().__init__('loader', 'config/config.ini')
        
        # Service definitions
        self.services: Dict[str, ServiceInfo] = {
            'logger': ServiceInfo('logger', 5001, 'services/logger/logger_service.py'),
            'tts': ServiceInfo('tts', 5006, 'services/tts/tts_service.py'),
            'llm': ServiceInfo('llm', 5005, 'services/llm/llm_service.py'),
            'stt': ServiceInfo('stt', 5004, 'services/stt/stt_service.py'),
            'kwd': ServiceInfo('kwd', 5003, 'services/kwd/kwd_service.py'),
        }
        
        # Components
        self.phase_controller = PhaseController(self.config, self.logger)
        self.dialog_manager = DialogManager(self.config, self.logger)
        
        # State
        self.system_state = SystemState.INITIALIZING
        self.start_time = time.time()
        self.stopping = False  # Track if we're already stopping
        self.ollama_process = None  # Track Ollama server process
        
        # Event handlers
        self.kwd_events_thread: Optional[threading.Thread] = None
        self.stt_results_thread: Optional[threading.Thread] = None
        self.tts_events_thread: Optional[threading.Thread] = None
        
        # Service stubs
        self.logger_stub: Optional[services_pb2_grpc.LoggerServiceStub] = None
        self.kwd_stub: Optional[services_pb2_grpc.KwdServiceStub] = None
        self.stt_stub: Optional[services_pb2_grpc.SttServiceStub] = None
        self.llm_stub: Optional[services_pb2_grpc.LlmServiceStub] = None
        self.tts_stub: Optional[services_pb2_grpc.TtsServiceStub] = None
        
    def setup(self):
        """Setup loader service and start services sequentially."""
        try:
            # Kill any orphaned services before starting
            self.logger.info("Cleaning up any orphaned services...")
            self.kill_orphaned_services()
            
            # Add Loader service to gRPC server
            loader_servicer = LoaderServicer(self)
            services_pb2_grpc.add_LoaderServiceServicer_to_server(
                loader_servicer, self.server
            )
            
            # Start Ollama server first (needed for LLM)
            self.logger.info("Starting Ollama server...")
            if not self.start_ollama_server():
                raise Exception("Failed to start Ollama server")
            
            # Pre-load LLM model into VRAM
            self.logger.info("Pre-loading LLM model (llama3.1:8b-instruct-q4_K_M)...")
            if not self.preload_llm_model():
                raise Exception("Failed to pre-load LLM model")
            
            self.logger.info("Starting services sequentially...")
            
            # Start logger first (always needed for logging)
            self.logger.info("[1/5] Starting Logger service...")
            if not self.start_and_wait_for_service('logger', 5001):
                raise Exception("Failed to start Logger service")
            
            # Connect to logger for app logging
            logger_channel = grpc.insecure_channel('127.0.0.1:5001')
            self.logger_stub = services_pb2_grpc.LoggerServiceStub(logger_channel)
            
            # Log startup
            self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                service="loader",
                event="startup",
                message="Loader service starting sequential service loading",
                level="INFO",
                timestamp_ms=int(time.time() * 1000)
            ))
            
            # Check VRAM before starting services
            # Commented out - test shows all services fit together
            # if not self.phase_controller.check_vram():
            #     self.logger.warning("Initial VRAM check failed, but continuing...")
            
            # Start services in order: KWD -> STT -> LLM -> TTS
            
            # 1. Start KWD (Keyword Detection)
            self.logger.info("[2/5] Starting KWD service...")
            if not self.start_and_wait_for_service('kwd', 5003):
                raise Exception("Failed to start KWD service")
            self.kwd_stub = services_pb2_grpc.KwdServiceStub(grpc.insecure_channel('127.0.0.1:5003'))
            self.logger.info("KWD service ready")
            
            # 2. Start STT (Speech-to-Text)
            self.logger.info("[3/5] Starting STT service (Whisper - uses ~1.5GB VRAM)...")
            if not self.start_and_wait_for_service('stt', 5004, timeout_seconds=30):
                raise Exception("Failed to start STT service")
            self.stt_stub = services_pb2_grpc.SttServiceStub(grpc.insecure_channel('127.0.0.1:5004'))
            self.logger.info("STT service ready")
            
            # 3. Start LLM (Language Model)
            self.logger.info("[4/5] Starting LLM service...")
            if not self.start_and_wait_for_service('llm', 5005):
                raise Exception("Failed to start LLM service")
            self.llm_stub = services_pb2_grpc.LlmServiceStub(grpc.insecure_channel('127.0.0.1:5005'))
            self.logger.info("LLM service ready")
            
            # 4. Start TTS (Text-to-Speech)
            self.logger.info("[5/5] Starting TTS service (Kokoro)...")
            if not self.start_and_wait_for_service('tts', 5006, timeout_seconds=30):
                raise Exception("Failed to start TTS service")
            self.tts_stub = services_pb2_grpc.TtsServiceStub(grpc.insecure_channel('127.0.0.1:5006'))
            self.logger.info("TTS service ready")
            
            # All services loaded
            self.logger.info("All services loaded successfully")
            
            # Warm-up greeting
            self.play_warmup_greeting()
            
            # Start event listeners
            self.start_event_listeners()
            
            # Enter IDLE state
            self.system_state = SystemState.IDLE
            self.logger.info("System ready in IDLE state - waiting for wake word")
            
        except Exception as e:
            self.logger.error(f"Failed to setup loader service: {e}")
            self.system_state = SystemState.ERROR
            # Kill all services on error
            self.cleanup()
            raise
            
    def start_and_wait_for_service(self, name: str, port: int, timeout_seconds: int = 10) -> bool:
        """Start a service and wait for it to be healthy.
        
        Args:
            name: Service name
            port: Service port
            timeout_seconds: Maximum time to wait for service to be healthy
            
        Returns:
            True if service started and is healthy, False otherwise
        """
        try:
            # Start the service
            service = self.services.get(name)
            if not service:
                self.logger.error(f"Unknown service: {name}")
                return False
            
            # Start the service process
            if not self.start_service(name):
                self.logger.error(f"Failed to start {name} service")
                return False
            
            # Wait a moment for service to initialize
            time.sleep(2)
            
            # Create health client if not exists
            if not service.health_client:
                service.health_client = HealthClient(port=port)
            
            # Wait for service to be healthy
            self.logger.info(f"Waiting for {name} service to be healthy...")
            deadline = time.time() + timeout_seconds
            
            while time.time() < deadline:
                status = service.health_client.check()
                if status == "SERVING":
                    service.health_status = health_pb2.HealthCheckResponse.SERVING
                    self.logger.info(f"{name} service is SERVING")
                    return True
                elif status == "NOT_SERVING":
                    service.health_status = health_pb2.HealthCheckResponse.NOT_SERVING
                else:
                    service.health_status = health_pb2.HealthCheckResponse.UNKNOWN
                
                time.sleep(0.5)
            
            # Timeout reached
            self.logger.error(f"{name} service health check timeout after {timeout_seconds}s")
            
            # Check the service log for errors
            log_file = f"{name}_service.log"
            if Path(log_file).exists():
                with open(log_file, 'r') as f:
                    last_lines = f.readlines()[-10:]  # Get last 10 lines
                    self.logger.error(f"Last lines from {name} log:")
                    for line in last_lines:
                        self.logger.error(f"  {line.strip()}")
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error starting/waiting for {name}: {e}")
            return False
        
    def play_warmup_greeting(self):
        """Play warm-up greeting."""
        try:
            self.logger.info("Playing warm-up greeting")
            
            if self.tts_stub:
                response = self.tts_stub.Speak(services_pb2.SpeakRequest(
                    text=self.dialog_manager.warmup_greeting,
                    dialog_id="warmup",
                    voice="af_heart"
                ))
                
                if response.success:
                    self.logger.info("Warm-up greeting played")
                else:
                    self.logger.error(f"Failed to play greeting: {response.message}")
                    
        except Exception as e:
            self.logger.error(f"Error playing warm-up greeting: {e}")
            
    def start_event_listeners(self):
        """Start event listener threads."""
        # KWD events listener
        self.kwd_events_thread = threading.Thread(target=self.listen_kwd_events, daemon=True)
        self.kwd_events_thread.start()
        
        self.logger.info("Event listeners started")
        
    def listen_kwd_events(self):
        """Listen for wake word events."""
        try:
            for event in self.kwd_stub.Events(services_pb2.Empty()):
                self.logger.info(f"Wake detected: {event.wake_word} (confidence: {event.confidence:.2f})")
                
                # Handle wake in main thread to avoid concurrency issues
                threading.Thread(target=self.handle_wake_event, args=(event,), daemon=True).start()
                
        except Exception as e:
            self.logger.error(f"KWD event listener error: {e}")
            
    def handle_wake_event(self, event):
        """Handle wake word detection."""
        try:
            if self.system_state != SystemState.IDLE:
                self.logger.info("Wake ignored - system not idle")
                return
                
            # Start dialog
            self.system_state = SystemState.DIALOG
            
            # Create new dialog
            dialog_response = self.logger_stub.NewDialog(services_pb2.NewDialogRequest(
                timestamp_ms=int(time.time() * 1000)
            ))
            dialog_id = dialog_response.dialog_id
            
            self.dialog_manager.start_dialog(dialog_id)
            
            # Disable KWD during dialog
            self.kwd_stub.Disable(services_pb2.Empty())
            
            # Say random yes phrase
            yes_phrase = self.dialog_manager.get_random_yes_phrase()
            self.tts_stub.Speak(services_pb2.SpeakRequest(
                text=yes_phrase,
                dialog_id=dialog_id,
                voice="af_heart"
            ))
            
            # Start STT
            self.stt_stub.Start(services_pb2.StartRequest(
                dialog_id=dialog_id,
                turn_number=self.dialog_manager.turn_number
            ))
            
            # Listen for STT results
            self.listen_stt_results(dialog_id)
            
        except Exception as e:
            self.logger.error(f"Error handling wake event: {e}")
            self.system_state = SystemState.IDLE
            
    def listen_stt_results(self, dialog_id: str):
        """Listen for STT results and process them."""
        try:
            dialog_ref = services_pb2.DialogRef(
                dialog_id=dialog_id,
                turn_number=self.dialog_manager.turn_number
            )
            
            for result in self.stt_stub.Results(dialog_ref):
                if result.final:
                    user_text = result.text
                    self.logger.info(f"User said: {user_text}")
                    
                    # Process with LLM and stream to TTS
                    self.process_user_input(dialog_id, user_text)
                    break
                    
        except Exception as e:
            self.logger.error(f"Error listening to STT results: {e}")
            
    def process_user_input(self, dialog_id: str, user_text: str):
        """Process user input through LLM and TTS."""
        try:
            # Stop STT
            self.stt_stub.Stop(services_pb2.StopRequest(dialog_id=dialog_id))
            
            # Cancel any existing follow-up timer
            self.dialog_manager.cancel_follow_up_timer()
            
            # Check for empty input
            if not user_text or user_text.strip() == "":
                self.logger.warning("Empty user input detected")
                # Play error prompt
                self.play_error_prompt(dialog_id, "stt_error")
                return
            
            # Get LLM completion and stream to TTS
            complete_request = services_pb2.CompleteRequest(
                text=user_text,
                dialog_id=dialog_id,
                turn_number=self.dialog_manager.turn_number,
                conversation_history=""
            )
            
            # Stream LLM chunks to TTS using streaming interface
            try:
                # Create a generator for TTS chunks
                def generate_tts_chunks():
                    chunk_count = 0
                    for llm_chunk in self.llm_stub.Complete(complete_request):
                        if llm_chunk.text:
                            chunk_count += 1
                            # Send to TTS stream
                            yield services_pb2.LlmChunk(
                                text=llm_chunk.text,
                                eot=False,
                                dialog_id=dialog_id
                            )
                        if llm_chunk.eot:
                            # Send EOT marker
                            yield services_pb2.LlmChunk(
                                text="",
                                eot=True,
                                dialog_id=dialog_id
                            )
                            break
                    
                    if chunk_count == 0:
                        self.logger.warning("No response from LLM")
                        raise Exception("Empty LLM response")
                
                # Stream to TTS
                tts_response = self.tts_stub.SpeakStream(generate_tts_chunks())
                
                if tts_response.success:
                    self.logger.info(f"TTS streaming complete: {tts_response.message}")
                    
                    # Start follow-up timer after TTS completes
                    self.dialog_manager.start_follow_up_timer(self.on_follow_up_timeout)
                    
                    # Listen for playback completion events
                    self.monitor_playback_completion(dialog_id)
                else:
                    self.logger.error(f"TTS streaming failed: {tts_response.message}")
                    self.play_error_prompt(dialog_id, "tts_error")
                    
            except grpc.RpcError as e:
                self.logger.error(f"LLM streaming error: {e}")
                self.play_error_prompt(dialog_id, "llm_error")
            except Exception as e:
                self.logger.error(f"Streaming bridge error: {e}")
                self.play_error_prompt(dialog_id, "general_error")
            
        except Exception as e:
            self.logger.error(f"Error processing user input: {e}")
            self.play_error_prompt(dialog_id, "general_error")
            
    def play_error_prompt(self, dialog_id: str, error_type: str):
        """Play appropriate error prompt based on error type."""
        error_prompts = {
            "stt_error": "Sorry, I didn't catch that.",
            "llm_error": "Sorry, I had a problem thinking about that.",
            "tts_error": "Sorry, I'm having trouble speaking.",
            "general_error": "Sorry, something went wrong."
        }
        
        prompt = error_prompts.get(error_type, error_prompts["general_error"])
        
        try:
            self.logger.info(f"Playing error prompt: {prompt}")
            self.tts_stub.Speak(services_pb2.SpeakRequest(
                text=prompt,
                dialog_id=dialog_id,
                voice="af_heart"
            ))
            
            # Keep 4s window for follow-up
            self.dialog_manager.start_follow_up_timer(self.on_follow_up_timeout)
            
        except Exception as e:
            self.logger.error(f"Failed to play error prompt: {e}")
            # Force end dialog if we can't even play error prompt
            self.on_follow_up_timeout()
    
    def monitor_playback_completion(self, dialog_id: str):
        """Monitor TTS playback completion events."""
        def monitor_events():
            try:
                dialog_ref = services_pb2.DialogRef(
                    dialog_id=dialog_id,
                    turn_number=self.dialog_manager.turn_number
                )
                
                for event in self.tts_stub.PlaybackEvents(dialog_ref):
                    self.logger.debug(f"Playback event: {event.event_type}")
                    
                    if event.event_type == "finished":
                        self.logger.info("Playback finished, ready for follow-up")
                        break
                        
            except Exception as e:
                self.logger.error(f"Error monitoring playback: {e}")
        
        # Start monitoring in background
        threading.Thread(target=monitor_events, daemon=True).start()
    
    def handle_follow_up_speech(self, dialog_id: str):
        """Handle follow-up speech within 4s window."""
        try:
            # Cancel timer since we got speech
            self.dialog_manager.cancel_follow_up_timer()
            
            # Increment turn number
            self.dialog_manager.increment_turn()
            
            # Start STT for next turn
            self.stt_stub.Start(services_pb2.StartRequest(
                dialog_id=dialog_id,
                turn_number=self.dialog_manager.turn_number
            ))
            
            # Listen for results
            self.listen_stt_results(dialog_id)
            
        except Exception as e:
            self.logger.error(f"Error handling follow-up: {e}")
            self.play_error_prompt(dialog_id, "general_error")
    
    def start_ollama_server(self) -> bool:
        """Start Ollama server for LLM."""
        try:
            # Check if already running
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
            if result.returncode == 0:
                self.logger.info("Ollama server already running")
                return True
            
            # Start Ollama server
            self.ollama_process = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Wait for server to be ready
            for i in range(10):
                time.sleep(1)
                result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
                if result.returncode == 0:
                    self.logger.info("Ollama server started successfully")
                    return True
            
            self.logger.error("Ollama server failed to start in time")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to start Ollama server: {e}")
            return False
    
    def preload_llm_model(self) -> bool:
        """Pre-load LLM model into VRAM."""
        try:
            self.logger.info("Loading LLM model into VRAM (this may take a moment)...")
            
            # Run a simple query to load the model
            result = subprocess.run(
                ["ollama", "run", "llama3.1:8b-instruct-q4_K_M", "hi"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("LLM model loaded successfully into VRAM")
                
                # Check VRAM usage after model load
                try:
                    vram_info = self.phase_controller.gpu_monitor.get_gpu_memory()
                    if vram_info:
                        self.logger.info(f"VRAM after LLM model: {vram_info['used_mb']}MB used, {vram_info['free_mb']}MB free")
                except:
                    pass
                
                return True
            else:
                self.logger.error(f"Failed to load LLM model: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("LLM model loading timed out")
            return False
        except Exception as e:
            self.logger.error(f"Failed to load LLM model: {e}")
            return False
    
    def on_follow_up_timeout(self):
        """Handle follow-up timeout."""
        self.logger.info("Follow-up timeout - ending dialog")
        
        # End dialog
        self.dialog_manager.end_dialog()
        
        # Re-enable KWD
        if self.kwd_stub:
            self.kwd_stub.Enable(services_pb2.Empty())
            
        # Return to IDLE
        self.system_state = SystemState.IDLE
        
    def start_service(self, name: str) -> bool:
        """Start a service."""
        service = self.services.get(name)
        if not service:
            return False
            
        try:
            # Check if already running
            if service.process and service.process.poll() is None:
                self.logger.info(f"{name} already running")
                return True
                
            # Start the service
            script_path = Path(service.script)
            if not script_path.exists():
                self.logger.error(f"Service script not found: {script_path}")
                return False
                
            # Use virtual environment Python
            venv_python = Path('.venv/bin/python').absolute()
            
            log_file = f"logs/{name}_service.log"
            service.process = subprocess.Popen(
                [str(venv_python), str(script_path)],
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT
            )
            
            service.pid = service.process.pid
            
            # Create health client
            service.health_client = HealthClient(port=service.port)
            
            self.logger.info(f"Started {name} service (PID: {service.pid})")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start {name}: {e}")
            return False
            
    def stop_service(self, name: str, force: bool = False) -> bool:
        """Stop a service.
        
        Args:
            name: Service name
            force: If True, use SIGKILL instead of SIGTERM
        """
        service = self.services.get(name)
        if not service:
            return False
            
        try:
            if service.process and service.process.poll() is None:
                if force:
                    service.process.kill()
                    self.logger.info(f"Force killed {name} service")
                else:
                    service.process.terminate()
                    try:
                        service.process.wait(timeout=2)
                        self.logger.info(f"Stopped {name} service gracefully")
                    except subprocess.TimeoutExpired:
                        service.process.kill()
                        self.logger.warning(f"Force killed {name} service after timeout")
                
                service.process = None
                service.pid = None
                
            if service.health_client:
                service.health_client.close()
                service.health_client = None
                
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to stop {name}: {e}")
            return False
            
    def kill_orphaned_services(self):
        """Kill any orphaned service processes before starting."""
        import psutil
        killed_count = 0
        
        # Service script patterns to kill
        service_patterns = [
            'logger_service.py',
            'tts_service.py', 
            'llm_service.py',
            'stt_service.py',
            'kwd_service.py'
        ]
        
        # Also kill Ollama if running
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline:
                    cmdline_str = ' '.join(cmdline)
                    for pattern in service_patterns:
                        if pattern in cmdline_str:
                            proc.kill()
                            killed_count += 1
                            self.logger.info(f"Killed orphaned {pattern} (PID: {proc.pid})")
                            break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        if killed_count > 0:
            self.logger.info(f"Killed {killed_count} orphaned service(s)")
            # Wait a bit for GPU memory to be released
            time.sleep(3)
    
    def cleanup(self):
        """Cleanup loader service resources."""
        if self.stopping:
            return  # Already cleaning up
        self.stopping = True
        
        # Cancel timers
        self.dialog_manager.cancel_follow_up_timer()
        
        # Force stop all services immediately
        self.logger.info("Stopping all child services...")
        for name in ['kwd', 'stt', 'llm', 'tts', 'logger']:
            self.stop_service(name, force=True)
        
        # Stop Ollama server
        if hasattr(self, 'ollama_process') and self.ollama_process:
            self.logger.info("Stopping Ollama server...")
            self.ollama_process.terminate()
            try:
                self.ollama_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.ollama_process.kill()
        
        # Extra cleanup
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            
        self.logger.info("Loader service cleanup complete")
    
    def _signal_handler(self, signum, frame):
        """Override signal handler to immediately kill all child services."""
        if self.stopping:
            return  # Already stopping
        
        self.logger.info(f"Received signal {signum} - immediately stopping all services")
        self.running = False
        self.stopping = True
        
        # Kill all child processes immediately
        for name, service in self.services.items():
            if service.process and service.process.poll() is None:
                try:
                    service.process.kill()
                    self.logger.info(f"Killed {name} service (PID: {service.pid})")
                except:
                    pass
        
        # Exit immediately
        sys.exit(0)


if __name__ == "__main__":
    service = LoaderService()
    service.start()
