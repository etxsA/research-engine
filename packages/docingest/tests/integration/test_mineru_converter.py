"""The MinerU adapter and ``ingest_async`` against a fake ``mineru-kit`` (tests/fixtures/mineru)."""

import asyncio
import functools
import json
import os
import shutil
import stat
import threading
import time
from pathlib import Path

import pytest
from builders import text_pdf
from fakes import (
    FakeConverter,
    FakeImages,
    InMemoryStore,
    blank,
    digital,
    make_service,
    scanned,
)
from typer.testing import CliRunner

from docingest.adapters.converters import mineru as mineru_module
from docingest.adapters.converters.mineru import MineruConverter
from docingest.application.ingest import IngestOptions
from docingest.config import MineruConfig
from docingest.domain.errors import ConversionError, InvalidConfigError, NotConfiguredError
from docingest.domain.models import PageMethod, SourceKind, SourceMetadata
from docingest.domain.text import split_pages
from docingest.entrypoints.cli import app
from docingest.ports import Conversion, MultimodalRequest, Segment


def aio(test):
    """Run an ``async def`` test to completion (the suite has no asyncio plugin)."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return run


FAKE = Path(__file__).parent.parent / "fixtures" / "mineru" / "fake_mineru.py"
MODEL = "MinerU2.5-Pro-2605-1.2B"
PAGES = "# Intro\n\nfirst page text\fsecond page text\f\fthird page text"  # page 3 is empty
TEXT = "Attention is all you need. " * 10


@pytest.fixture
def command(tmp_path, monkeypatch):
    """The fake executable (a copy: the tests own it) and where it logs its calls."""
    exe = tmp_path / "bin" / "mineru-kit"
    exe.parent.mkdir()
    shutil.copy(FAKE, exe)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_MINERU_LOG", str(tmp_path / "calls.jsonl"))
    return exe


def calls(tmp_path) -> list[dict]:
    log = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def option(call: dict, name: str) -> str:
    return call["argv"][call["argv"].index(name) + 1]


def config(tmp_path, exe, **kw) -> MineruConfig:
    values = {"command": str(exe), "model": MODEL, "output_dir": str(tmp_path / "work")}
    return MineruConfig(**{**values, **kw})


def service(tmp_path, command, docs, *, converters=None, **kw):
    conv = MineruConverter(config(tmp_path, command, **kw))
    return make_service(docs, converters=converters, multimodal=conv, images=FakeImages(2))


def request(pages=1, ocr=True, doc_id="d" * 64):
    return MultimodalRequest(doc_id, pages, ocr)


# ------------------------------------------------------------------ pages (R3)
@aio
async def test_one_segment_per_page_and_empty_pages_are_kept(tmp_path, command, src):
    path = src("paper.pdf", PAGES.encode())
    svc = service(tmp_path, command, {"paper.pdf": [digital("x")] * 4})
    doc = await svc.ingest_async(path)
    pages = split_pages(svc.store.markdown(doc))
    assert sorted(pages) == [1, 2, 3, 4]  # the page numbers of the PDF
    assert "# Intro" in pages[1] and "first page text" in pages[1]
    assert pages[2] == "second page text" and pages[3] == "" and "third" in pages[4]
    assert doc.manifest.n_pages == doc.manifest.source_pages == 4
    assert {p.method for p in doc.manifest.pages} == {PageMethod.MINERU}
    assert doc.manifest.config_hash and doc.manifest.pages[0].engine.startswith("mineru 4.0.12")


@aio
async def test_a_page_the_document_does_not_have_is_a_conversion_error(
    tmp_path, command, src, monkeypatch
):
    monkeypatch.setenv("FAKE_MINERU_MODE", "extra")
    conv = MineruConverter(config(tmp_path, command))
    with pytest.raises(ConversionError, match="page 1"):
        await conv.convert_async(src("a.pdf", b"text"), request(pages=1))


@aio
async def test_images_are_converted_with_one_segment_per_frame(tmp_path, command, src):
    svc = service(tmp_path, command, {})
    doc = await svc.ingest_async(src("scan.png", b"one\ftwo"))
    assert doc.manifest.source_kind == SourceKind.IMAGE and doc.manifest.n_pages == 2
    assert option(calls(tmp_path)[-1], "--ocr-mode") == "ocr"


# ------------------------------------------------------ the command line (R6, R7)
@aio
async def test_the_command_is_an_argument_list_with_tier_and_mode(tmp_path, command, src):
    conv = MineruConverter(config(tmp_path, command, tier="advanced"))
    await conv.convert_async(src("a.pdf", b"text"), request(ocr=False))
    (call,) = calls(tmp_path)
    assert call["argv"][0] == "parse" and call["argv"][1].endswith("a.pdf")
    assert option(call, "--format") == "zip" and option(call, "--tier") == "advanced"
    assert option(call, "--ocr-mode") == "txt"


@aio
async def test_a_command_that_hangs_is_stopped_at_timeout_s(tmp_path, command, src, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "hang")
    conv = MineruConverter(config(tmp_path, command, timeout_s=0.5))
    with pytest.raises(ConversionError, match="timeout_s"):
        await conv.convert_async(src("a.pdf", b"text"), request())
    assert list((tmp_path / "work").iterdir()) == []  # the work folder is gone


@aio
@pytest.mark.parametrize(("mode", "message"), [
    ("fail", "exit code 3: boom"), ("nozip", "without writing"), ("badjson", "unreadable"),
])  # fmt: skip
async def test_a_failing_command_is_a_conversion_error(
    tmp_path, command, monkeypatch, mode, message
):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"text")
    monkeypatch.setenv("FAKE_MINERU_MODE", mode)
    conv = MineruConverter(config(tmp_path, command))
    with pytest.raises(ConversionError, match=message):
        await conv.convert_async(path, request())


def test_the_fingerprint_has_the_mineru_version_and_the_model(tmp_path, command, monkeypatch):
    base = MineruConverter(config(tmp_path, command)).fingerprint
    assert "4.0.12" in base and MODEL in base
    other = MineruConverter(config(tmp_path, command, model="MinerU2.5-Pro-2604-1.2B"))
    assert other.fingerprint != base
    monkeypatch.setenv("FAKE_MINERU_VERSION", "4.0.13")
    assert MineruConverter(config(tmp_path, command)).fingerprint != base


@aio
async def test_a_new_mineru_version_invalidates_the_cache(tmp_path, command, src, monkeypatch):
    path = src("a.pdf", b"text")
    docs = {"a.pdf": [digital("x")]}
    first = service(tmp_path, command, docs)
    await first.ingest_async(path)
    await first.ingest_async(path)
    assert len(calls(tmp_path)) == 1  # the second one came from the cache
    monkeypatch.setenv("FAKE_MINERU_VERSION", "4.1.0")
    second = service(tmp_path, command, docs)
    second.store = first.store
    await second.ingest_async(path)
    assert len(calls(tmp_path)) == 2


# ------------------------------------------------------- work folders (R5, O5, O9)
@aio
async def test_two_files_with_the_same_name_do_not_share_output(tmp_path, command):
    docs = {"paper.pdf": [digital("x")]}
    svc = service(tmp_path, command, docs, keep_output=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "paper.pdf").write_bytes(b"alpha text")
    (tmp_path / "b" / "paper.pdf").write_bytes(b"beta text")
    first = await svc.ingest_async(tmp_path / "a" / "paper.pdf")
    second = await svc.ingest_async(tmp_path / "b" / "paper.pdf")
    assert first.manifest.doc_id != second.manifest.doc_id
    assert "alpha" in svc.store.markdown(first) and "beta" not in svc.store.markdown(first)
    assert "beta" in svc.store.markdown(second) and "alpha" not in svc.store.markdown(second)
    kept = sorted(p.name for p in (tmp_path / "work").iterdir())
    assert kept == sorted([first.manifest.doc_id, second.manifest.doc_id])  # no leftovers


@aio
async def test_nothing_is_kept_by_default(tmp_path, command, src):
    svc = service(tmp_path, command, {"a.pdf": [digital("x")]})
    await svc.ingest_async(src("a.pdf", b"text"))
    assert list((tmp_path / "work").iterdir()) == []


@aio
async def test_image_paths_are_relative_to_the_kept_folder(tmp_path, command, src):
    conv = MineruConverter(config(tmp_path, command, keep_output=True))
    path = src("a.pdf", b"text")
    conversion = await conv.convert_async(path, request())
    assert conversion.artifacts == [{"kind": "image", "page": 0, "path": "images/fig.png"}]
    assert (tmp_path / "work" / ("d" * 64) / "images" / "fig.png").is_file()


# --------------------------------------------------------- mode of a PDF (R4, O8)
@aio
@pytest.mark.parametrize(
    ("pages", "mode"),
    [
        ([digital(TEXT), digital(TEXT)], "txt"),
        ([digital(TEXT), scanned()], "ocr"),  # mixed: the scanned page decides
        ([scanned(), scanned()], "ocr"),
    ],
)
async def test_the_mode_is_decided_from_the_signals_of_every_page(
    tmp_path, command, src, pages, mode
):
    svc = service(tmp_path, command, {"m.pdf": pages})
    await svc.ingest_async(src("m.pdf", b"one\ftwo"))
    assert option(calls(tmp_path)[-1], "--ocr-mode") == mode
    assert svc.pdf.opened[-1].closed


# ----------------------------------------------------------------- fallback (R14)
@aio
async def test_latex_text_and_office_take_the_regular_path(tmp_path, command, src):
    tex = FakeConverter([Segment("A section")], PageMethod.LATEX)
    md = FakeConverter([Segment("notes")], PageMethod.PASSTHROUGH)
    svc = service(tmp_path, command, {}, converters={SourceKind.LATEX: tex, SourceKind.TEXT: md})
    doc = await svc.ingest_async(src("paper.tex"))
    assert doc.manifest.pages[0].method == PageMethod.LATEX and tex.calls == 1
    assert (await svc.ingest_async(src("n.md"))).manifest.pages[0].method == PageMethod.PASSTHROUGH
    assert calls(tmp_path) == []  # MinerU never saw them


@aio
async def test_a_conversion_error_falls_back_to_the_regular_ingest(
    tmp_path, command, src, monkeypatch
):
    monkeypatch.setenv("FAKE_MINERU_MODE", "fail")
    svc = service(tmp_path, command, {"a.pdf": [digital("plain text layer " * 5)]})
    doc = await svc.ingest_async(src("a.pdf", b"text"))
    assert [p.method for p in doc.manifest.pages] == [PageMethod.TEXT_LAYER]


class OverlapStore(InMemoryStore):
    """Counts how many ``save`` calls are inside the store at the same time."""

    def __init__(self):
        super().__init__()
        self.active = 0
        self.peak = 0
        self._guard = threading.Lock()

    def save(self, manifest, markdown, *, degraded=False):
        with self._guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.3)
            return super().save(manifest, markdown, degraded=degraded)
        finally:
            with self._guard:
                self.active -= 1


class FailsFirstThenSucceeds:
    """``a.pdf`` cannot be converted (a fallback follows); the other file converts a bit later."""

    fingerprint = "fails-first"

    async def convert_async(self, path, request):
        if path.name == "a.pdf":
            raise ConversionError("cannot read it")
        await asyncio.sleep(0.1)
        return Conversion(
            [Segment("converted text")] * request.source_pages, PageMethod.MINERU, "x"
        )


@aio
async def test_a_fallback_never_saves_while_another_document_is_saving(tmp_path, src):
    store = OverlapStore()
    svc = make_service(
        {"a.pdf": [digital(TEXT)], "b.pdf": [digital(TEXT + "b")]},
        images=FakeImages(1),
        multimodal=FailsFirstThenSucceeds(),
    )
    svc.store = store
    docs = await asyncio.gather(
        svc.ingest_async(src("a.pdf", b"a")), svc.ingest_async(src("b.pdf", b"b"))
    )
    assert [d.manifest.pages[0].method for d in docs] == [PageMethod.TEXT_LAYER, PageMethod.MINERU]
    assert store.peak == 1  # the fallback ran under the same lock as the other save


@aio
async def test_without_a_converter_ingest_async_says_so(src):
    svc = make_service({})
    with pytest.raises(NotConfiguredError, match="multimodal"):
        await svc.ingest_async(src("a.pdf"))


# ---------------------------------------------------------- cache keys (O1, O7)
@aio
async def test_the_multimodal_fingerprint_is_not_in_the_regular_cache_key(tmp_path, command):
    plain = make_service({})
    with_mineru = service(tmp_path, command, {})
    for kind in SourceKind:
        if kind in (SourceKind.PDF, SourceKind.IMAGE):
            continue
        conv = {kind: FakeConverter([Segment("x")])}
        assert make_service({}, converters=conv).config_hash(kind) == make_service(
            {}, converters=conv, multimodal=with_mineru.multimodal
        ).config_hash(kind)
    assert plain.multimodal is None
    assert with_mineru.multimodal_hash(SourceKind.PDF) != with_mineru.config_hash(SourceKind.PDF)


@aio
async def test_a_metadata_change_reuses_the_cache(tmp_path, command, src):
    path = src("a.pdf", b"text")
    svc = service(tmp_path, command, {"a.pdf": [digital("x")]})
    await svc.ingest_async(path)
    doc = await svc.ingest_async(path, SourceMetadata(title="A real title", year=2024))
    assert len(calls(tmp_path)) == 1 and doc.manifest.title == "A real title"
    assert doc.manifest.metadata and doc.manifest.metadata.year == 2024
    assert svc.store.markdown(doc).startswith("# A real title")


@aio
async def test_the_last_command_wins_the_canonical_document(tmp_path, command, src):
    """ingest and ingest-async share the one canonical slot of a document: the keys differ, so
    each knows its own result, but a run of the other command replaces what the first stored."""
    path = src("a.pdf", b"text")
    svc = service(tmp_path, command, {"a.pdf": [digital(TEXT)]})
    first = await svc.ingest_async(path)
    assert first.manifest.pages[0].method == PageMethod.MINERU
    regular = svc.ingest(path, IngestOptions())
    assert regular.manifest.pages[0].method == PageMethod.TEXT_LAYER
    assert (
        svc.store.lookup(
            regular.manifest.doc_id, first.manifest.config_hash, max_pages=None, ocr_all=False
        )
        is None
    )
    again = await svc.ingest_async(path)
    assert again.manifest.pages[0].method == PageMethod.MINERU
    assert len(calls(tmp_path)) == 2  # MinerU ran again: the regular run had replaced its result


# ------------------------------------------------- scan or text (blank pages)
@aio
async def test_a_blank_page_does_not_make_the_document_a_scan(tmp_path, command, src):
    pages = [digital(TEXT), digital(TEXT), blank()]
    svc = service(tmp_path, command, {"m.pdf": pages})
    await svc.ingest_async(src("m.pdf", b"one\ftwo\fthree"))
    assert option(calls(tmp_path)[-1], "--ocr-mode") == "txt"


# ------------------------------------------------------------ segment count
class CountingConverter:
    fingerprint = "counting 1"

    def __init__(self, n: int):
        self.n = n

    async def convert_async(self, path: Path, request: MultimodalRequest) -> Conversion:
        return Conversion([Segment("x")] * self.n, PageMethod.MINERU, "counting")


@aio
@pytest.mark.parametrize("n", [0, 1])
async def test_a_conversion_with_the_wrong_number_of_pages_falls_back(src, n):
    svc = make_service({"a.pdf": [digital(TEXT)] * 3}, multimodal=CountingConverter(n))
    doc = await svc.ingest_async(src("a.pdf", b"text"))
    assert doc.manifest.n_pages == doc.manifest.source_pages == 3
    assert {p.method for p in doc.manifest.pages} == {PageMethod.TEXT_LAYER}


# --------------------------------------------------- what the zip and the tool do
@aio
async def test_a_timeout_stops_the_processes_the_command_started(
    tmp_path, command, src, monkeypatch
):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_MINERU_MODE", "orphan")
    monkeypatch.setenv("FAKE_MINERU_CHILD", str(pid_file))
    conv = MineruConverter(config(tmp_path, command, timeout_s=1.5))
    with pytest.raises(ConversionError, match="timeout_s"):
        await conv.convert_async(src("a.pdf", b"text"), request())
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    os.kill(pid, 9)
    pytest.fail("the child of the command is still running")


@aio
async def test_a_timeout_does_not_wait_for_the_children_of_the_command(
    tmp_path, command, src, monkeypatch
):
    """Killing only the direct child would leave communicate() waiting for the grandchild that
    holds the pipes (60 s here): the whole process group has to go."""
    monkeypatch.setenv("FAKE_MINERU_MODE", "orphan")
    monkeypatch.setenv("FAKE_MINERU_CHILD", str(tmp_path / "child.pid"))
    conv = MineruConverter(config(tmp_path, command, timeout_s=1))
    started = time.monotonic()
    with pytest.raises(ConversionError, match="timeout_s"):
        await conv.convert_async(src("a.pdf", b"text"), request())
    assert time.monotonic() - started < 10


@aio
async def test_a_zip_member_outside_the_folder_is_refused(tmp_path, command, src, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "unsafe")
    conv = MineruConverter(config(tmp_path, command))
    with pytest.raises(ConversionError, match="unsafe path"):
        await conv.convert_async(src("a.pdf", b"text"), request())
    assert not (tmp_path / "x").exists() and list((tmp_path / "work").iterdir()) == []


@aio
async def test_a_zip_that_unpacks_too_big_is_refused(tmp_path, command, src, monkeypatch):
    monkeypatch.setattr(mineru_module, "MAX_UNPACKED_BYTES", 10)
    conv = MineruConverter(config(tmp_path, command))
    with pytest.raises(ConversionError, match="unreasonable size"):
        await conv.convert_async(src("a.pdf", b"text"), request())


@aio
async def test_a_result_without_text_is_a_conversion_error(tmp_path, command, src, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "empty")
    conv = MineruConverter(config(tmp_path, command))
    with pytest.raises(ConversionError, match="produced no text"):
        await conv.convert_async(src("a.pdf", b"text"), request())


@aio
async def test_an_absolute_image_path_comes_back_relative(tmp_path, command, src, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "abs")
    conv = MineruConverter(config(tmp_path, command, keep_output=True))
    conversion = await conv.convert_async(src("a.pdf", b"text"), request())
    assert conversion.artifacts == [{"kind": "image", "page": 0, "path": "images/fig.png"}]


@aio
async def test_an_inline_equation_is_wrapped_in_dollars(tmp_path, command, src, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "math")
    conv = MineruConverter(config(tmp_path, command))
    conversion = await conv.convert_async(src("a.pdf", b"text"), request())
    assert "where $x^2$" in conversion.segments[0].text


@aio
async def test_the_model_source_reaches_the_command(tmp_path, command, src):
    conv = MineruConverter(config(tmp_path, command, model_source="modelscope"))
    await conv.convert_async(src("a.pdf", b"text"), request())
    assert calls(tmp_path)[-1]["model_source"] == "modelscope"
    await MineruConverter(config(tmp_path, command)).convert_async(src("b.pdf", b"t"), request())
    assert calls(tmp_path)[-1]["model_source"] is None


@aio
async def test_two_runs_of_the_same_document_at_once_keep_one_output(tmp_path, command, src):
    conv = MineruConverter(config(tmp_path, command, keep_output=True))
    path = src("a.pdf", b"text")
    results = await asyncio.gather(
        *(conv.convert_async(path, request()) for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(r, Conversion | ConversionError) for r in results)
    assert any(isinstance(r, Conversion) for r in results)
    assert sorted(p.name for p in (tmp_path / "work").iterdir()) == ["d" * 64]


@aio
async def test_same_name_files_at_once_do_not_share_output(tmp_path, command):
    svc = service(tmp_path, command, {"paper.pdf": [digital("x")]}, keep_output=True)
    paths = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "paper.pdf").write_bytes(f"{name}{name} text".encode())
        paths.append(tmp_path / name / "paper.pdf")
    first, second = await asyncio.gather(*(svc.ingest_async(p) for p in paths))
    assert "aa" in svc.store.markdown(first) and "bb" not in svc.store.markdown(first)
    assert "bb" in svc.store.markdown(second) and "aa" not in svc.store.markdown(second)
    assert len(list((tmp_path / "work").iterdir())) == 2


# ------------------------------------------------------------- configuration
@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"command": None}, "command is required"),
        ({"model": None}, "model is required"),
        ({"model": "MinerU2.5-2509-1.2B"}, "AGPL"),
        ({"model": "someone/else"}, "only the Apache-2.0"),
        ({"tier": "flash"}, "tier"),
    ],
)
def test_a_configuration_that_cannot_run_is_refused(tmp_path, command, kw, message):
    with pytest.raises(InvalidConfigError, match=message):
        MineruConverter(config(tmp_path, command, **kw))


def test_a_command_that_is_not_mineru_kit_is_refused(tmp_path, command, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_VERSION", "3.4.5")
    with pytest.raises(InvalidConfigError, match="only MinerU 4"):
        MineruConverter(config(tmp_path, command))
    with pytest.raises(InvalidConfigError, match="cannot be run"):
        MineruConverter(config(tmp_path, tmp_path / "missing"))


# --------------------------------------------------------------------- the CLI
def write_config(tmp_path, command, adapter="mineru", extra="") -> Path:
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(
        f'output_dir = "{tmp_path / "out"}"\n'
        f'[adapters]\nmultimodal = "{adapter}"\n'
        f'[mineru]\ncommand = "{command}"\nmodel = "{MODEL}"\n'
        f'output_dir = "{tmp_path / "work"}"\n{extra}'
    )
    return cfg


def test_ingest_async_command_reads_pages_and_reports_failures(tmp_path, command):
    raw = tmp_path / "raw"
    raw.mkdir()
    text_pdf(raw / "a.pdf", "A page with a text layer, long enough to be read as text " * 2)
    (raw / "b.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<<")  # corrupt: not even the fallback reads it
    (raw / "c.md").write_text("# Note\n" + "words " * 40)
    cfg = write_config(tmp_path, command)
    result = CliRunner().invoke(app, ["ingest-async", str(raw), "--config", str(cfg)])
    assert result.exit_code == 1, result.output
    index = json.loads((tmp_path / "out" / "index.json").read_text())
    assert sorted(v["source_name"] for v in index.values()) == ["a.pdf", "c.md"]
    assert "b.pdf" in result.output.split("Failed inputs", 1)[1]
    assert len(calls(tmp_path)) >= 1
    (a,) = (v for v in index.values() if v["source_name"] == "a.pdf")
    manifest = json.loads((tmp_path / "out" / a["dir"] / "manifest.json").read_text())
    assert {p["method"] for p in manifest["pages"]} == {"mineru"}


def test_ingest_async_command_needs_a_converter(tmp_path, command):
    cfg = write_config(tmp_path, command, adapter="none")
    result = CliRunner().invoke(app, ["ingest-async", str(tmp_path), "--config", str(cfg)])
    assert result.exit_code == 1 and "multimodal" in result.output


def test_ingest_async_command_stops_on_a_bad_mineru_table(tmp_path, command):
    cfg = write_config(tmp_path, command)
    cfg.write_text(cfg.read_text().replace(MODEL, "MinerU2.5-2509-1.2B"))
    result = CliRunner().invoke(app, ["ingest-async", str(tmp_path), "--config", str(cfg)])
    assert result.exit_code == 1 and "AGPL" in result.output


def run_cli_on_pdfs(tmp_path, command, n, concurrency, monkeypatch):
    monkeypatch.setenv("FAKE_MINERU_MODE", "slow")
    raw = tmp_path / "raw"
    raw.mkdir()
    for i in range(n):
        text_pdf(raw / f"d{i}.pdf", f"Document number {i} with a text layer long enough. " * 3)
    cfg = write_config(tmp_path, command, extra=f"max_concurrency = {concurrency}\n")
    result = CliRunner().invoke(app, ["ingest-async", str(raw), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    starts = sorted(c["start"] for c in calls(tmp_path))
    return starts[-1] - starts[0], json.loads((tmp_path / "out" / "index.json").read_text())


def test_max_concurrency_one_runs_the_command_one_document_at_a_time(
    tmp_path, command, monkeypatch
):
    span, index = run_cli_on_pdfs(tmp_path, command, 3, 1, monkeypatch)
    assert span >= 0.8 and len(index) == 3


def test_documents_at_once_all_reach_the_catalog(tmp_path, command, monkeypatch):
    span, index = run_cli_on_pdfs(tmp_path, command, 4, 4, monkeypatch)
    assert span < 1.2 and len(index) == 4  # they overlapped, and no catalog entry was lost


def test_a_mixed_folder_keeps_every_document_in_the_catalog(tmp_path, command, monkeypatch):
    """PDFs go through the converter, the text files straight to ``ingest``: with several
    documents at once, every save still has to land in index.json."""
    monkeypatch.setenv("FAKE_MINERU_MODE", "slow")
    raw = tmp_path / "raw"
    raw.mkdir()
    for i in range(4):
        text_pdf(raw / f"d{i}.pdf", f"Document number {i} with a text layer long enough. " * 3)
    for i in range(60):
        (raw / f"n{i}.md").write_text(f"# Note {i}\n" + f"words {i} " * 40)
    cfg = write_config(tmp_path, command, extra="max_concurrency = 8\n")
    result = CliRunner().invoke(app, ["ingest-async", str(raw), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    index = json.loads((out / "index.json").read_text())
    stored = list(out.glob("*/manifest.json"))
    assert len(stored) == 64
    assert len(index) == len(stored)


def test_a_plain_ingest_does_not_start_the_converter(tmp_path, command):
    cfg = write_config(tmp_path, command)
    cfg.write_text(cfg.read_text().replace(MODEL, "MinerU2.5-2509-1.2B"))  # an unusable table
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "n.md").write_text("# Note\n" + "words " * 40)
    result = CliRunner().invoke(app, ["ingest", str(raw), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert calls(tmp_path) == []
