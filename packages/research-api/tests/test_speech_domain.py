from __future__ import annotations

import pytest
from helpers import make_wav

from research_api.domain.errors import InvalidInputError
from research_api.domain.speech import EMPTY_CLIP, WRONG_TYPE, clip_seconds, is_wav_type


def test_a_clip_is_measured_in_seconds() -> None:
    assert clip_seconds(make_wav(1.5), max_s=60) == 1.5


def test_a_clip_at_the_limit_passes_and_one_over_it_does_not() -> None:
    assert clip_seconds(make_wav(60), max_s=60) == 60
    with pytest.raises(InvalidInputError, match="longer than 60 seconds"):
        clip_seconds(make_wav(60.5), max_s=60)


@pytest.mark.parametrize(
    "clip",
    [
        make_wav(1, rate=44_100),
        make_wav(1, channels=2),
        make_wav(1, width=1),
        b"RIFF",
        b"not audio at all",
    ],
)
def test_anything_but_16_khz_mono_16_bit_pcm_is_refused(clip: bytes) -> None:
    with pytest.raises(InvalidInputError) as error:
        clip_seconds(clip, max_s=60)
    assert error.value.detail == WRONG_TYPE


def test_a_body_of_zero_bytes_is_called_empty_not_wrong_format() -> None:
    with pytest.raises(InvalidInputError) as error:
        clip_seconds(b"", max_s=60)
    assert error.value.detail == EMPTY_CLIP


def test_a_clip_with_no_sound_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="no sound"):
        clip_seconds(make_wav(0), max_s=60)


def test_a_header_that_promises_more_than_the_body_holds_counts_what_is_there() -> None:
    clip = make_wav(2)
    assert clip_seconds(clip[: 44 + 16_000 * 2], max_s=60) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("audio/wav", True),
        ("Audio/X-WAV; charset=binary", True),
        (" audio/wave ", True),
        ("audio/webm", False),
        ("application/octet-stream", False),
        ("", False),
    ],
)
def test_the_wav_types(value: str, expected: bool) -> None:
    assert is_wav_type(value) is expected
