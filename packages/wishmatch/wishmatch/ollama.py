"""
A tiny Ollama client (standard library only).

Ollama runs open-weight language models on your own machine and answers over HTTP.
Everything here fails quietly: a missing server or model returns None, and the
caller falls back to the plain word lookup.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class OllamaClient:
    def __init__(self, url: str, model: str, *, timeout: float = 20.0, keep_alive: str = "5m",
                 options: Optional[Dict[str, Any]] = None, think: Optional[bool] = False):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.options = dict(options or {})
        self.think = think          # False: models that "think out loud" first (Qwen 3) answer straight away
        self.last_error: Optional[str] = None
        self.last_seconds: Optional[float] = None
        self._pulling = False

    def _post(self, path: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def _get(self, path: str, timeout: float = 3.0) -> Dict[str, Any]:
        with urllib.request.urlopen(self.url + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def generate(self, prompt: str, schema: Optional[Dict[str, Any]] = None, system: str = "") -> Optional[str]:
        """The model's answer, or None when Ollama can't be reached, times out or lacks the model."""
        body: Dict[str, Any] = {"model": self.model, "prompt": prompt, "stream": False,
                                "keep_alive": self.keep_alive, "options": self.options}
        if self.think is not None:
            body["think"] = self.think
        if system:
            body["system"] = system
        if schema:
            body["format"] = schema
        start = time.time()
        try:
            out = self._post("/api/generate", body, self.timeout)
            self.last_error = None
            return str(out.get("response") or "")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None
        finally:
            self.last_seconds = round(time.time() - start, 2)

    def status(self) -> Dict[str, Any]:
        """Whether Ollama answers and has the model."""
        try:
            tags = self._get("/api/tags")
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"reachable": False, "model": self.model, "error": f"{type(e).__name__}: {e}"}
        names = {m.get("name") for m in tags.get("models") or []} | {m.get("model") for m in tags.get("models") or []}
        has = self.model in names or f"{self.model}:latest" in names
        return {"reachable": True, "model": self.model, "has_model": has, "pulling": self._pulling,
                "last_seconds": self.last_seconds, "last_error": self.last_error}

    def ensure_model(self, background: bool = True) -> None:
        """Ask Ollama to download the model if it doesn't have it (once; a download is free but large)."""
        def pull() -> None:
            try:
                if self.status().get("has_model"):
                    return
                self._pulling = True
                print(f"[Wishmatch] Asking Ollama to download {self.model} (one time).", flush=True)
                self._post("/api/pull", {"model": self.model, "stream": False}, timeout=3600)
                print(f"[Wishmatch] Ollama has {self.model}.", flush=True)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"[Wishmatch] Could not download {self.model} into Ollama ({e}).", flush=True)
            finally:
                self._pulling = False

        if background:
            threading.Thread(target=pull, name="wishmatch-ollama-pull", daemon=True).start()
        else:
            pull()
