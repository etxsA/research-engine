"""``domain.artifacts`` and the manifest's ``artifacts``: what is read from the Markdown, which
page it is on, and what must not change (the cache key, old manifests).

Every text here is invented.
"""

import hashlib
import json

import pytest
from fakes import FakeConverter, make_service
from pydantic import ValidationError

from docingest.application.ingest import PIPELINE_VERSION, IngestOptions
from docingest.domain.artifacts import build_artifact, checked_confidence, extract_artifacts
from docingest.domain.models import (
    ArtifactKind,
    DocumentManifest,
    LayoutArtifact,
    PageMethod,
    PageRecord,
    SourceKind,
)
from docingest.ports import Segment

TABLE = "| name | value |\n| --- | --- |\n| a | 1 |\n| b | 2 |"
EQUATION = "$$\nE = m c^2\n$$"


def page(n: int, body: str) -> str:
    return f"<!-- page {n} | method=text_layer -->\n{body}\n"


# ------------------------------------------------------------------------------- extraction


def test_display_equations_and_tables_become_artifacts_in_order():
    md = "# T\n\n" + page(1, f"Intro.\n\n{EQUATION}\n\nThen:\n\n{TABLE}\n")
    artifacts, warnings = extract_artifacts(md)
    assert [a.kind for a in artifacts] == [ArtifactKind.EQUATION, ArtifactKind.TABLE]
    assert artifacts[0].latex == "E = m c^2" and artifacts[0].text is None
    assert artifacts[1].text == TABLE and artifacts[1].latex is None
    assert warnings == []


def test_each_display_form_gives_its_latex_without_delimiters():
    md = page(
        1,
        "\\[\na^2 + b^2 = c^2\n\\]\n\n"
        "\\begin{equation}\nx = 1 \\tag{3}\n\\end{equation}\n\n$$ y = 2 $$\n",
    )
    artifacts, _ = extract_artifacts(md)
    assert [a.latex for a in artifacts] == [
        "a^2 + b^2 = c^2",
        "\\begin{equation}\nx = 1 \\tag{3}\n\\end{equation}",
        "y = 2",
    ]


def test_inline_math_and_code_blocks_are_not_artifacts():
    md = page(1, "Let $x$ and $y = 2$ be reals, and $a_1$ too.\n\n```\ncode with $$ in it\n```\n")
    assert extract_artifacts(md) == ([], [])


def test_thousands_of_inline_formulas_add_nothing():
    md = page(1, " ".join(f"$x_{i}$" for i in range(2000)))
    assert extract_artifacts(md)[0] == []


def test_a_text_without_blocks_has_no_artifacts():
    assert extract_artifacts("") == ([], [])
    assert extract_artifacts(page(1, "plain words")) == ([], [])


# ------------------------------------------------------------------------------- pages


def test_the_page_is_the_zero_based_index_of_the_marker_above_the_block():
    md = "# T\n\n" + page(1, "no math") + "\n" + page(2, EQUATION) + "\n" + page(3, TABLE)
    artifacts, _ = extract_artifacts(md)
    assert [(a.kind, a.page) for a in artifacts] == [
        (ArtifactKind.EQUATION, 1),
        (ArtifactKind.TABLE, 2),
    ]


def test_two_blocks_on_one_page_share_it_and_a_page_without_any_has_none():
    md = page(1, EQUATION) + "\n" + page(2, "text") + "\n" + page(3, f"{EQUATION}\n\n{TABLE}")
    assert [a.page for a in extract_artifacts(md)[0]] == [0, 2, 2]


def test_a_block_above_the_first_marker_is_on_page_zero():
    assert [a.page for a in extract_artifacts(f"{EQUATION}\n\n" + page(4, "x"))[0]] == [0]


def test_the_page_follows_the_marker_number_not_the_position_of_the_marker():
    """A partial run keeps the markers it wrote: the first marker may not say page 1."""
    assert [a.page for a in extract_artifacts(page(5, EQUATION))[0]] == [4]


# ------------------------------------------------------------------------------- confidence


@pytest.mark.parametrize("value", [None, 0.0, 0.5, 1.0])
def test_a_confidence_in_range_is_kept(value):
    assert checked_confidence(value) == (value, None)


@pytest.mark.parametrize("value", [-0.1, 1.01, 87.0, float("nan"), float("inf")])
def test_a_confidence_out_of_range_is_dropped_with_a_warning(value):
    kept, warning = checked_confidence(value)
    assert kept is None and warning and "outside [0, 1]" in warning


def test_a_bad_confidence_does_not_fail_building_the_artifact():
    artifact, warnings = build_artifact(ArtifactKind.FIGURE, 0, text="Fig. 1", confidence=87.0)
    assert artifact.confidence is None and len(warnings) == 1
    kept, none = build_artifact(ArtifactKind.FIGURE, 0, text="Fig. 1", confidence=0.9)
    assert kept.confidence == 0.9 and none == []


