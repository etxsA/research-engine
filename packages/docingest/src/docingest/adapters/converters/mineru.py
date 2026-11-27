"""AsyncDocumentConverter adapter: PDFs and images -> one Markdown segment per page via MinerU 4.

MinerU is an *external command* (``mineru-kit``), not a Python dependency of docingest: it is
installed in its own environment and ``[mineru] command`` points at it. Each document is parsed
by ``<command> parse <file> -o <zip> --format zip --tier <tier> --ocr-mode <mode>`` (an argument
list, no shell) under a wall-clock timeout, inside a work folder that is new for every run and
lives in ``[mineru] output_dir``. The zip holds MinerU's middle JSON, which carries the page of
every block (``page_idx``, from 0): the text of each page is rebuilt from it, so the page numbers
of the stored document are the PDF's own and a page without text stays an empty page.

With ``keep_output`` the work folder is renamed to ``output_dir/<doc_id>`` once the conversion
worked (a document never reads another one's files, whatever its file name); without it nothing
is kept.

The weights MinerU runs cannot be chosen on its command line. ``[mineru] model`` names the ones
the operator checked: only the Apache-2.0 MinerU2.5-Pro weights are accepted (config/README.md,
"Licenses").
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ...config import MINERU_MODELS, MINERU_TIERS, MineruConfig
from ...domain.errors import ConversionError, InvalidConfigError
from ...domain.models import PageMethod
from ...ports import Conversion, MultimodalRequest, Segment

MAJOR = "4"  # the CLI of MinerU 4 (`mineru-kit parse`); older releases have another one
MIDDLE_JSON = "middle_json.json"
MAX_UNPACKED_BYTES = 2 << 30  # a zip that unpacks to more than this is refused
MAX_DETAIL = 500  # characters of the command's own message kept in an error
_VERSION = re.compile(r"\d+(?:\.\d+)+[\w.+-]*")
_AGPL_MODEL = "MinerU2.5-2509-1.2B"


def _run_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InvalidConfigError(
            f"[mineru] command {command!r} cannot be run ({error}): point it at the mineru-kit "
            "executable of a MinerU 4 install"
        ) from error
    text = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not (found := _VERSION.search(text)):
        raise InvalidConfigError(
            f"[mineru] command {command!r} did not report a version for --version "
            f"(exit code {result.returncode}): is it mineru-kit?"
        )
    return found.group(0)


def check_config(cfg: MineruConfig) -> str:
    """Refuse a configuration that cannot run, naming the key; returns the resolved command."""
    if not cfg.command:
        raise InvalidConfigError(
            '[mineru] command is required with [adapters] multimodal = "mineru": '
            "the path of the mineru-kit executable"
        )
    command = shutil.which(cfg.command) or cfg.command
    if cfg.tier not in MINERU_TIERS:
        raise InvalidConfigError(
            f"[mineru] tier = {cfg.tier!r}: only {list(MINERU_TIERS)} run the MinerU2.5-Pro model"
        )
    if cfg.model is None:
        raise InvalidConfigError(
            "[mineru] model is required: the weights the install uses, one of "
            f"{list(MINERU_MODELS)} (MinerU's CLI cannot choose them; check with "
            "`mineru-kit models verify`)"
        )
    if cfg.model not in MINERU_MODELS:
        why = (
            "those weights are AGPL-3.0 and docingest does not use them"
            if _AGPL_MODEL in cfg.model
            else "only the Apache-2.0 Pro weights are supported"
        )
        raise InvalidConfigError(
            f"[mineru] model = {cfg.model!r} is refused: {why}; use one of {list(MINERU_MODELS)}"
        )
    return command


class MineruConverter:
    """Runs MinerU 4 per document off the event loop; one segment per page of the source."""

    def __init__(self, cfg: MineruConfig):
        self.cfg = cfg
        self.command = check_config(cfg)
        self.version = _run_version(self.command)
        if self.version.split(".", 1)[0] != MAJOR:
            raise InvalidConfigError(
                f"[mineru] command is MinerU {self.version}: only MinerU {MAJOR} is supported"
            )
        self.fingerprint = f"mineru {self.version} model={cfg.model} tier={cfg.tier}"

    async def convert_async(self, path: Path, request: MultimodalRequest) -> Conversion:
        return await asyncio.to_thread(self._convert, path, request)

    # ------------------------------------------------------------------ run
    def _convert(self, path: Path, request: MultimodalRequest) -> Conversion:
        output_dir = Path(self.cfg.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f".{request.doc_id}-", dir=output_dir))
        try:
            unpacked = work / "out"
            self._run(path, work / "result.zip", request)
            self._unpack(work / "result.zip", unpacked)
            conversion = self._read(path, unpacked, request)
            if self.cfg.keep_output:
                final = output_dir / request.doc_id
                shutil.rmtree(final, ignore_errors=True)
                os.replace(unpacked, final)
            return conversion
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, path: Path, result: Path, request: MultimodalRequest) -> None:
        args = [
            self.command,
            "parse",
            str(path),
            "-o",
            str(result),
            "--format",
            "zip",
            "--tier",
            self.cfg.tier,
            "--ocr-mode",
            "ocr" if request.ocr else "txt",
        ]
        env = {**os.environ, "MINERU_TELEMETRY_DISABLED": "1"}
        if self.cfg.model_source:
            env["MINERU_MODEL_SOURCE"] = self.cfg.model_source
        try:
            done = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.cfg.timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as error:
            raise ConversionError(
                f"MinerU took more than [mineru] timeout_s = {self.cfg.timeout_s:g} s on "
                f"{path.name}; it was stopped"
            ) from error
        except OSError as error:
            raise ConversionError(
                f"MinerU could not be started for {path.name}: {error}"
            ) from error
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip()[-MAX_DETAIL:]
            raise ConversionError(
                f"MinerU failed on {path.name} with exit code {done.returncode}: {detail}"
            )
        if not result.is_file():
            raise ConversionError(f"MinerU finished without writing its result for {path.name}")

    @staticmethod
    def _unpack(archive: Path, into: Path) -> None:
        try:
            with zipfile.ZipFile(archive) as zf:
                members = zf.infolist()
                if sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
                    raise ConversionError("the MinerU result unpacks to an unreasonable size")
                for member in members:
                    parts = PurePosixPath(member.filename).parts
                    if PurePosixPath(member.filename).is_absolute() or ".." in parts:
                        raise ConversionError(
                            f"unsafe path in the MinerU result: {member.filename}"
                        )
                zf.extractall(into)
        except zipfile.BadZipFile as error:
            raise ConversionError(f"the MinerU result is not a readable zip: {error}") from error

    # ----------------------------------------------------------------- read
    def _read(self, path: Path, root: Path, request: MultimodalRequest) -> Conversion:
        middle = root / MIDDLE_JSON
        if not middle.is_file():
            found = sorted(root.rglob(MIDDLE_JSON))
            if len(found) != 1:
                raise ConversionError(f"the MinerU result for {path.name} has no {MIDDLE_JSON}")
            middle = found[0]
        try:
            payload = json.loads(middle.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConversionError(f"unreadable {MIDDLE_JSON} for {path.name}: {error}") from error
        pages = (
            payload.get("pdf_info") or payload.get("pages") if isinstance(payload, dict) else None
        )
        if not isinstance(pages, list):
            raise ConversionError(f"{MIDDLE_JSON} for {path.name} has no list of pages")

        texts = [[] for _ in range(request.source_pages)]
        artifacts: list[dict[str, object]] = []
        for position, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            index = page.get("page_idx", position)
            if not isinstance(index, int) or isinstance(index, bool):
                index = position
            if not 0 <= index < request.source_pages:
                raise ConversionError(
                    f"MinerU returned page {index} for {path.name}, which has "
                    f"{request.source_pages} page(s)"
                )
            blocks = page.get("para_blocks") or page.get("blocks") or []
            for block in blocks if isinstance(blocks, list) else []:
                if text := _block_text(block):
                    texts[index].append(text)
                _collect_images(block, index, root, artifacts)
        segments = [Segment("\n\n".join(parts)) for parts in texts]
        if not any(s.text.strip() for s in segments):
            raise ConversionError(f"MinerU produced no text for {path.name}")
        return Conversion(
            segments=segments,
            method=PageMethod.MINERU,
            engine=self.fingerprint,
            artifacts=artifacts,
        )


# ------------------------------------------------------------------- blocks
def _span_text(span: object) -> str:
    if not isinstance(span, dict):
        return ""
    kind = span.get("type")
    value = span.get("html") if kind == "table" and span.get("html") else span.get("content")
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    if kind == "inline_equation":
        return f"${text}$"
    if kind == "interline_equation":
        return f"\n$$\n{text}\n$$\n"
    return text


def _lines_text(lines: list[object]) -> str:
    out = []
    for line in lines:
        spans = line.get("spans") if isinstance(line, dict) else None
        if isinstance(spans, list):
            out.append(" ".join(t for t in map(_span_text, spans) if t))
    return " ".join(t for t in out if t).replace(" \n", "\n").replace("\n ", "\n")


def _block_text(block: object) -> str:
    """The Markdown of one block: its lines, or its child blocks (an image with a caption)."""
    if not isinstance(block, dict):
        return ""
    if isinstance(block.get("lines"), list):
        text = _lines_text(block["lines"])
    elif isinstance(block.get("blocks"), list):
        text = "\n\n".join(t for t in map(_block_text, block["blocks"]) if t)
    elif isinstance(block.get("content"), str):
        text = block["content"].strip()
    else:
        text = ""
    if text and block.get("type") == "title":
        level = block.get("level")
        depth = level if isinstance(level, int) and not isinstance(level, bool) else 1
        return f"{'#' * min(max(depth, 1), 6)} {text}"
    return text


def _relative(value: str, root: Path) -> str | None:
    """An image path as a path inside the output folder; None when it points elsewhere."""
    raw = Path(value)
    if raw.is_absolute():
        try:
            raw = raw.relative_to(root)
        except ValueError:
            return None
    if ".." in raw.parts:
        return None
    for candidate in (raw, Path("images") / raw):
        if (root / candidate).is_file():
            return candidate.as_posix()
    return raw.as_posix()


def _collect_images(block: object, page: int, root: Path, out: list[dict[str, object]]) -> None:
    if isinstance(block, dict):
        for key in ("image_path", "img_path"):
            if isinstance(value := block.get(key), str) and (rel := _relative(value, root)):
                out.append({"kind": "image", "page": page, "path": rel})
        for key in ("blocks", "lines", "spans"):
            children = block.get(key)
            for child in children if isinstance(children, list) else []:
                _collect_images(child, page, root, out)
