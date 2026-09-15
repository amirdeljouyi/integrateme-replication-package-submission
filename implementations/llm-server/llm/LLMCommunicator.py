import time
import random
import threading
import traceback
from typing import Optional

import requests
from openai import OpenAI
from openai import (
    APIConnectionError,
    RateLimitError,
    APITimeoutError,
    OpenAIError,
)
from RequestState import RequestState
from config.AppConfig import AppConfig
import os, threading, time, uuid, traceback

class LLMCommunicator:
    """
    OpenAI: Uses Conversations API + Responses API
    HF / Ollama: Stateless single-shot calls (unless you add your own history).
    """

    DEFAULT_SYSTEM_INSTRUCTIONS = (
        "You are an expert Java developer specializing in writing high-quality unit tests. "
        "Follow best practices, ensuring comprehensive test coverage. "
        "Strictly adhere to JUnit conventions and the provided guidelines."
    )

    def __init__(self):
        self.config = AppConfig.get_instance()
        self.model = self.config.model_name

        self.openai_key = self.config.openai_key
        self.hf_key = self.config.hf_key
        self.hf_url = self.config.hf_url

        self.client = OpenAI(api_key=self.openai_key)
        self.logger = self.config.setup_logger("LLMCommunicator")

        # OpenAI conversation state
        self._lock = threading.RLock()
        self._conversation_id: Optional[str] = None
        self._system_instructions = self.DEFAULT_SYSTEM_INSTRUCTIONS

        self._max_output_tokens = getattr(self.config, "max_output_tokens", 200000)
        self._timeout_seconds = getattr(self.config, "openai_timeout_seconds", 450)
        self._max_retries = getattr(self.config, "openai_max_retries", 3)

    def call_llm(self, prompt: str, state: "RequestState") -> str:
        state.increment_llm_calls()
        self.logger.info("Calling model %s", self.model)

        if self.model.startswith(("gpt-", "chatgpt-", "o")):
            return self._call_openai(prompt)
        if self.model.endswith("-hf"):
            return self._call_huggingface_api(prompt)
        return self._call_ollama(prompt)

    # ----------------------------
    # OpenAI (Conversations + Responses)
    # ----------------------------

    def _ensure_conversation(self) -> str:
        """
        Create a new conversation once, and keep its ID.
        We store the 'system' message in the conversation items so it persists.  [oai_citation:1‡OpenAI Platform](https://platform.openai.com/docs/api-reference/conversations/create?utm_source=chatgpt.com)
        """
        with self._lock:
            if self._conversation_id is not None:
                return self._conversation_id

            conv = self.client.conversations.create(
                metadata={"component": "LLMCommunicator"},
                items=[
                    {
                        "type": "message",
                        "role": "system",
                        "content": [
                            {"type": "input_text", "text": self._system_instructions}
                        ],
                    }
                ],
            )
            self._conversation_id = conv.id
            self.logger.info("Created OpenAI conversation: %s", self._conversation_id)
            return self._conversation_id

    def _call_openai(self, prompt: str) -> str:
        conversation_id = self._ensure_conversation()
        self.logger.info("Open AI conversationID using: %s", conversation_id)

        call_id = uuid.uuid4().hex[:8]
        pid = os.getpid()
        tid = threading.get_ident()

        self.logger.info(
            "OPENAI_CALL_START call_id=%s pid=%s tid=%s conv=%s prompt_len=%d",
            call_id, pid, tid, conversation_id, len(prompt)
        )
        start = time.time()

        for attempt in range(1, self._max_retries + 1):
            try:
                # Responses API supports attaching the response to a conversation; items are auto-added.  [oai_citation:2‡OpenAI Platform](https://platform.openai.com/docs/api-reference/responses)
                resp = self.client.responses.create(
                    model=self.model,
                    conversation=conversation_id,
                    input=prompt,  # simple text input
                    max_output_tokens=self._max_output_tokens,
                    timeout=self._timeout_seconds,
                )

                text = (resp.output_text or "").strip()
                self.logger.info(
                    "OpenAI response ok (response_id=%s, conversation_id=%s)",
                    getattr(resp, "id", None),
                    conversation_id,
                )
                return text or ""

            except (APITimeoutError, APIConnectionError) as e:
                self.logger.warning("[Timeout/Connection] attempt %d/%d: %s", attempt, self._max_retries, e)
            except RateLimitError as e:
                self.logger.warning("[RateLimit] attempt %d/%d: %s", attempt, self._max_retries, e)
            except OpenAIError as e:
                # Non-retryable in many cases; you can choose to retry specific subclasses if desired.
                self.logger.error("[OpenAIError] attempt %d/%d: %s", attempt, self._max_retries, e)
                self.logger.error(
                    "OPENAI_CALL_FAIL  call_id=%s pid=%s tid=%s conv=%s elapsed=%.2fs err=%r\n%s",
                    call_id, pid, tid, conversation_id, time.time() - start, e, traceback.format_exc()
                )
                break

            # Exponential backoff + jitter
            sleep_s = min(2 ** attempt, 30) + random.random()
            time.sleep(sleep_s)

        self.logger.error("OpenAI call failed after retries (conversation_id=%s).", conversation_id)
        return "Error: LLM did not respond in time. Please try again later."

    # ----------------------------
    # Ollama
    # ----------------------------

    def _call_ollama(self, prompt: str) -> str:
        api_url = "http://localhost:11434/api/generate"
        try:
            r = requests.post(
                api_url,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=60,
            )
            r.raise_for_status()
            return (r.json().get("response") or "").strip()
        except requests.exceptions.Timeout:
            self.logger.error("Timeout from Ollama")
        except Exception as e:
            self.logger.error("Ollama error: %s", e)
            self.logger.debug(traceback.format_exc())
        return "Error during LLM processing."

    # ----------------------------
    # Hugging Face Inference API
    # ----------------------------

    def _call_huggingface_api(self, prompt: str) -> str:
        # FIXED: Authorization should use hf_key, and POST URL should be hf_url
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.hf_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 400,
                "return_full_text": False,
                "rope_frequency_base": 1_000_000,
            },
        }

        try:
            r = requests.post(self.hf_url, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()

            # Common HF text-generation format:
            if isinstance(data, list) and data and "generated_text" in data[0]:
                return (data[0]["generated_text"] or "").strip()

            self.logger.warning("Unexpected Hugging Face response: %s", data)
            return "Error: Unexpected response format from Hugging Face API."
        except Exception as e:
            self.logger.error("Hugging Face API error: %s", e)
            self.logger.debug(traceback.format_exc())
            return "Error during Hugging Face API request."

    # ----------------------------
    # Reset / conversation management
    # ----------------------------

    def reset(self):
        """
        Start a fresh OpenAI conversation (new conversation_id).
        """
        with self._lock:
            self._conversation_id = None

    def set_system_instructions(self, instructions: str):
        """
        Optional: change system prompt for future conversations.
        Note: changing it mid-conversation won't rewrite existing conversation items;
        call reset() after setting if you want a clean slate.
        """
        with self._lock:
            self._system_instructions = instructions