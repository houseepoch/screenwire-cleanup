"""
Abstract base handler and shared utilities for media generation.

Centralises Replicate API integration (predict, poll, upload, download),
retry logic (tenacity), error classification, model parameter adaptation,
and fallback-chain execution so individual handlers stay lean.

@AI_STATUS:COMPLETE
@AI_REASONING: Single base class keeps API ceremony (auth, headers, retry,
    polling, data-URI encoding) in one place. Handlers only override
    generate() and build their model-specific input dicts.
    generate_batch() added for semaphore-capped (10) concurrent execution.
    _make_error_output() is the fallback — overridden by each handler for
    typed error outputs with preserved IDs.
@AI_DEPENDS: handlers/models.py:MODEL_ROUTES,RESOLUTION_SPECS
@AI_WARN: _gateway_retry wraps individual predict calls (3 attempts).
    The fallback chain in _run_model_chain is a SEPARATE layer on top.
    Don't confuse the two retry scopes.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from telemetry import build_request_headers

from .models import MODEL_ROUTES, ModelRoute

logger = logging.getLogger("handlers")

# ── Constants ──────────────────────────────────────────────────────

REPLICATE_API_BASE = "https://api.replicate.com/v1"
POLL_INTERVAL_S = 5
POLL_MAX_ATTEMPTS = 120  # 10 minutes max


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Retry predicate (matches server.py's _gateway_retry)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _is_retryable_http_error(exc: BaseException) -> bool:
    """Retry on 429 (rate-limit), 500 (server error), 503 (unavailable)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 503)
    return False


