#!/usr/bin/env python3
"""A stand-in for the ``mineru-kit`` executable, for the tests of the MinerU adapter.

It understands ``--version`` and ``parse INPUT -o OUT --format zip --tier T --ocr-mode M``.
The input is a text file: its pages are separated by a form feed, a page that starts with
``# `` gets that line as a title block. It writes the zip MinerU writes (middle JSON, Markdown,
one image) and logs every call as a JSON line in ``$FAKE_MINERU_LOG``.

``FAKE_MINERU_VERSION`` sets the version; ``FAKE_MINERU_MODE`` is ``fail`` (exit code 3),
``hang`` (never answers), ``orphan`` (hangs after starting a child process whose pid goes to
``$FAKE_MINERU_CHILD``), ``slow`` (answers after 0.4 s), ``nozip`` (exit 0, writes nothing),
``badjson``, ``extra`` (a page the document does not have), ``unsafe`` (a zip member outside the
folder), ``empty`` (a result without text), ``abs`` (an absolute image path inside the folder) or
``math`` (an inline equation in the text).
"""

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def build_pages(content: str, mode: str, out: Path) -> list[dict]:
    pages = []
    for index, text in enumerate(content.split("\f")):
        blocks = []
        for part in filter(None, text.split("\n\n")):
            kind = "title" if part.startswith("# ") else "text"
            spans = [{"type": "text", "content": part.removeprefix("# ")}]
            blocks.append({"type": kind, "level": 1, "lines": [{"spans": spans}]})
        if text.strip():
            path = str(out.parent / "out" / "images" / "fig.png") if mode == "abs" else "fig.png"
            image = {
                "type": "image_body",
                "lines": [{"spans": [{"type": "image", "image_path": path}]}],
            }
            blocks.append({"type": "image", "blocks": [image]})
        if mode == "math" and text.strip():
            spans = [
                {"type": "text", "content": "where"},
                {"type": "inline_equation", "content": "x^2"},
            ]
            blocks.append({"type": "text", "lines": [{"spans": spans}]})
        if mode == "empty":
            blocks = []
        pages.append({"page_idx": index, "page_size": [612, 792], "para_blocks": blocks})
    return pages


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print(f"mineru-kit, version {os.environ.get('FAKE_MINERU_VERSION', '4.0.12')}")
        return 0
    assert argv[0] == "parse", argv
    source = Path(argv[1])
    out = Path(argv[argv.index("-o") + 1])
    with open(os.environ["FAKE_MINERU_LOG"], "a") as log:
        started = time.time()
        log.write(
            json.dumps(
                {
                    "argv": argv,
                    "out": str(out),
                    "model_source": os.environ.get("MINERU_MODEL_SOURCE"),
                    "start": started,
                }
            )
            + "\n"
        )
    mode = os.environ.get("FAKE_MINERU_MODE", "")
    if mode == "orphan":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path(os.environ["FAKE_MINERU_CHILD"]).write_text(str(child.pid))
    if mode in ("hang", "orphan"):
        time.sleep(60)
    if mode == "slow":
        time.sleep(0.4)
    if mode == "fail":
        print("boom: the model is not downloaded", file=sys.stderr)
        return 3
    if mode == "nozip":
        return 0
    pages = build_pages(source.read_text(errors="replace"), mode, out)
    if mode == "extra":
        pages.append({"page_idx": len(pages), "para_blocks": []})
    middle = "{" if mode == "badjson" else json.dumps({"pdf_info": pages})
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("middle_json.json", middle)
        zf.writestr("markdown.md", source.read_text(errors="replace"))
        zf.writestr("images/fig.png", b"png")
        if mode == "unsafe":
            zf.writestr("../x", b"escaped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
