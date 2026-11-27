#!/usr/bin/env python3
"""A stand-in for the ``mineru-kit`` executable, for the tests of the MinerU adapter.

It understands ``--version`` and ``parse INPUT -o OUT --format zip --tier T --ocr-mode M``.
The input is a text file: its pages are separated by a form feed, a page that starts with
``# `` gets that line as a title block. It writes the zip MinerU writes (middle JSON, Markdown,
one image) and logs every call as a JSON line in ``$FAKE_MINERU_LOG``.

``FAKE_MINERU_VERSION`` sets the version; ``FAKE_MINERU_MODE`` is ``fail`` (exit code 3),
``hang`` (never answers), ``nozip`` (exit 0, writes nothing), ``badjson`` or ``extra`` (a page
the document does not have).
"""

import json
import os
import sys
import time
import zipfile
from pathlib import Path


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print(f"mineru-kit, version {os.environ.get('FAKE_MINERU_VERSION', '4.0.12')}")
        return 0
    assert argv[0] == "parse", argv
    source = Path(argv[1])
    out = Path(argv[argv.index("-o") + 1])
    with open(os.environ["FAKE_MINERU_LOG"], "a") as log:
        log.write(json.dumps({"argv": argv, "out": str(out)}) + "\n")
    mode = os.environ.get("FAKE_MINERU_MODE", "")
    if mode == "hang":
        time.sleep(60)
    if mode == "fail":
        print("boom: the model is not downloaded", file=sys.stderr)
        return 3
    if mode == "nozip":
        return 0
    pages = []
    for index, text in enumerate(source.read_text().split("\f")):
        blocks = []
        for part in filter(None, text.split("\n\n")):
            kind = "title" if part.startswith("# ") else "text"
            spans = [{"type": "text", "content": part.removeprefix("# ")}]
            blocks.append({"type": kind, "level": 1, "lines": [{"spans": spans}]})
        if text.strip():
            image = {
                "type": "image_body",
                "lines": [{"spans": [{"type": "image", "image_path": "fig.png"}]}],
            }
            blocks.append({"type": "image", "blocks": [image]})
        pages.append({"page_idx": index, "page_size": [612, 792], "para_blocks": blocks})
    if mode == "extra":
        pages.append({"page_idx": len(pages), "para_blocks": []})
    middle = "{" if mode == "badjson" else json.dumps({"pdf_info": pages})
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("middle_json.json", middle)
        zf.writestr("markdown.md", source.read_text())
        zf.writestr("images/fig.png", b"png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
