import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.openai_service import OpenAIService


def make_service_with_image_response(image_data):
    """Builds an OpenAI service with a mocked image API response."""
    service = OpenAIService.__new__(OpenAIService)
    service.image_model = "gpt-image-1-mini"
    service.image_quality = "medium"
    service.client = MagicMock()
    service.client.images.generate.return_value = SimpleNamespace(data=[image_data])
    return service


def test_generate_image_decodes_base64_response():
    """GPT Image base64 responses become named in-memory PNG files."""
    expected = b"fake-png-content"
    image_data = SimpleNamespace(
        b64_json=base64.b64encode(expected).decode("ascii"),
        url=None,
    )
    service = make_service_with_image_response(image_data)

    image_file = asyncio.run(service.generate_image("Create a flyer"))

    assert image_file.name == "btcgdl-flyer.png"
    assert image_file.read() == expected


def test_generate_image_returns_url_response():
    """URL-based image responses remain supported."""
    image_data = SimpleNamespace(
        b64_json=None,
        url="https://example.com/flyer.png",
    )
    service = make_service_with_image_response(image_data)

    result = asyncio.run(service.generate_image("Create a flyer"))

    assert result == "https://example.com/flyer.png"
