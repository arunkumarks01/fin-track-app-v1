from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import LOG_FORMAT, MASK, SECRETS, MaskingFormatter, mask

SECRET_TOKEN = "s3cr3t-token-value-abcdef0123456789"


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(MaskingFormatter(LOG_FORMAT))
    logger = logging.getLogger("test.masking")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    yield logger, stream
    logger.handlers.clear()


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("connect postgresql://fintrack_v2:hunter2pass@localhost:5433/db", "hunter2pass"),
        ("Authorization: Bearer abc.DEF-123_xyz", "abc.DEF-123_xyz"),
        ("calling with api_key=kiteKEY123 and api_secret=kiteSECRET9", "kiteKEY123"),
        ("calling with api_key=kiteKEY123 and api_secret=kiteSECRET9", "kiteSECRET9"),
        ('{"access_token": "tok_ABC987", "x": 1}', "tok_ABC987"),
        ("password: pa55word!", "pa55word!"),
        ("request_token=rqTOKEN42&status=success", "rqTOKEN42"),
    ],
)
def test_pattern_masking(raw: str, leak: str) -> None:
    out = mask(raw, secrets=())
    assert leak not in out
    assert MASK in out


def test_registered_secret_masked_in_message_args_and_traceback(
    capture: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = capture
    SECRETS.register([SECRET_TOKEN])
    logger.info("token is %s", SECRET_TOKEN)
    try:
        raise RuntimeError(f"failed using {SECRET_TOKEN}")
    except RuntimeError:
        logger.exception("boom")
    out = stream.getvalue()
    assert SECRET_TOKEN not in out
    assert out.count(MASK) >= 2
    assert "RuntimeError" in out  # traceback still useful


def test_short_values_not_registered() -> None:
    SECRETS.register(["ab", ""])
    assert SECRETS.snapshot() == frozenset()


def test_ordinary_text_untouched() -> None:
    text = "ingested 250 bars for RELIANCE 1d"
    assert mask(text, secrets=()) == text