def test_the_model_itself_refuses_a_confidence_out_of_range():
    with pytest.raises(ValidationError):
        LayoutArtifact(kind=ArtifactKind.TABLE, page=0, text="t", confidence=2.0)


# ------------------------------------------------------------------------------- the model


def test_an_artifact_needs_text_or_latex_and_a_page_from_zero():
    with pytest.raises(ValidationError):
        LayoutArtifact(kind=ArtifactKind.REGION, page=0)
    with pytest.raises(ValidationError):
        LayoutArtifact(kind=ArtifactKind.REGION, page=-1, text="x")
    box = LayoutArtifact(kind=ArtifactKind.FIGURE, page=3, text="Fig. 2", bbox=(0.1, 0.2, 0.9, 0.5))
    assert box.bbox == (0.1, 0.2, 0.9, 0.5) and box.confidence is None


# ------------------------------------------------------------------------------- ingest


def converter(*bodies: str) -> FakeConverter:
    return FakeConverter([Segment(b, f"S{i}") for i, b in enumerate(bodies)])


def test_ingest_fills_the_manifest_from_the_converted_markdown(src):
    svc = make_service(converters={SourceKind.LATEX: converter("Intro", f"{EQUATION}\n\n{TABLE}")})
    doc = svc.ingest(src("paper.tex"))
    assert [(a.kind, a.page) for a in doc.manifest.artifacts] == [
        (ArtifactKind.EQUATION, 1),
        (ArtifactKind.TABLE, 1),
    ]
    markdown = svc.store.markdown(doc)
    assert "<!-- page 2 | method=latex -->" in markdown and "E = m c^2" in markdown


def test_a_document_without_blocks_has_an_empty_list(src):
    svc = make_service(converters={SourceKind.LATEX: converter("just words")})
    assert svc.ingest(src("paper.tex")).manifest.artifacts == []


def test_a_partial_run_numbers_the_pages_of_the_segments_it_kept(src):
    svc = make_service(converters={SourceKind.LATEX: converter("a", EQUATION, TABLE)})
    doc = svc.ingest(src("paper.tex"), IngestOptions(max_pages=2))
    assert [a.page for a in doc.manifest.artifacts] == [1]


def test_a_metadata_refresh_keeps_the_artifacts(src):
    from docingest.domain.models import SourceMetadata

    svc = make_service(converters={SourceKind.LATEX: converter(EQUATION)})
    path = src("paper.tex")
    first = svc.ingest(path)
    again = svc.ingest(path, metadata=SourceMetadata(title="A new title"))
    assert again.manifest.title == "A new title"
    assert again.manifest.artifacts == first.manifest.artifacts != []


# ------------------------------------------------------------------------------- compatibility


def test_the_pipeline_version_and_the_cache_key_do_not_depend_on_artifacts(src):
    """Artifacts are an output with a default, not an input of the run: a document stored
    before them is served again, never converted a second time (R15)."""
    assert PIPELINE_VERSION == "0.4.1"
    conv = converter(f"{EQUATION}\n\n{TABLE}")
    svc = make_service(converters={SourceKind.LATEX: conv})
    # Written out again: the key is the sha256 of this list, nothing about artifacts is in it.
    key = json.dumps(["0.4.1", "latex", False, conv.fingerprint])
    expected = hashlib.sha256(key.encode()).hexdigest()[:12]
    assert svc.config_hash(SourceKind.LATEX) == expected == "682714ac9455"  # as on main
    doc = svc.ingest(src("paper.tex"))
    assert doc.manifest.config_hash == expected and len(doc.manifest.artifacts) == 2
    again = svc.ingest(src("paper.tex"))
    assert conv.calls == 1 and again.manifest.config_hash == expected


def test_a_manifest_written_before_the_field_loads_and_is_served_from_the_cache(src):
    conv = converter(EQUATION)
    svc = make_service(converters={SourceKind.LATEX: conv})
    stored = svc.ingest(src("paper.tex"))
    old = json.loads(stored.manifest.model_dump_json())
    del old["artifacts"]  # what manifest.json looked like before this field
    loaded = DocumentManifest.model_validate(old)
    assert loaded.artifacts == []
    assert loaded.config_hash == svc.config_hash(SourceKind.LATEX)
    svc.store.canonical[loaded.doc_id] = (loaded, svc.store.markdown(stored))
    svc.ingest(src("paper.tex"))
    assert conv.calls == 1  # a hit: the document is not converted again


def test_the_model_dump_of_a_manifest_without_artifacts_still_loads():
    m = DocumentManifest(
        doc_id="d" * 64,
        source_path="/x/a.txt",
        source_name="a.txt",
        source_kind=SourceKind.TEXT,
        mime="text/plain",
        size_bytes=1,
        n_pages=1,
        source_pages=1,
        pages=[PageRecord(index=0, method=PageMethod.PASSTHROUGH, n_chars=1, seconds=0)],
        pipeline_version="0.4.1",
        config_hash="h",
    )
    assert m.artifacts == []
    assert DocumentManifest.model_validate_json(m.model_dump_json()) == m
