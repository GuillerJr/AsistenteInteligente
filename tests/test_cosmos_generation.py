from __future__ import annotations

import asyncio
import base64
import io
import json
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    ImageInput,
    InputModality,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    UserRequest,
)
from aegis_core.generated_media import GeneratedMediaError, GeneratedMediaStore, media_tokens
from aegis_core.job_interactions import confirmation_preview
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.orchestration.graph import (
    _request_may_need_tools,
    _route_request,
    _tool_names_for_request,
    build_swarm_graph,
)
from aegis_core.providers.cosmos import (
    COSMOS_TOOL,
    COSMOS_URL,
    CosmosArguments,
    CosmosClient,
    CosmosError,
    CosmosToolService,
    bind_cosmos_call,
    cosmos_definition,
    wants_generated_media,
)
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (48, 48), "green").save(output, format="JPEG")
    return output.getvalue()


def mp4():
    def box(kind, data):
        return (8 + len(data)).to_bytes(4, "big") + kind + data

    return box(b"ftyp", b"isom\0\0\0\0isom") + box(b"moov", b"test") + box(b"mdat", b"test")


def call(mode="text2image"):
    return ToolCall(
        call_id=str(uuid4()),
        tool_name=COSMOS_TOOL,
        requested_by=AgentRole.PLANNER,
        arguments={"model_mode": mode, "prompt": "A green robot in a laboratory"},
    )


@pytest.mark.parametrize(
    "text",
    [
        "Genera una imagen de un robot",
        "Haz un video corto",
        "Crea una ilustración de un laboratorio",
        "Puedes generar una imagen?",
        "Animate this image",
        "Generate a picture of a robot",
        "Dibuja una imagen",
        "Hazme una imagen de un robot",
        "Puedes hacer un video corto?",
    ],
)
def test_generation_is_scoped_to_planner_tool(text):
    request = UserRequest(text=text)
    assert wants_generated_media(request)
    assert _request_may_need_tools(request)
    assert COSMOS_TOOL in _tool_names_for_request(request)
    assert _route_request(request).role == AgentRole.PLANNER


@pytest.mark.parametrize(
    "text",
    [
        "No generes una imagen",
        "Describe esta imagen",
        'Explica "genera una imagen"',
        "Analiza el video adjunto",
        "Calcula 2 + 2",
    ],
)
def test_mentions_and_quoted_payload_are_not_generation(text):
    assert not wants_generated_media(UserRequest(text=text))


def test_real_attachment_is_bound_before_digest_and_not_offered_in_schema():
    image = ImageInput(media_type="image/jpeg", data_base64=base64.b64encode(jpeg()).decode())
    request = UserRequest(
        text="Anima esta imagen",
        image=image,
        modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
    )
    bound = bind_cosmos_call(call("image2video"), request)
    assert CosmosArguments.model_validate(bound.arguments).image == image
    assert _route_request(request).role == AgentRole.PLANNER
    assert (
        "image" not in cosmos_definition().openai_schema()["function"]["parameters"]["properties"]
    )
    forged = call("image2video").model_copy(
        update={
            "arguments": {
                "model_mode": "image2video",
                "prompt": "test",
                "image": image.model_dump(),
            }
        }
    )
    with pytest.raises(ValidationError):
        CosmosArguments.model_validate(bind_cosmos_call(forged, request).arguments)


def test_generation_requires_one_time_confirmation_and_bounded_preview(tmp_path):
    broker = build_default_tool_broker((cosmos_definition(),))
    authorization = broker.authorize(call(), PolicyContext(workspace_root=tmp_path))
    assert authorization.decision == PolicyDecision.REQUIRE_CONFIRMATION
    assert JobToolPresenter(broker).supports_confirmation(COSMOS_TOOL)
    assert "NVIDIA Cosmos3 Nano" in JobToolPresenter(broker).confirmation_summary(authorization)
    assert len(confirmation_preview(authorization).model_dump_json()) < 24_000


@pytest.mark.parametrize(
    "mode,field,mime,data",
    [
        ("text2image", "b64_image", "image/jpeg", jpeg()),
        ("text2video", "b64_video", "video/mp4", mp4()),
    ],
)
async def test_success_calls_correct_endpoint_and_publishes_original(
    tmp_path, mode, field, mime, data
):
    seen = []

    async def endpoint(request):
        seen.append(request)
        value = json.loads(request.content)
        assert value["model_mode"] == mode
        assert ("num_frames" in value) == (mode != "text2image")
        assert "image" not in value
        return httpx.Response(200, json={field: base64.b64encode(data).decode()})

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    store = GeneratedMediaStore(tmp_path / "media")
    tool = CosmosToolService(client, store)
    authorization = ToolAuthorization(
        call_id="test-call",
        tool_name=COSMOS_TOOL,
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments=CosmosArguments(model_mode=mode, prompt="A green robot").model_dump(),
    )
    try:
        result = await tool.execute(authorization, PolicyContext(workspace_root=tmp_path))
        assert result.success and result.metadata["verified"]
        text = JobToolPresenter(None).format_result(result, authorization)
        assert len(media_tokens(text)) == 1
        descriptor, saved = store.read(media_tokens(text)[0])
        assert descriptor.media_type == mime and saved == data
        assert len(seen) == 1 and str(seen[0].url) == COSMOS_URL
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "cosmos_authentication_failed"),
        (403, "cosmos_authentication_failed"),
        (404, "cosmos_model_unavailable"),
        (410, "cosmos_model_unavailable"),
        (429, "cosmos_rate_limited"),
        (422, "cosmos_request_rejected"),
        (500, "cosmos_unavailable"),
    ],
)
async def test_failures_do_not_retry_or_leak_provider_body(status, code):
    requests = []

    async def endpoint(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "PRIVATE-BODY"})

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    try:
        with pytest.raises(CosmosError) as error:
            await client.generate(CosmosArguments(model_mode="text2image", prompt="A green robot"))
        assert error.value.code == code and "PRIVATE-BODY" not in str(error.value)
        assert len(requests) == 1
    finally:
        await client.close()


