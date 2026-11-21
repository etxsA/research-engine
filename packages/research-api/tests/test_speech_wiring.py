"""``[speech]`` in the settings, the key file, and a live container with and without it."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import live_settings, prepare_live, write_config

from research_api.adapters.models import OpenAiSynthesizer, OpenAiTranscriber
from research_api.bootstrap import Settings, SettingsError, build_container, load_settings
from research_api.bootstrap.storage import read_secret
from research_api.domain.speech import SpeechLimits
from research_api.domain.synthesis import SynthesisLimits, Voices

PACKAGE = Path(__file__).resolve().parents[1]
ENDPOINT = "http://127.0.0.1:8004/v1"
TTS = "http://127.0.0.1:8005/v1"


def section(tmp_path: Path, extra: str = "") -> Settings:
    key = tmp_path / "stt.key"
    key.write_text("k-123\n")
    key.chmod(0o600)
    return load_settings(
        write_config(
            tmp_path / "s.toml",
            f'[speech]\nstt_endpoint = "{ENDPOINT}"\nstt_api_key_file = "{key}"\n{extra}',
        )
    )


def test_a_speech_section_has_the_defaults_of_the_scope(tmp_path: Path) -> None:
    speech = section(tmp_path).speech
    assert speech is not None
    assert speech.stt_model == "local-stt"
    assert speech.limits() == SpeechLimits(
        max_audio_bytes=2 * 1024 * 1024, max_audio_s=60, timeout_s=30, per_member_open=2
    )


def test_without_the_section_there_is_no_speech() -> None:
    assert Settings.defaults().speech is None


def test_the_example_file_documents_the_section() -> None:
    text = (PACKAGE / "config.example.toml").read_text()
    for key in (
        "stt_endpoint",
        "stt_model",
        "stt_api_key_file",
        "max_audio_bytes",
        "max_audio_s",
        "timeout_s",
        "per_member_open",
        "tts_endpoint",
        "tts_model",
        "tts_api_key_file",
        "tts_voice_es",
        "tts_voice_en",
        "max_tts_chars",
        "tts_timeout_s",
    ):
        assert key in text


def test_every_limit_can_be_set(tmp_path: Path) -> None:
    speech = section(
        tmp_path,
        'stt_model = "m"\nmax_audio_bytes = 4096\nmax_audio_s = 5\ntimeout_s = 1.5\n'
        "per_member_open = 3\n",
    ).speech
    assert speech is not None
    assert speech.stt_model == "m"
    assert speech.limits() == SpeechLimits(4096, 5.0, 1.5, 3)


@pytest.mark.parametrize(
    "line",
    [
        "max_audio_bytes = 10",
        "max_audio_bytes = 99999999999",
        "max_audio_s = 0",
        "max_audio_s = 1000",
        "timeout_s = 0",
        "per_member_open = 0",
        "per_member_open = 11",
        'stt_model = ""',
        "typo_key = 1",
    ],
)
def test_a_bad_value_or_a_mistyped_key_is_refused_by_name(tmp_path: Path, line: str) -> None:
    with pytest.raises(SettingsError, match=line.split(" ", maxsplit=1)[0]):
        section(tmp_path, line + "\n")


@pytest.mark.parametrize(
    ("endpoint", "reason"),
    [
        ("http://stt.example.com/v1", "plain http"),
        ("ftp://127.0.0.1/v1", "must look like"),
        ("http://user:pw@127.0.0.1/v1", "no credentials"),
        ("http://127.0.0.1/v1?key=1", "no credentials"),
    ],
)
def test_the_endpoint_follows_the_rules_of_a_model_server(
    tmp_path: Path, endpoint: str, reason: str
) -> None:
    with pytest.raises(SettingsError, match=reason):
        load_settings(
            write_config(
                tmp_path / "s.toml",
                f'[speech]\nstt_endpoint = "{endpoint}"\nstt_api_key_file = "/k"\n',
            )
        )


def test_https_to_another_host_and_a_tailscale_address_are_accepted(tmp_path: Path) -> None:
    for endpoint in ("https://stt.example.com/v1", "http://100.64.0.7:8004/v1"):
        text = f'[speech]\nstt_endpoint = "{endpoint}"\nstt_api_key_file = "/k"\n'
        assert load_settings(write_config(tmp_path / "s.toml", text)).speech is not None


def test_an_endpoint_and_its_key_file_go_together_and_the_path_is_absolute(
    tmp_path: Path,
) -> None:
    for text in (
        '[speech]\nstt_endpoint = "http://127.0.0.1:8004/v1"\n',
        '[speech]\nstt_api_key_file = "/k"\n',
        '[speech]\ntts_endpoint = "http://127.0.0.1:8005/v1"\n',
        '[speech]\ntts_api_key_file = "/k"\n',
        f'[speech]\ntts_endpoint = "{TTS}"\ntts_api_key_file = "relative.key"\n',
        f'[speech]\nstt_endpoint = "{ENDPOINT}"\nstt_api_key_file = "relative.key"\n',
    ):
        with pytest.raises(SettingsError):
            load_settings(write_config(tmp_path / "s.toml", text))


def test_a_key_is_read_from_a_private_file_and_stripped(tmp_path: Path) -> None:
    key = tmp_path / "k"
    key.write_text("  abc123\n")
    key.chmod(0o600)
    assert read_secret(key, what="the key") == "abc123"


def test_a_key_file_open_to_others_is_refused(tmp_path: Path) -> None:
    key = tmp_path / "k"
    key.write_text("abc123")
    key.chmod(0o644)
    with pytest.raises(SettingsError, match="chmod 600"):
        read_secret(key, what="the key")


@pytest.mark.parametrize("content", ["", "  \n", "two words", "a\nb"])
def test_a_key_file_must_hold_one_key(tmp_path: Path, content: str) -> None:
    key = tmp_path / "k"
    key.write_text(content)
    key.chmod(0o600)
    with pytest.raises(SettingsError, match="one key") as error:
        read_secret(key, what="the key")
    assert content.strip() not in str(error.value) or not content.strip()


def test_a_missing_key_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="does not exist"):
        read_secret(tmp_path / "none", what="the key")


def test_a_live_container_without_the_section_has_no_speech(tmp_path: Path) -> None:
    container = build_container(prepare_live(live_settings(tmp_path)), cheap_hashing=True)
    try:
        assert container.speech is None
    finally:
        container.close()


def test_a_live_container_with_the_section_talks_to_the_speech_server(tmp_path: Path) -> None:
    key = tmp_path / "stt.key"
    key.write_text("k-123")
    key.chmod(0o600)
    settings = prepare_live(
        live_settings(
            tmp_path,
            speech={"stt_endpoint": ENDPOINT, "stt_api_key_file": key, "per_member_open": 1},
        )
    )
    container = build_container(settings, cheap_hashing=True)
    try:
        assert container.speech is not None
        assert container.speech.limits.per_member_open == 1
        assert isinstance(container.speech._transcriber, OpenAiTranscriber)
    finally:
        container.close()


def test_a_live_start_with_a_loose_key_file_stops(tmp_path: Path) -> None:
    key = tmp_path / "stt.key"
    key.write_text("k-123")
    key.chmod(0o644)
    settings = prepare_live(
        live_settings(tmp_path, speech={"stt_endpoint": ENDPOINT, "stt_api_key_file": key})
    )
    with pytest.raises(SettingsError, match="chmod 600"):
        build_container(settings, cheap_hashing=True)


def test_the_reading_keys_have_their_defaults(tmp_path: Path) -> None:
    speech = section(tmp_path).speech
    assert speech is not None
    assert speech.tts_endpoint is None and speech.tts_api_key_file is None
    assert speech.tts_model == "local-tts"
    assert speech.synthesis_limits() == SynthesisLimits(400, 30.0, 2)
    assert speech.voices() == Voices("default", "default")


def test_every_reading_key_can_be_set(tmp_path: Path) -> None:
    key = tmp_path / "tts.key"
    key.write_text("k")
    text = (
        f'[speech]\ntts_endpoint = "{TTS}"\ntts_api_key_file = "{key}"\ntts_model = "m"\n'
        'tts_voice_es = "ana"\ntts_voice_en = "bo"\nmax_tts_chars = 900\ntts_timeout_s = 5\n'
        "per_member_open = 3\n"
    )
    speech = load_settings(write_config(tmp_path / "s.toml", text)).speech
    assert speech is not None and speech.stt_endpoint is None
    assert speech.tts_model == "m"
    assert speech.synthesis_limits() == SynthesisLimits(900, 5.0, 3)
    assert speech.voices() == Voices("ana", "bo")


@pytest.mark.parametrize(
    "line", ["max_tts_chars = 0", "max_tts_chars = 2001", "tts_timeout_s = 0", "tts_voice_es = ''"]
)
def test_the_reading_limits_are_bounded(tmp_path: Path, line: str) -> None:
    key = tmp_path / "tts.key"
    key.write_text("k")
    text = f'[speech]\ntts_endpoint = "{TTS}"\ntts_api_key_file = "{key}"\n{line}\n'
    with pytest.raises(SettingsError):
        load_settings(write_config(tmp_path / "s.toml", text))


def test_a_reading_endpoint_follows_the_rules_of_any_endpoint(tmp_path: Path) -> None:
    for endpoint in ("http://tts.example.com/v1", "http://127.0.0.1:8005/v1?k=1"):
        text = f'[speech]\ntts_endpoint = "{endpoint}"\ntts_api_key_file = "/k"\n'
        with pytest.raises(SettingsError):
            load_settings(write_config(tmp_path / "s.toml", text))


def test_a_live_container_with_only_the_reading_half_has_no_transcription(
    tmp_path: Path,
) -> None:
    key = tmp_path / "tts.key"
    key.write_text("k-123")
    key.chmod(0o600)
    settings = prepare_live(
        live_settings(
            tmp_path,
            speech={
                "tts_endpoint": TTS,
                "tts_api_key_file": key,
                "tts_voice_es": "ana",
                "max_tts_chars": 100,
            },
        )
    )
    container = build_container(settings, cheap_hashing=True)
    try:
        assert container.speech is None
        assert container.synthesis is not None
        assert container.synthesis.limits.max_chars == 100
        assert container.synthesis.voices.es == "ana"
        assert isinstance(container.synthesis._synthesizer, OpenAiSynthesizer)
    finally:
        container.close()


def test_a_live_container_with_only_the_transcription_half_has_no_reading(
    tmp_path: Path,
) -> None:
    key = tmp_path / "stt.key"
    key.write_text("k-123")
    key.chmod(0o600)
    settings = prepare_live(
        live_settings(tmp_path, speech={"stt_endpoint": ENDPOINT, "stt_api_key_file": key})
    )
    container = build_container(settings, cheap_hashing=True)
    try:
        assert container.speech is not None and container.synthesis is None
    finally:
        container.close()


def test_a_live_start_with_a_loose_reading_key_file_stops(tmp_path: Path) -> None:
    key = tmp_path / "tts.key"
    key.write_text("k-123")
    key.chmod(0o644)
    settings = prepare_live(
        live_settings(tmp_path, speech={"tts_endpoint": TTS, "tts_api_key_file": key})
    )
    with pytest.raises(SettingsError, match="chmod 600"):
        build_container(settings, cheap_hashing=True)
