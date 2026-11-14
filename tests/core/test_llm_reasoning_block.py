"""A reasoning model served without a reasoning parser prints its chain of
thought in `content`, inside `<think>…</think>`, before the answer. `chat` and
`chat_json` drop that block, so a translation does not carry the model's
reasoning and a JSON answer after it parses."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from codify.core.llm import OpenAICompatibleClient, _strip_reasoning
from codify.translate.translate import translate_title


def _resp(content: str | None) -> MagicMock:
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


@pytest.fixture
def client() -> OpenAICompatibleClient:
    c = OpenAICompatibleClient(base_url="http://x/v1", api_key="k", model="deepseek-r1")
    c.client = MagicMock()
    c.client.chat = MagicMock()
    c.client.chat.completions = MagicMock()
    c.client.chat.completions.create = AsyncMock()
    return c


async def test_chat_drops_a_leading_reasoning_block(client: OpenAICompatibleClient) -> None:
    client.client.chat.completions.create.return_value = _resp(
        "<think>\nThe title names a tax act.\n</think>\n\nIncome Tax Act"
    )
    assert await client.chat("translate this title") == "Income Tax Act"


async def test_chat_keeps_an_answer_without_a_reasoning_block(
    client: OpenAICompatibleClient,
) -> None:
    client.client.chat.completions.create.return_value = _resp("  Income Tax Act\n")
    # Unchanged: callers strip their own whitespace.
    assert await client.chat("translate this title") == "  Income Tax Act\n"


async def test_a_translated_title_carries_no_reasoning(client: OpenAICompatibleClient) -> None:
    client.client.chat.completions.create.return_value = _resp(
        '<think>\n"Tuloverolaki" is the Income Tax Act.\n</think>\n\n"Income Tax Act"'
    )
    title = await translate_title("Tuloverolaki", target_language="en", llm=client)
    assert title == "Income Tax Act"


async def test_chat_json_parses_after_a_leading_reasoning_block(
    client: OpenAICompatibleClient,
) -> None:
    client.client.chat.completions.create.return_value = _resp(
        '<think>Two units, both sections.</think>{"units": 2}'
    )
    assert await client.chat_json("count the units") == {"units": 2}


async def test_chat_json_refuses_a_reply_of_reasoning_alone(client: OpenAICompatibleClient) -> None:
    client.client.chat.completions.create.return_value = _resp("<think>Two units.</think>")
    # Invalid, not an empty object; with no answer, the error shows the reply.
    with capture_logs() as logs, pytest.raises(ValueError) as raised:
        await client.chat_json("count the units")
    assert str(raised.value) == "Model returned invalid JSON: <think>Two units.</think>"
    assert [e["preview"] for e in logs if e["event"] == "invalid_json"] == [
        "<think>Two units.</think>"
    ]


async def test_chat_json_names_the_answer_that_failed(client: OpenAICompatibleClient) -> None:
    # A long block would otherwise fill the preview and hide the broken answer.
    client.client.chat.completions.create.return_value = _resp(
        "<think>" + "Count the units. " * 50 + '</think>{"units": 2'
    )
    with capture_logs() as logs, pytest.raises(ValueError) as raised:
        await client.chat_json("count the units")
    assert str(raised.value) == 'Model returned invalid JSON: {"units": 2'
    assert [e["preview"] for e in logs if e["event"] == "invalid_json"] == ['{"units": 2']


async def test_chat_json_still_reads_no_content_as_an_empty_object(
    client: OpenAICompatibleClient,
) -> None:
    client.client.chat.completions.create.return_value = _resp(None)
    assert await client.chat_json("count the units") == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<think>a</think>answer", "answer"),
        ("\n  <think>\na\n</think>\n\nanswer", "answer"),
        ("<think></think>answer", "answer"),
        # A tag inside the reasoning does not end it early.
        ("<think>is <b> a tag?</think>answer", "answer"),
        # Only the first closing tag ends the block; a later one is the answer's.
        ("<think>a</think>x</think>y", "x</think>y"),
        # Only a leading block: one inside the answer is the answer's text.
        ("answer <think>a</think>", "answer <think>a</think>"),
        # Unclosed, as when the reply is cut off at the token limit: left whole,
        # so what came back is shown rather than read as an empty answer.
        ("<think>still reasoning", "<think>still reasoning"),
        ("", ""),
    ],
)
def test_strip_reasoning(text: str, expected: str) -> None:
    assert _strip_reasoning(text) == expected