_gateway_retry = retry(
    retry=retry_if_exception(_is_retryable_http_error),
    wait=wait_exponential(min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Error classification (mirrors server.py's _classify_replicate_error)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SAFETY_TRIGGER_HINTS: list[str] = [
    "Avoid: blood, wound, gunshot, gore, corpse, dead body, kill",
    "Avoid: weapon aimed at camera, violence in progress",
    "Rephrase: 'injured soldier' -> 'battle-worn soldier with torn uniform'",
    "Rephrase: 'gunshot wound' -> 'damaged combat gear'",
    "Rephrase: 'blood' -> 'dirt and grime'",
    "Tip: Focus on emotion and atmosphere rather than explicit injury",
    "Tip: Use 'war photography' or 'documentary style' framing",
]


def classify_replicate_error(error_msg: str, logs: str = "") -> dict[str, Any]:
    """Parse a Replicate error to determine failure type and recoverability."""
    combined = f"{error_msg} {logs}".lower()

    if any(kw in combined for kw in ("nsfw", "safety", "blocked", "e005")):
        return {
            "failure_type": "SAFETY_FILTER",
            "is_retryable": True,
            "rephrase_hints": SAFETY_TRIGGER_HINTS,
        }
    if any(kw in combined for kw in ("timeout", "e004", "timed out")):
        return {
            "failure_type": "TIMEOUT",
            "is_retryable": False,
            "rephrase_hints": [],
        }
    if any(
        kw in combined
        for kw in ("capacity", "unavailable", "503", "e003", "overloaded")
    ):
        return {
            "failure_type": "UPSTREAM_TRANSIENT",
            "is_retryable": True,
            "rephrase_hints": [],
        }
    return {
        "failure_type": "MODEL_ERROR",
        "is_retryable": False,
        "rephrase_hints": [],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Model-specific input adaptation (mirrors server.py's _adapt_input)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def adapt_input_for_model(model: str, base_input: dict) -> dict:
    """
    Strip or inject parameters to match each model's accepted schema.

    Each Replicate model has a different input contract:
    - nano-banana-2: no safety_filter_level, no allow_fallback_model
    - nano-banana-pro: no google_search/image_search; adds safety + fallback
    - nano-banana (base): only prompt/image_input/aspect_ratio/output_format
    - p-image: prompt/aspect_ratio/seed/disable_safety_checker (no output_format)
    - grok-imagine-video: prompt/image/duration/resolution/aspect_ratio/mode
    """
    inp = dict(base_input)

    if model == "google/nano-banana-2":
        inp.pop("safety_filter_level", None)
        inp.pop("allow_fallback_model", None)

    elif model == "google/nano-banana-pro":
        inp.pop("google_search", None)
        inp.pop("image_search", None)
        inp.setdefault("safety_filter_level", "block_only_high")

    elif model == "google/nano-banana":
        allowed = {"prompt", "image_input", "aspect_ratio", "output_format"}
        inp = {k: v for k, v in inp.items() if k in allowed}

    elif model == "prunaai/p-image":
        # p-image only accepts: prompt, aspect_ratio, seed, width, height,
        # disable_safety_checker. It ALWAYS outputs JPG.
        allowed = {
            "prompt",
            "aspect_ratio",
            "seed",
            "width",
            "height",
            "disable_safety_checker",
        }
        inp = {k: v for k, v in inp.items() if k in allowed}

    elif model == "prunaai/p-image-upscale":
        allowed = {
            "image",
            "mode",
            "target_megapixels",
            "factor",
            "enhance_realism",
            "enhance_details",
            "output_format",
            "quality",
        }
        inp = {k: v for k, v in inp.items() if k in allowed}

    elif model == "xai/grok-imagine-video":
        allowed = {
            "prompt",
            "image",
            "video",
            "mode",
            "duration",
            "resolution",
            "aspect_ratio",
        }
        inp = {k: v for k, v in inp.items() if k in allowed}

    return inp


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Base Handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BaseHandler(ABC):
    """
    Abstract base for all media generation handlers.

    Provides:
    - Replicate API predict / poll / upload / download
    - Model fallback-chain execution
    - Error classification
    - Async HTTP client lifecycle (shared or owned)

    Subclasses override ``generate()`` and set ``handler_name``.
    """

    handler_name: str = ""  # Override in each subclass

    def __init__(
        self,
        replicate_token: str,
        xai_key: str = "",
        http_client: httpx.AsyncClient | None = None,
    ):
        self.replicate_token = replicate_token
        self.xai_key = xai_key
        self._client = http_client
        self._owns_client = http_client is None

    # ── Lifecycle ──────────────────────────────────────────────

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-create or return shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0)
            )
            self._owns_client = True
        return self._client

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseHandler":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── Abstract Interface ─────────────────────────────────────

    @abstractmethod
    async def generate(self, inp: Any) -> Any:
        """Generate media from a typed input. Override in each handler."""
        ...

    # ── Replicate API ──────────────────────────────────────────

    def _replicate_headers(
        self,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.replicate_token}",
            "Content-Type": "application/json",
            "Prefer": "wait",  # Synchronous mode
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    @staticmethod
    def _build_request_headers(
        *,
        run_id: str = "",
        phase: str = "",
        frame_id: str = "",
        asset_id: str = "",
    ) -> dict[str, str]:
        return build_request_headers(
            run_id=run_id,
            phase=phase,
            frame_id=frame_id,
            asset_id=asset_id,
        )

    @_gateway_retry
    async def _replicate_predict(
        self,
        model: str,
        pred_input: dict,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        """
        Create a Replicate prediction.

        Uses ``Prefer: wait`` for synchronous responses. Retries on
        429 / 500 / 503 up to 3 times with exponential back-off.
        """
        resp = await self.client.post(
            f"{REPLICATE_API_BASE}/models/{model}/predictions",
            json={"input": pred_input},
            headers=self._replicate_headers(extra_headers),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_prediction(
        self,
        prediction_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        """Poll a Replicate prediction until it reaches a terminal state."""
        url = f"{REPLICATE_API_BASE}/predictions/{prediction_id}"
        headers = self._replicate_headers(extra_headers)
        for _ in range(POLL_MAX_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_S)
            resp = await self.client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") in ("succeeded", "failed", "canceled"):
                return data
        return {"status": "timeout", "error": "Polling timed out after 10 minutes"}

    async def _resolve_prediction(
        self,
        prediction: dict,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        """Ensure a prediction reaches terminal state (poll if processing)."""
        status = prediction.get("status", "")
        if status in ("succeeded", "failed", "canceled", "timeout"):
            return prediction
        pred_id = prediction.get("id")
        if pred_id:
            return await self._poll_prediction(pred_id, extra_headers=extra_headers)
        return prediction

    # ── File Utilities ─────────────────────────────────────────

    async def upload_to_replicate(self, file_path: Path) -> str:
        """Convert a local file to a base64 data URI for Replicate inputs."""
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        b64 = base64.b64encode(file_path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    async def upload_many(self, paths: list[Path]) -> list[str]:
        """Upload multiple local files to data URIs concurrently."""
        existing = [p for p in paths if p.exists()]
        if not existing:
            return []
        return list(await asyncio.gather(*(self.upload_to_replicate(p) for p in existing)))

    async def download_output(self, url: str, output_path: Path) -> Path:
        """Download a Replicate output URL to a local file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        resp = await self.client.get(url, follow_redirects=True)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
        return output_path

    # ── Fallback Chain Execution ───────────────────────────────

    async def _run_model_chain(
        self,
        handler_name: str,
        base_input: dict,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[dict, str]:
        """
        Execute a model chain: try primary, then each fallback.

        Returns ``(prediction_data, model_used)``.

        The tenacity retry on ``_replicate_predict`` handles transient HTTP
        errors (429/500/503) at the individual-call level. This method adds
        a higher-level fallback across *different models* when a model is
        persistently unavailable or returns a failed prediction.
        """
        route: ModelRoute = MODEL_ROUTES[handler_name]
        chain = [route.primary] + route.fallback

        last_error: dict | None = None
        last_model = chain[0]

        for model in chain:
            last_model = model
            adapted = adapt_input_for_model(model, base_input.copy())

            try:
                prediction = await self._replicate_predict(
                    model,
                    adapted,
                    extra_headers=extra_headers,
                )
                prediction = await self._resolve_prediction(
                    prediction,
                    extra_headers=extra_headers,
                )

                status = prediction.get("status", "")
                if status == "succeeded":
                    return prediction, model

                # Failed — classify and decide whether to continue the chain
                error_msg = prediction.get("error", "") or ""
                logs = prediction.get("logs", "") or ""
                detail = classify_replicate_error(error_msg, logs)

                if detail["failure_type"] == "UPSTREAM_TRANSIENT":
                    logger.warning(
                        "Model %s transient failure, trying next in chain",
                        model,
                    )
                    last_error = prediction
                    continue

                # Non-transient → stop chain, return the error
                last_error = prediction
                break

            except RetryError:
                logger.warning(
                    "Model %s exhausted all retries, trying next in chain", model
                )
                last_error = {
                    "status": "failed",
                    "error": f"Retries exhausted for {model}",
                }
                continue

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (429, 503):
                    logger.warning(
                        "Model %s HTTP %d after retries, trying next",
                        model,
                        exc.response.status_code,
                    )
                    last_error = {"status": "failed", "error": str(exc)}
                    continue
                raise

        return last_error or {"status": "failed", "error": "Chain exhausted"}, last_model

    # ── 4K Capacity Rescue ─────────────────────────────────────

    async def _try_capacity_rescue(
        self,
        pred_input: dict,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[dict, str] | None:
        """
        Downshift 4K -> 2K + ``allow_fallback_model`` on nano-banana-pro.

        Used by the Frame handler when the full chain fails at 4K due to
        a transient capacity issue. Returns ``(prediction, model)`` on
        success or ``None`` if rescue also fails.
        """
        if pred_input.get("resolution") != "4K":
            return None

        rescue_input = dict(pred_input)
        rescue_input["resolution"] = "2K"
        rescue_input["allow_fallback_model"] = True
        rescue_input.setdefault("safety_filter_level", "block_only_high")

        model = "google/nano-banana-pro"
        adapted = adapt_input_for_model(model, rescue_input)

        try:
            prediction = await self._replicate_predict(
                model,
                adapted,
                extra_headers=extra_headers,
            )
            prediction = await self._resolve_prediction(
                prediction,
                extra_headers=extra_headers,
            )
            if prediction.get("status") == "succeeded":
                return prediction, model
        except Exception:
            logger.warning("4K capacity rescue also failed for nano-banana-pro")

        return None

    # ── Output Extraction ──────────────────────────────────────

    @staticmethod
    def extract_output_url(prediction: dict) -> str | None:
        """
        Extract the usable output URL from a Replicate prediction.

        Replicate returns output as either a bare string URL or a list
        of URLs (common for image models). This normalises both.
        """
        output = prediction.get("output")
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            return first if isinstance(first, str) else None
        return None

    # ── Batch Execution ───────────────────────────────────────

    async def generate_batch(
        self,
        inputs: list,
        max_concurrent: int = 10,
    ) -> "BatchResult":
        """
        Run up to *max_concurrent* ``generate()`` calls concurrently.

        Individual failures are captured as error outputs — one failure
        does **not** cancel the batch.  Results are returned in the same
        positional order as *inputs*.

        Returns a :class:`BatchResult` with per-item results and summary
        counters.
        """
        from .models import BatchResult

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _guarded(inp: Any) -> Any:
            async with semaphore:
                try:
                    return await self.generate(inp)
                except Exception as exc:
                    return self._make_error_output(inp, exc)

        results = list(await asyncio.gather(*[_guarded(i) for i in inputs]))
        succeeded = sum(1 for r in results if getattr(r, "success", False))
        return BatchResult(
            results=results,
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )

    def _make_error_output(self, inp: Any, exc: Exception) -> Any:
        """
        Create a minimal failed output when ``generate()`` raises unexpectedly.

        Handlers **should** override this to return their typed output with
        the relevant ID field populated.
        """
        from .models import HandlerOutput

        return HandlerOutput(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
