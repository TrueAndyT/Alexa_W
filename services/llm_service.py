#!/usr/bin/env python3
"""LLM Service with Ollama integration for streaming completions."""
import sys
import time
import json
import grpc
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional, AsyncIterator
import logging
from concurrent import futures

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from proto import services_pb2, services_pb2_grpc
from common.base_service import BaseService
from grpc_health.v1 import health_pb2


class OllamaClient:
    """Client for Ollama API with streaming support."""
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.logger = logging.getLogger("OllamaClient")
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    async def ensure_session(self):
        """Ensure session is created."""
        if not self.session:
            self.session = aiohttp.ClientSession()
            
    async def check_health(self) -> bool:
        """Check if Ollama is running."""
        try:
            await self.ensure_session()
            async with self.session.get(f"{self.base_url}/api/tags") as resp:
                return resp.status == 200
        except Exception as e:
            self.logger.error(f"Ollama health check failed: {e}")
            return False
            
    async def stream_completion(
        self, 
        prompt: str, 
        model: str = "llama3.1:8b-instruct-q4_K_M",
        system_prompt: Optional[str] = None
    ) -> AsyncIterator[dict]:
        """Stream completion from Ollama."""
        await self.ensure_session()
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 256
            }
        }
        
        if system_prompt:
            payload["system"] = system_prompt
            
        try:
            async with self.session.post(
                f"{self.base_url}/api/generate",
                json=payload
            ) as resp:
                async for line in resp.content:
                    if line:
                        try:
                            data = json.loads(line)
                            yield data
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            self.logger.error(f"Stream completion error: {e}")
            raise


