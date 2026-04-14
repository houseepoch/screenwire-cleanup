from __future__ import annotations

from pathlib import Path

from llm.xai_client import XAIClient


class _FakeCompletions:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)

        class _Message:
            content = '{"description":"aligned"}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


def test_generate_json_with_image_builds_multimodal_request(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    client = XAIClient(api_key="test-key")
    fake = _FakeCompletions()
    client.client.chat.completions = fake

    import asyncio

    result = asyncio.run(
        client.generate_json_with_image(
            image_path=image_path,
            prompt="Align this entity to the uploaded reference.",
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"description": {"type": "string"}},
                "required": ["description"],
            },
            system_prompt="Return concise JSON only.",
            model="grok-4-1-fast-reasoning",
            task_hint="entity_review_alignment",
        )
    )

    assert result == {"description": "aligned"}
    request = fake.requests[0]
    user_content = request["messages"][1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user_content[1]["type"] == "text"