async def test_polling_retrieves_only_the_same_request_and_never_resubmits():
    identifier = str(uuid4())
    requests = []

    async def endpoint(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"nvcf-reqid": identifier})
        assert request.url.host == "api.nvcf.nvidia.com" and request.url.path.endswith(identifier)
        return httpx.Response(200, json={"b64_image": base64.b64encode(jpeg()).decode()})

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    try:
        raw, mime, request_id = await client.generate(
            CosmosArguments(model_mode="text2image", prompt="A green robot")
        )
        assert mime == "image/jpeg" and request_id == identifier and raw == jpeg()
        assert [r.method for r in requests] == ["POST", "GET"]
    finally:
        await client.close()


async def test_redirect_never_sends_token_to_a_provider_supplied_url():
    requests = []

    async def endpoint(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/result"})

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    try:
        with pytest.raises(CosmosError):
            await client.generate(CosmosArguments(model_mode="text2image", prompt="A green robot"))
        assert len(requests) == 1 and requests[0].url.host == "ai.api.nvidia.com"
    finally:
        await client.close()


async def test_cancelled_generation_is_not_replayed_and_releases_slot():
    started = asyncio.Event()

    async def endpoint(request):
        started.set()
        await asyncio.Future()

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    task = asyncio.create_task(
        client.generate(CosmosArguments(model_mode="text2image", prompt="A green robot"))
    )
    await started.wait()
    with pytest.raises(CosmosError, match="cosmos_busy"):
        await client.generate(CosmosArguments(model_mode="text2image", prompt="A second robot"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client._busy.locked()
    await client.close()


@pytest.mark.parametrize("change", ["tamper", "symlink", "permissions", "traversal"])
def test_private_original_store_rejects_unsafe_reads(tmp_path, change):
    store = GeneratedMediaStore(tmp_path / "media")
    artifact = store.put(jpeg(), "image/jpeg")
    path = store.root / artifact.file_name
    token = artifact.token
    if change == "tamper":
        path.write_bytes(b"x" * artifact.byte_count)
    if change == "permissions":
        path.chmod(0o644)
    if change == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "private-file")
    if change == "traversal":
        token = "../../private-file"
    with pytest.raises((GeneratedMediaError, OSError, ValueError)):
        store.read(token)


def test_store_limits_and_corrupt_media_do_not_publish_files(tmp_path):
    store = GeneratedMediaStore(tmp_path / "media", max_files=1)
    with pytest.raises((GeneratedMediaError, OSError)):
        store.put(b"not-an-image-or-video", "image/jpeg")
    store.put(jpeg(), "image/jpeg")
    with pytest.raises(GeneratedMediaError, match="capacity"):
        store.put(jpeg(), "image/jpeg")
    with pytest.raises(GeneratedMediaError):
        store.put(b"\0\0\0\x18ftypfake-is-not-video", "video/mp4")


async def test_unapproved_generation_never_reaches_nvidia(tmp_path):
    async def forbidden(request):
        pytest.fail("unapproved generation reached NVIDIA")

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(forbidden))
    tool = CosmosToolService(client, GeneratedMediaStore(tmp_path / "media"))
    authorization = ToolAuthorization(
        call_id="pending",
        tool_name=COSMOS_TOOL,
        call_digest="a" * 64,
        decision=PolicyDecision.REQUIRE_CONFIRMATION,
        reason_code="confirmation_required",
        normalized_arguments=call().arguments,
    )
    try:
        result = await tool.execute(authorization, PolicyContext(workspace_root=tmp_path))
        assert not result.success and result.error_code == "authorization_not_allowed"
    finally:
        await client.close()


@pytest.mark.parametrize("value", [{}, {"b64_image": "!!!"}, {"b64_image": 77}])
async def test_invalid_generation_payload_is_not_published(value):
    async def endpoint(request):
        return httpx.Response(200, json=value)

    client = CosmosClient(lambda: "test-key", transport=httpx.MockTransport(endpoint))
    try:
        with pytest.raises(CosmosError, match="cosmos_response_invalid"):
            await client.generate(CosmosArguments(model_mode="text2image", prompt="A robot"))
    finally:
        await client.close()


async def test_graph_offers_cosmos_and_waits_without_execution(tmp_path):
    class Brain:
        async def complete_nvidia_only(self, *, role, **kwargs):
            assert role == AgentRole.PLANNER
            schemas = kwargs.get("extra_body", {}).get("tools", kwargs.get("tools", []))
            assert any(s["function"]["name"] == COSMOS_TOOL for s in schemas)
            return AgentResult(role=role, model_id="test/remote", content="", tool_calls=(call(),))

    broker = build_default_tool_broker((cosmos_definition(),))
    graph = build_swarm_graph(
        Brain(),
        inference_nvidia_only=True,
        tool_broker=broker,
        policy_context=PolicyContext(workspace_root=tmp_path),
        tool_executor=ReadOnlyToolExecutor(),
    )
    value = await graph.ainvoke({"request": UserRequest(text="Genera una imagen de un robot")})
    assert value["tool_authorizations"][0].decision == PolicyDecision.REQUIRE_CONFIRMATION
    assert not value.get("tool_results")
