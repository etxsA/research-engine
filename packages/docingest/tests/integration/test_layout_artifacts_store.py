"""Layout artifacts through the filesystem store: written to manifest.json, read back by lookup
and by corpus, and old manifest files that have no such field."""

import json

from fakes import FakeConverter, make_service

from docingest.adapters.store.filesystem import FilesystemStore
from docingest.domain.models import ArtifactKind, SourceKind
from docingest.ports import Segment

BODY = "Intro.\n\n$$\n\\psi = \\sum_i c_i |i\\rangle\n$$\n\n| k | v |\n| - | - |\n| a | 1 |"


def service(tmp_path):
    conv = FakeConverter([Segment("Words only"), Segment(BODY)])
    svc = make_service(converters={SourceKind.LATEX: conv})
    svc.store = FilesystemStore(tmp_path / "out")
    return svc, conv


def test_artifacts_round_trip_through_manifest_json(tmp_path, src):
    svc, conv = service(tmp_path)
    path = src("paper.tex")
    doc = svc.ingest(path)
    written = json.loads(
        (tmp_path / "out" / doc.manifest.doc_id[:16] / "manifest.json").read_text()
    )
    assert [(a["kind"], a["page"]) for a in written["artifacts"]] == [("equation", 1), ("table", 1)]
    assert "\\psi" in written["artifacts"][0]["latex"] and written["artifacts"][1]["latex"] is None

    hit = svc.ingest(path)  # lookup reads the file back
    assert conv.calls == 1 and hit.manifest.artifacts == doc.manifest.artifacts
    docs, warnings = svc.store.corpus()
    assert warnings == [] and docs[0].manifest.artifacts == doc.manifest.artifacts
    assert [a.kind for a in docs[0].manifest.artifacts] == [
        ArtifactKind.EQUATION,
        ArtifactKind.TABLE,
    ]


def test_a_manifest_file_without_artifacts_is_still_a_cache_hit(tmp_path, src):
    svc, conv = service(tmp_path)
    path = src("paper.tex")
    doc = svc.ingest(path)
    file = tmp_path / "out" / doc.manifest.doc_id[:16] / "manifest.json"
    data = json.loads(file.read_text())
    del data["artifacts"]  # a manifest from before this field
    file.write_text(json.dumps(data))
    hit = svc.ingest(path)
    assert conv.calls == 1 and hit.manifest.artifacts == []
    assert hit.manifest.config_hash == doc.manifest.config_hash