class LLMServicer(services_pb2_grpc.LlmServiceServicer):
    """gRPC service implementation for LLM."""
    
    def __init__(self, config, logger_stub):
        self.config = config
        self.logger_stub = logger_stub
        self.logger = logging.getLogger("LLMService")
        
        # Get config values
        self.model = config.get('llm', 'model', fallback='llama3.1:8b')
        self.modelfile_path = config.get('llm', 'modelfile_path', fallback='config/Modelfile')
        
        # Load system prompt from Modelfile
        self.system_prompt = self._load_modelfile()
        
        # Ollama client
        self.ollama_client = OllamaClient()
        
        # Async event loop for streaming
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        self.logger.info(f"LLM service initialized with model: {self.model}")
        
    def _load_modelfile(self) -> str:
        """Load system prompt from Modelfile."""
        try:
            modelfile_path = Path(self.modelfile_path)
            if modelfile_path.exists():
                content = modelfile_path.read_text()
                # Extract SYSTEM section
                if 'SYSTEM """' in content:
                    start = content.index('SYSTEM """') + len('SYSTEM """')
                    end = content.index('"""', start)
                    system_prompt = content[start:end].strip()
                    self.logger.info("Loaded system prompt from Modelfile")
                    return system_prompt
        except Exception as e:
            self.logger.error(f"Failed to load Modelfile: {e}")
            
        # Default system prompt
        return """You are a helpful voice assistant called Alexa. You provide concise, 
        friendly responses suitable for spoken conversation. Keep your answers brief 
        and natural-sounding, as they will be converted to speech."""
    
    def Configure(self, request, context):
        """Configure LLM service."""
        if request.model:
            self.model = request.model
        if request.max_tokens > 0:
            # Update max_tokens in ollama options if needed
            pass
        if request.temperature > 0:
            # Update temperature in ollama options if needed
            pass
        
        return services_pb2.Status(
            success=True,
            message="LLM configured"
        )
        
    def Complete(self, request, context):
        """Stream completion response."""
        dialog_id = request.dialog_id
        user_text = request.text
        turn_number = request.turn_number
        
        # Log message received with timestamp
        received_time = time.time()
        self.logger.info(f"[MESSAGE RECEIVED] Dialog: {dialog_id}, Turn: {turn_number}, Time: {received_time:.3f}")
        self.logger.info(f"[USER TEXT] {user_text}")
        
        # Log user input to logger service (if available)
        if self.logger_stub:
            try:
                self.logger_stub.WriteDialog(services_pb2.DialogLogRequest(
                    dialog_id=dialog_id,
                    speaker="USER",
                    text=user_text,
                    timestamp_ms=int(time.time() * 1000)
                ))
            except Exception as e:
                self.logger.error(f"Failed to log user input: {e}")
        
        # Track timing for first token latency
        start_time = time.time()
        first_token_time = None
        full_response = []
        token_count = 0
        
        self.logger.info(f"[PROCESSING START] Time: {start_time:.3f}")
        
        try:
            # Create a queue to bridge async to sync
            import queue
            import threading
            response_queue = queue.Queue()
            exception_holder = {'exception': None}
            
            def run_async_stream():
                """Run the async stream in a thread with its own event loop."""
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def stream_tokens():
                    nonlocal first_token_time, token_count
                    
                    try:
                        async with self.ollama_client:
                            # Check Ollama health
                            self.logger.info("[OLLAMA CHECK] Checking Ollama health...")
                            if not await self.ollama_client.check_health():
                                self.logger.error("[OLLAMA ERROR] Ollama service not available")
                                raise Exception("Ollama service not available")
                            self.logger.info("[OLLAMA OK] Ollama is healthy")
                            
                            # Stream completion
                            self.logger.info(f"[OLLAMA REQUEST] Model: {self.model}, Prompt length: {len(user_text)}")
                            chunk_num = 0
                            async for chunk in self.ollama_client.stream_completion(
                                prompt=user_text,
                                model=self.model,
                                system_prompt=self.system_prompt
                            ):
                                chunk_num += 1
                                if 'response' in chunk:
                                    text = chunk['response']
                                    if text:
                                        # Track first token time
                                        if first_token_time is None:
                                            first_token_time = time.time()
                                            latency = (first_token_time - start_time) * 1000
                                            self.logger.info(f"[FIRST TOKEN] Latency: {latency:.0f}ms, Time: {first_token_time:.3f}")
                                        
                                        if chunk_num % 10 == 0:
                                            self.logger.debug(f"[CHUNK {chunk_num}] Text length: {len(text)}")
                                        
                                        full_response.append(text)
                                        token_count += 1
                                        
                                        # Put response chunk in queue
                                        response_queue.put(services_pb2.CompleteResponse(
                                            text=text,
                                            eot=False,
                                            token_count=token_count,
                                            latency_ms=latency if first_token_time else 0
                                        ))
                                
                                # Check if done
                                if chunk.get('done', False):
                                    self.logger.info(f"[OLLAMA DONE] Total chunks: {chunk_num}")
                                    break
                                    
                    except Exception as e:
                        self.logger.error(f"[STREAM ERROR] {str(e)}")
                        exception_holder['exception'] = e
                    finally:
                        # Signal completion
                        response_queue.put(None)
                
                loop.run_until_complete(stream_tokens())
                loop.close()
            
            # Start async streaming in a thread
            self.logger.info("[THREAD START] Starting async stream thread")
            stream_thread = threading.Thread(target=run_async_stream)
            stream_thread.start()
            
            # Yield responses as they come
            yielded_count = 0
            while True:
                response = response_queue.get()
                if response is None:
                    self.logger.info(f"[STREAM END] Total yielded: {yielded_count}")
                    break
                yielded_count += 1
                if yielded_count == 1:
                    self.logger.info(f"[FIRST YIELD] Time: {time.time():.3f}")
                yield response
            
            # Wait for thread to finish
            stream_thread.join(timeout=5)
            
            # Check for exceptions
            if exception_holder['exception']:
                raise exception_holder['exception']
                
            # Send final EOT marker
            yield services_pb2.CompleteResponse(
                text="",
                eot=True,
                token_count=token_count,
                latency_ms=(time.time() - start_time) * 1000
            )
            
            # Log complete assistant response
            full_text = ''.join(full_response)
            end_time = time.time()
            total_time = (end_time - start_time) * 1000
            if self.logger_stub:
                try:
                    self.logger_stub.WriteDialog(services_pb2.DialogLogRequest(
                        dialog_id=dialog_id,
                        speaker="ASSISTANT",
                        text=full_text,
                        timestamp_ms=int(time.time() * 1000)
                    ))
                except Exception as e:
                    self.logger.error(f"Failed to log assistant response: {e}")
            
            self.logger.info(f"[RESPONSE COMPLETE] Dialog: {dialog_id}, Length: {len(full_text)} chars, Total time: {total_time:.0f}ms")
            self.logger.info(f"[ASSISTANT TEXT] {full_text[:200]}..." if len(full_text) > 200 else f"[ASSISTANT TEXT] {full_text}")
                
        except Exception as e:
            self.logger.error(f"Error in Complete: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            


class LLMService(BaseService):
    """LLM service with Ollama integration."""
    
    def __init__(self):
        super().__init__('llm', 'config/config.ini')
        self.logger_channel = None
        self.logger_stub = None
        
    def setup(self):
        """Setup LLM service."""
        try:
            # Try to connect to logger service (optional)
            try:
                self.logger.info("Connecting to Logger service...")
                self.logger_channel = grpc.insecure_channel('localhost:5001')
                self.logger_stub = services_pb2_grpc.LoggerServiceStub(self.logger_channel)
                
                # Log startup
                self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                    service="llm",
                    event="startup",
                    message="LLM service starting",
                    level="INFO",
                    timestamp_ms=int(time.time() * 1000)
                ))
            except Exception as e:
                self.logger.warning(f"Logger service not available: {e}")
                self.logger_stub = None
            
            # Add LLM service to gRPC server
            self.logger.info("Creating LLMServicer...")
            llm_servicer = LLMServicer(self.config, self.logger_stub)
            self.logger.info("Adding LLMServicer to gRPC server...")
            services_pb2_grpc.add_LlmServiceServicer_to_server(
                llm_servicer, self.server
            )
            
            self.logger.info("LLM service setup complete")
            
        except Exception as e:
            self.logger.error(f"Failed to setup LLM service: {e}")
            raise
            
    def cleanup(self):
        """Cleanup LLM service resources."""
        if self.logger_stub:
            try:
                self.logger_stub.WriteApp(services_pb2.AppLogRequest(
                    service="llm",
                    event="shutdown",
                    message="LLM service stopping",
                    level="INFO",
                    timestamp_ms=int(time.time() * 1000)
                ))
            except:
                pass
                
        if self.logger_channel:
            self.logger_channel.close()


if __name__ == "__main__":
    service = LLMService()
    service.start()
