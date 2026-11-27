# Page OCR versus MinerU: an engineering comparison

**Status: design note, and not a controlled benchmark.** The two paths below were measured on
different machines. The page OCR numbers come from an Apple M4 Pro laptop, and the MinerU numbers
come from the Linux GPU workstation. The models and runtimes differ too. Do not read the numbers as
a ranking. The MinerU path is a proposal and is not part of `docingest` today (see
[rag-multimodal-upgrade.md](../../../docs/rag-multimodal-upgrade.md)). The MinerU measurements come
from LaloAsetsu.

For a controlled comparison of OCR models, read [benchmark.md](benchmark.md).

## The two paths

| Concern | Page OCR (`docingest ingest`) | MinerU 4 (proposed) |
|---|---|---|
| Unit of work | One PDF page | The whole document |
| Engine | `mlx-vlm` VLM in the same process, Apple Silicon | `mineru-kit` with a local `llama-cpp-engine`, on Linux |
| Routing | Per page: keep a usable text layer, OCR only the pages that need it | The parser runs over the document with a `tier` and an `ocr_mode` |
| Main output | Normalized Markdown and a page manifest | Markdown, manifest, bounded raw parser output and optional layout JSON |
| Layout | Page routing data; OCR text is mostly linear | Blocks, equations, tables, figures and images with page numbers can be kept |
| Fit for circuit work | Needs a later extraction step on text | A better start, since layout blocks and image references survive |
| Best platform | Apple Silicon | Linux GPU workstation |
| Failures | Guards catch truncation, loops and empty output, with retries | Subprocess and output errors stay visible |

Page OCR is an adapter for one page. MinerU is a document converter with OCR and layout analysis.
That difference matters more than the library names: the second path gives a richer intermediate
form.

## Measurements

### Page OCR

The page OCR figures are not repeated here. They change with every run and live in
[benchmark.md](benchmark.md), which is the single place for them.

### MinerU

An 8-page corpus PDF was processed with `tier = "standard"`, `ocr_mode = "ocr"`, the local
`llama-cpp-engine` and `middle_json` output. The direct MinerU run took 251.4 seconds, about 31.4
seconds per page, and wrote a 120,187-byte JSON file.

A smoke test through the proposed project command also finished in `flash`/`txt` mode in 7.6
seconds. That mode reads the existing text layer of a PDF. It is not an OCR measurement.

A run with `standard`/`ocr` through the project command reached VLM inference and then ended with
exit code `-11` on its second local call. This is a runtime stability problem and is recorded as
one. The standalone MinerU `standard`/`ocr` command worked on the same machine and input.

These numbers come from one machine and one document. They cannot be compared with the benchmark
results, which use other hardware, other models and a scored corpus.

## What the numbers do and do not say

- The two paths differ in the form of their output: MinerU keeps structure (blocks, tables,
  equations, figures with page numbers) where page OCR yields mostly linear text. This is a
  statement about form. It is not a claim about character-level accuracy, which needs the same
  corpus, the same class of hardware, fixed models and scoring against ground truth.
- For a born-digital PDF with a good text layer, MinerU `flash`/`txt` is fast. It does not OCR
  scans.
- Page OCR is selective by page, so it does less work when only a few pages need OCR. It is also
  simpler when the goal is clean page text only.

## Where each path could fit (not a recommendation)

1. Scans, images, tables, figures, equations and circuit research: MinerU with `standard`/`ocr`
   keeps the structured output, if the lead approves it.
2. Born-digital PDFs with a good text layer: the regular ingestion path.
3. Apple Silicon, page-selective OCR, text only: page OCR with `mlx-vlm`.

For PDFs, the proposed adapter could look at the text layer first. A PDF with text would use
`flash`/`txt`, and a PDF without text would use the OCR settings. A scan must never be silently
downgraded to text-only mode.

## MinerU is optional, and so are its weights

MinerU would be an opt-in tool, off by default. The MinerU license adds terms to Apache-2.0, and
the weights have their own licenses. Only the Apache-2.0 weights (`MinerU2.5-Pro-2604-1.2B`) are
acceptable. The details are in the licenses section of
[rag-multimodal-upgrade.md](../../../docs/rag-multimodal-upgrade.md#licenses).

## Errors seen with MinerU

- **Missing Markdown.** Call `mineru-kit parse` with `--format markdown` and an output path inside
  a writable folder made for that document. The adapter should report a `ConversionError` when the
  file is missing or empty.
- **Missing structured JSON.** Use a format that writes it (`middle_json` or `zip`). Markdown mode
  is enough for text, but a setting that requires layout data must reject a Markdown-only result.
- **Exit code `-11`.** A native crash, not a Markdown error. Check RAM and VRAM, run one document
  at a time, and cut the input to one page to reproduce. Check the tier and the model files, and
  test the direct command apart from the Python wrapper. In the Linux run the direct command worked
  and a repeated call through the wrapper crashed.
- **Repeated crashes.** Restart the worker between documents if memory is not released, keep
  concurrency at one, and prefer `flash`/`txt` for PDFs with a text layer.

## Reproducing the measurements

Page OCR benchmark: follow [benchmark.md](benchmark.md#reproduce). From `packages/docingest`:

```bash
./scripts/setup_bench_scorer.sh
uv run docingest bench prepare --preset screen
uv run docingest bench run --preset screen --run-id screen
uv run docingest bench report --run-id screen
```

Direct MinerU parse, with `$PAPER` and `$OUT` set to your own paths:

```bash
mineru-kit parse "$PAPER" \
  -o "$OUT/parsed" \
  --format middle_json \
  --tier standard \
  --ocr-mode ocr
```

The project command for the second path does not exist yet, so there is no project smoke test to
repeat.
