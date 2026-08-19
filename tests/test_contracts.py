import base64

import pytest
from pydantic import ValidationError

from aegis_core.contracts import (
    MAX_IMAGE_BYTES,
    AgentRole,
    ImageInput,
    InputModality,
    RiskLevel,
    RouteDecision,
    UserRequest,
)


def test_request_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        UserRequest(text="")


def test_route_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RouteDecision(
            role=AgentRole.PLANNER,
            risk=RiskLevel.LOW,
            reason="normal request",
            unexpected=True,
        )


def test_image_request_accepts_bounded_canonical_png() -> None:
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode("ascii")
    image = ImageInput(media_type="image/png", data_base64=encoded)

    request = UserRequest(
        text="Describe la imagen",
        modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
        image=image,
    )

    assert request.image is image
    assert image.data_uri == f"data:image/png;base64,{encoded}"


@pytest.mark.parametrize(
    ("media_type", "data"),
    [
        ("image/gif", b"GIF89a"),
        ("image/png", b"not-a-png"),
        ("image/jpeg", b"not-a-jpeg"),
        ("image/webp", b"RIFF0000NOPE"),
    ],
)
def test_image_rejects_unsupported_or_mismatched_content(
    media_type: str,
    data: bytes,
) -> None:
    with pytest.raises(ValidationError):
        ImageInput(
            media_type=media_type,
            data_base64=base64.b64encode(data).decode("ascii"),
        )


def test_image_rejects_invalid_or_oversized_base64() -> None:
    with pytest.raises(ValidationError):
        ImageInput(media_type="image/png", data_base64="not+canonical===")
    with pytest.raises(ValidationError):
        ImageInput(
            media_type="image/png",
            data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES).decode(
                "ascii"
            ),
        )


def test_image_attachment_and_modality_are_atomic() -> None:
    image = ImageInput(
        media_type="image/png",
        data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode("ascii"),
    )
    with pytest.raises(ValidationError):
        UserRequest(text="Describe", image=image)
    with pytest.raises(ValidationError):
        UserRequest(
            text="Describe",
            modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
        )
