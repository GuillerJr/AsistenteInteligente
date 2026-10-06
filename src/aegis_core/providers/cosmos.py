"""NVIDIA-hosted Cosmos3 generation. One submission, bounded result retrieval."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import (
    AgentRole,
    Capability,
    ImageInput,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.generated_media import GeneratedMediaError, GeneratedMediaStore
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.providers.inference_metrics import record_http_request
from aegis_core.secrets import SecretNotFoundError, contains_likely_secret_material
from aegis_core.tools.broker import PolicyContext, ToolDefinition

COSMOS_TOOL = "cosmos_generate_media"
COSMOS_URL = "https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano"
_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_MAX_RESPONSE = 64_100_000


class CosmosArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_mode: Literal["text2image", "text2video", "image2video"]
    prompt: str = Field(min_length=3, max_length=4_000)
    resolution: Literal["480_16_9", "480_1_1", "480_9_16", "720_16_9", "720_1_1", "720_9_16"] = (
        "480_16_9"
    )
    num_frames: int = Field(default=49, ge=25, le=197, strict=True)
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)
    # Bound by Jarvis from the actual attachment before authorization. Never model-supplied.
    image: ImageInput | None = Field(default=None, repr=False)

    @field_validator("prompt")
    @classmethod
    def safe_prompt(cls, value: str) -> str:
        if not value.strip() or contains_likely_secret_material(value) or "\x00" in value:
            raise ValueError("invalid generation prompt")
        return redact_secrets_for_remote(value).text

    @model_validator(mode="after")
    def matching_input(self) -> CosmosArguments:
        if (self.model_mode == "image2video") != (self.image is not None):
            raise ValueError("image2video requires the current attached image")
        return self

    def payload(self) -> dict:
        value = {
            "model_mode": self.model_mode,
            "prompt": self.prompt,
            "resolution": self.resolution,
            "seed": self.seed,
            "num_inference_steps": 50 if self.model_mode == "text2image" else 35,
        }
        if self.model_mode != "text2image":
            value.update(num_frames=self.num_frames, fps=24)
        if self.image is not None:
            value["input_reference"] = self.image.data_uri
        return value


def cosmos_definition() -> ToolDefinition:
    schema = CosmosArguments.model_json_schema()
    schema["properties"].pop("image")
    schema.pop("$defs", None)
    return ToolDefinition(
        name=COSMOS_TOOL,
        description="Generate and deliver a real JPEG image or a short MP4 video with NVIDIA "
        "Cosmos3 Nano. text2image from a prompt; text2video from a prompt; image2video animates "
        "only the image attached to the current request. Never send image data, URLs or paths "
        "as arguments. Translate the visual description to a precise English prompt. "
        "Default short video is 49 frames at 24 fps; use up to 197 for about 8 seconds. "
        "Image defaults should use 720_1_1. This creates a file, not a text description. "
        "A one-time approval is required before sending the prompt/attachment to NVIDIA.",
        arguments_model=CosmosArguments,
        parameters_schema=schema,
        capability=Capability.MEDIA_GENERATION,
        risk=RiskLevel.MEDIUM,
        allowed_roles=frozenset({AgentRole.PLANNER}),
        requires_confirmation=True,
        provider_label="NVIDIA Cosmos3 Nano",
        external_destination="ai.api.nvidia.com",
    )


def bind_cosmos_call(call: ToolCall, request: UserRequest) -> ToolCall:
    if call.tool_name != COSMOS_TOOL:
        return call
    values = dict(call.arguments)
    # Reject forged input even if a model ignored its narrowed schema.
    if "image" in values or "input_reference" in values:
        return call.model_copy(update={"arguments": {"invalid_input": True}})
    if values.get("model_mode") == "image2video" and request.image is not None:
        values["image"] = request.image.model_dump()
    return call.model_copy(update={"arguments": values})


def wants_generated_media(request: UserRequest) -> bool:
    from aegis_core.request_intent import folded_request, mask_quoted_text

    text = mask_quoted_text(folded_request(request.text))
    if re.match(r"^(no |don't |do not )", text.strip()):
        return False
    return bool(
        re.search(
            r"\b(?:genera\w*|crea\w*|dibuja\w*|ilustra\w*|disena\w*|animate|anima\w*|"
            r"generate|create|draw|render|haz\w*|hacer|make)\b.{0,100}\b"
            r"(?:imagen|imagenes|ilustracion|foto|video|videos|image|images|picture|illustration|"
            r"animation|animacion|clip)\b",
            text,
        )
    ) or bool(request.image is not None and re.search(r"\b(?:animala|animate it)\b", text))


class CosmosError(RuntimeError):
    def __init__(self, code: str, request_id: str | None = None, *, status_code: int | None = None):
        super().__init__(code)
        self.code, self.request_id = code, request_id
        self.status_code = status_code


class CosmosClient:
    def __init__(self, key_loader: Callable[[], str], *, transport=None, timeout_seconds=210):
        self._key_loader = key_loader
        self.timeout_seconds = timeout_seconds
        self._http = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout_seconds, connect=10),
        )
        self._busy = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _fetch(
        self, method: str, url: str, headers: dict, payload=None
    ) -> tuple[int, dict, bytes]:
        record_http_request()
        async with self._http.stream(method, url, headers=headers, json=payload) as response:
            data = bytearray()
            async for part in response.aiter_bytes():
                if len(data) + len(part) > _MAX_RESPONSE:
                    raise CosmosError("cosmos_response_invalid")
                data.extend(part)
            return response.status_code, dict(response.headers), bytes(data)

    async def generate(self, arguments: CosmosArguments) -> tuple[bytes, str, str | None]:
        if self._busy.locked():
            raise CosmosError("cosmos_busy")
        async with self._busy:
            request_id = None
            submitted = False
            try:
                key = self._key_loader()
                if not key or any(c.isspace() for c in key):
                    raise CosmosError("cosmos_authentication_failed")
                headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
                async with asyncio.timeout(self.timeout_seconds):
                    submitted = True
                    status, response_headers, body = await self._fetch(
                        "POST", COSMOS_URL, headers, arguments.payload()
                    )
                    while status == 202:
                        candidate = response_headers.get("nvcf-reqid") or request_id
                        if (
                            not candidate
                            or not _REQUEST_ID.fullmatch(candidate)
                            or (request_id is not None and candidate != request_id)
                        ):
                            raise CosmosError("cosmos_result_unknown", request_id)
                        request_id = candidate
                        await asyncio.sleep(2)
                        # Fixed NVIDIA result host; never follow provider URLs with a bearer token.
                        status, response_headers, body = await self._fetch(
                            "GET",
                            f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/{request_id}",
                            headers,
                        )
                    if status in {401, 403}:
                        raise CosmosError(
                            "cosmos_authentication_failed", request_id, status_code=status
                        )
                    if status in {404, 410}:
                        raise CosmosError(
                            "cosmos_result_unknown" if request_id else "cosmos_model_unavailable",
                            request_id,
                            status_code=status,
                        )
                    if status == 429:
                        raise CosmosError("cosmos_rate_limited", request_id, status_code=status)
                    if status in {400, 422}:
                        raise CosmosError("cosmos_request_rejected", request_id, status_code=status)
                    if status != 200:
                        raise CosmosError(
                            "cosmos_result_unknown" if request_id else "cosmos_unavailable",
                            request_id,
                            status_code=status,
                        )
                    value = json.loads(body)
                    field = "b64_image" if arguments.model_mode == "text2image" else "b64_video"
                    if not isinstance(value, dict) or not isinstance(value.get(field), str):
                        raise CosmosError("cosmos_response_invalid", request_id)
                    raw = value[field]
                    data = base64.b64decode(raw, validate=True)
                    if base64.b64encode(data).decode("ascii") != raw:
                        raise CosmosError("cosmos_response_invalid", request_id)
                    return data, "image/jpeg" if field == "b64_image" else "video/mp4", request_id
            except CosmosError:
                raise
            except SecretNotFoundError:
                raise CosmosError("cosmos_authentication_failed", request_id) from None
            except (TimeoutError, httpx.HTTPError):
                raise CosmosError(
                    "cosmos_result_unknown" if submitted else "cosmos_unavailable", request_id
                ) from None
            except (ValueError, TypeError):
                raise CosmosError("cosmos_response_invalid", request_id) from None


class CosmosToolService:
    def __init__(self, client: CosmosClient, store: GeneratedMediaStore):
        self.client, self.store = client, store

    async def execute(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if (
            authorization.tool_name != COSMOS_TOOL
            or authorization.decision != PolicyDecision.ALLOW
            or authorization.reason_code != "confirmation_consumed"
            or context.is_security_compromised()
        ):
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=COSMOS_TOOL,
                success=False,
                error_code="authorization_not_allowed",
            )
        try:
            arguments = CosmosArguments.model_validate(authorization.normalized_arguments)
            data, media_type, request_id = await self.client.generate(arguments)
            if context.is_security_compromised():
                raise CosmosError("security_compromised", request_id)
            from aegis_core.async_tasks import run_blocking_owned

            artifact = await run_blocking_owned(self.store.put, data, media_type)
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=COSMOS_TOOL,
                success=True,
                output=artifact.model_dump_json(),
                metadata={
                    "verified": True,
                    "source": "nvidia_cosmos",
                    "model_id": artifact.model_id,
                    "request_id": request_id or "",
                    "artifact_token": artifact.token,
                },
            )
        except CosmosError as error:
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=COSMOS_TOOL,
                success=False,
                error_code=error.code,
                metadata={
                    "request_id": error.request_id or "",
                    "http_status": error.status_code or 0,
                },
            )
        except (GeneratedMediaError, OSError, ValueError) as error:
            code = (
                str(error) if isinstance(error, GeneratedMediaError) else "cosmos_response_invalid"
            )
            return ToolExecutionResult(
                call_id=authorization.call_id, tool_name=COSMOS_TOOL, success=False, error_code=code
            )
