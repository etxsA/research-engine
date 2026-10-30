# tests/

The pytest suite for `docingest`. It is organised by how much of the real world a test touches: unit tests run the domain and the application services against in-memory fakes, contract tests run the same behavioural checks against every implementation of a port, and integration tests run real adapters against real files and local HTTP servers. Tests that need multi-GB models or live remote services are opt-in.

For the architecture being tested (ports, adapters, services, composition root) see [docs/architecture.md](../docs/architecture.md) and [src/docingest/README.md](../src/docingest/README.md). For the port Protocols the fakes implement see [src/docingest/ports/README.md](../src/docingest/ports/README.md).

## Running the tests

| Command | What runs |
|---|---|
| `uv run pytest` | The default suite: everything except tests marked `model` or `network` (those are skipped with a reason) |
| `uv run pytest tests/unit` | Unit tests only (fastest) |
| `uv run pytest tests/contract` | Contract tests only |
| `uv run pytest tests/integration` | Integration tests only |
| `uv run pytest -m "not slow"` | Deselects the `slow` tests |
| `uv run pytest --cov` | The default suite with coverage; fails below the threshold (see [Coverage](#coverage)) |
| `./scripts/check.sh` | Every quality gate, including `pytest --cov` (see [scripts/README.md](../scripts/README.md#checksh)) |
| `DOCINGEST_NETWORK_TESTS=1 uv run pytest -m network` | Only the live-network tests |
| `DOCINGEST_MODEL_TESTS=1 uv run pytest -m model` | Only the model tests (Apple Silicon, `mlx` extra installed) |
| `DOCINGEST_LATEX_SAMPLES=/path/to/sources uv run pytest tests/integration/test_pandoc_latex.py -k real_arxiv_sources` | The optional real arXiv LaTeX sources |

`pyproject.toml` sets `testpaths = ["tests"]` and `addopts = ["--strict-markers", "-ra"]`: `-ra` ends the run with a short summary of every test that did not pass, including each skip and its reason, and `--strict-markers` turns an unregistered marker into an error.

## Layout

```text
tests/
├── conftest.py      opt-in marker gating, sys.path setup, the cfg fixture
├── fakes.py         in-memory fakes for the ports (behaviour, not mocks)
├── builders.py      tiny document builders: minimal PDFs and image PDFs
├── timing.py        assert_linear (cost grows in proportion to the input) and timeout_notes
├── unit/            domain rules and application services with fakes
├── contract/        one behavioural test suite per port, run against fakes and real adapters
├── integration/     real adapters, real files, local HTTP servers, the CLI
└── fixtures/        recorded or hand-made inputs
    ├── arxiv/       trimmed arXiv API (Atom) and OAI-PMH responses
    ├── bench/       an olmOCR-bench subset layout and a recorded scorer output
    └── latex/       a multi-file LaTeX paper with known traps, a REVTeX-style paper, and a malformed .tex file
```

A local checkout may also contain an empty `tests/e2e/` directory. Git does not track empty directories, and pytest collects nothing from it.

There is no `__init__.py` in `tests/` or its subdirectories. Pytest therefore imports each test module by its file name, so **every test file name must be unique across the whole `tests/` tree**. `conftest.py` puts `tests/` on `sys.path`, which is why tests import helpers as `from fakes import ...` and `from builders import ...`. Pytest also puts the directory of each collected test module on `sys.path`, so a test module can import helpers from another module in the same directory: `test_arxiv_fixes.py` imports `FakeTransport`, `FakeClock` and friends from `test_arxiv`, and `test_bench_fixes.py` imports `MemSuite`, `EchoOcr`, `runner` and `spec` from `test_benchmark`.

## Test layers

The diagram orders the layers by how much of the real world a test touches, from the opt-in tests at the top (real model weights, live services) to the unit tests at the bottom (nothing but Python objects in memory). Unit tests are the large majority of the suite. Of the three directories, `tests/contract` is the smallest, because each contract test is written once and runs once per implementation. The opt-in tests are not a directory: they are individual tests or parameters inside `tests/contract` and `tests/integration` that carry a `model` or `network` marker.

```mermaid
flowchart TD
    OPT["Opt-in: tests marked model or network (minutes, multi-GB weights or live services)"]
    INT["Integration: tests/integration (real adapters, real files, local HTTP servers, CLI)"]
    CON["Contract: tests/contract (one test suite, every implementation of a port)"]
    UNI["Unit: tests/unit (domain and services with in-memory fakes, milliseconds)"]
    OPT -->|fewer real dependencies| INT
    INT -->|fewer real dependencies| CON
    CON -->|fewer real dependencies| UNI
```

| Layer | Directory | Uses | Proves |
|---|---|---|---|
| Unit | `tests/unit` | `fakes.py`, fake transports and clocks defined in the test module, stub modules, `tests/fixtures/arxiv`, and `builders.py` in `test_bench_fixes.py` | Domain rules (routing, text clean-up, metadata), application services (ingest, ask, crawl, benchmark runner, statistics), composition-root wiring |
| Contract | `tests/contract` | `fakes.py` and the real adapters, parametrized | A fake and a real adapter behave the same way through the port, so unit tests built on the fake stay meaningful |
| Integration | `tests/integration` | Real adapters, `builders.py`, `tests/fixtures`, local `ThreadingHTTPServer` stand-ins, `typer.testing.CliRunner` | Adapters against real libraries (pypdfium2, Pillow, pandoc, pylatexenc, urllib), and the CLI end to end (with a fake OCR engine wherever OCR would run) |
| Opt-in | Marked tests inside contract and integration | Real model weights, arxiv.org, Hugging Face | The in-process MLX engine, the live arXiv API, the olmOCR-bench dataset download |

This diagram shows which shared helpers each layer draws on.

```mermaid
flowchart LR
    FK["tests/fakes.py"] --> U["unit tests"]
    FK --> C["contract tests"]
    FK --> I["integration tests"]
    AD["real adapters in src/docingest/adapters"] --> C
    AD --> I
    BL["tests/builders.py"] --> U
    BL --> I
    FX["tests/fixtures"] --> U
    FX --> I
    CF["cfg fixture in conftest.py"] --> U
    CF --> I
```

## Test modules

### unit/

| Module | Covers |
|---|---|
| `test_arxiv.py` | `ArxivCrawler` against a fake transport and a fake clock: feed parsing, id normalisation, query parameters, retries, source format detection. No network, no real sleeping |
| `test_arxiv_fixes.py` | Regression tests for the arXiv crawler, same fakes |
| `test_ask_whole_blocks.py` | `AskService` with `whole_blocks` on fakes and invented papers: an equation, a circuit and a table cut at either end and completed, the caption that follows (also when a chunk stops between the block and its caption), the bound (three chunks to the character, the caption left out first, then a note), no text added twice (neighbouring chunks, the better ranked chunk taking a hole), the chunks in their order, the order against the gate lists, the lookup and the headers (the list right after the completed circuit, an equation made whole not added again, header then note then text), the pages and name of an extended chunk, only the chunks the prompt carries, a stale chunk, the failures (a Markdown that cannot be read, a chunk that cannot be extended, a planner that fails: one warning per paper, the answer still comes), the setting (the key turns it on, `skip_summary` makes it available, off by default and the lookup then as before) and the choice per question (`whole_blocks=True` or `False`, refused without `skip_summary` or without the index), and linear time |
| `test_ask_equation_lookup.py` | `AskService` with `equation_lookup` on fakes and invented papers: the chunk of the asked number added behind the best chunk of its paper, two papers with the same number, a paper that does not tag it, a retrieved chunk that already holds the equation (labelled, not added), the bounds (two per number, four in all, the first pick of every number first), the places (the lowest-ranked chunk replaced, never the best or a labelled one, free places kept, `answer_max_sources` smaller than the chunks), what is read and when, the failures (one warning per paper, the answer still comes), the headers, the gate lists made before the header, no paper leaving the prompt when chunks are replaced, an index older than the Markdown (stale offsets), an equation cut by a chunk boundary (extended back to its start, or called the end of the equation), the title gate (four shared content words, English and Spanish, nothing read for a paper that fails, nothing added when none passes), the lead sentence before the equation and its bound, the switch from `skip_summary`, and linear time |
| `test_ask_gate_lists.py` | `AskService` with `circuit_lists` on fakes: the list right after a block in the chunk that holds it, the whole block's list in a chunk that holds only its end, none for a block the chunk cuts, none for display math, the chunks unchanged with the switch off or for other papers, the Markdown read once per paper and only for a chunk with a fence, a changed document left alone, a chunk that starts where a block ends, a failing Markdown or reader that leaves the chunks as they are and warns once per paper, and that `Container.ask` turns it on with `[qa] skip_summary` |
| `test_ask_session.py` | `AskSession` on fakes: the index not closed and the corpus not listed again between questions, the reload when (and only when) the generation changes, an index without generations reloaded on request, `warm_up`, the structured result (answer, contexts, sources, warnings, timings) also with the `skip_summary` additions, options that change from one question to the next, the phases, the warnings of the corpus listing, the store reads kept off the event loop thread, a commit that lands during a question, errors that leave the session usable, one retrieval at a time and answers that overlap |
| `test_ask_lines.py` | The JSON-lines front of the session: every request field and its refusals, one answer line per request in order with the id echoed, `return_contexts`, error lines for a bad line or a failing question, and the end of the input |
| `test_ask_retrieval.py` | `AskService` with fakes: the plain path unchanged, the two-stage retrieval (one query embedding, `candidates`, rerank by a stable sort, `contexts`, no reranker), the keyword override, the errors for an empty, uncommitted or foreign index, the warnings, and that the index is closed |
| `test_bench_fixes.py` | Benchmark regressions: clustered statistics, the OCR retry ladder in telemetry, page furniture and LaTeX / `<img>` normalisation, stale saved scores. The MLX adapter runs against stub modules, never a model |
| `test_benchmark.py` | `BenchmarkRunner`, score persistence and report aggregation on an in-memory suite (`MemSuite`) |
| `test_bootstrap.py` | `REGISTRY` defaults, unknown adapter names, `Container` overrides, entry-point plugins including broken ones, the `none` retrieval adapters and the `[index]`, `[embedder]` and `[reranker]` tables reaching a plugin |
| `test_blocks.py` | `domain.blocks`: display math, code blocks and pipe tables and where they open and close (what is never a block: pandoc's escaped brackets, a stray dollar, an unclosed fence), their captions, `whole_blocks`, `complete` (each kind cut at either end, the bound to the character, text held twice, the better ranked chunk taking a hole, the note), a property check on the extensions, and linear time as a ratio of CPU times |
| `test_circuits.py` | `domain.circuits`: the exact gate list of each construct (invented circuits: gates, controls and targets, swaps, measurements, multi-wire gates, labels, macros that must be `unparsed`), code-block finding and `add_gate_lists`, that no input raises and every item is a known operation or `unparsed`, and the linear-time ratio tests |
| `test_circuit_steps.py` | `domain.circuits` as data (`GateList.circuits`): the text written again from the fields alone and compared with `gate_list(...).text` on every construct, prefix, random mix and edit of the text tests, the fields of each construct, `CircuitSteps.to_dict` (`circuit/1`), and the size of the data |
| `test_circuit_consumption.py` | The consumption check of `domain.circuits`: an unknown command or a random text token injected at random places into every fixture circuit (quantikz, Qcircuit, yquant) is never `parsed`, a token that is only spacing leaves the list unchanged, a token no rule consumed is an unparsed item that names it, text on a row that draws no wire, the type of a quantikz `\wire` and a label on a vertical one, the fill of a box style, a `\cw` and what is drawn on a classical wire, text outside the circuit environments of a block (and the wrappers that are let through), `\linethrough` and a lone vertical wire, the words for an arrow between circuits, and the ket samples shared with quantum-sim |
| `test_chunking.py` | `domain.chunking.chunk_pages`: chunk names and page ranges, overlap, page breaks, invalid settings |
| `test_core_fixes.py` | Regressions: config loading, the pipeline version against `pyproject.toml` and `uv.lock`, QA LLM parameters (including the `skip_summary` and `whole_blocks` keys: defaults, validation, no cache key), metadata refresh and degraded results through the real `FilesystemStore` |
| `test_domain_models.py` | `SourceMetadata` citation formatting |
| `test_dot_leaders.py` | `collapse_dot_leaders`: contents lines with and without leaders, section numbers, ellipses of prose and Roman numerals; and where `IngestService` applies it (text-layer, structured, OCR and image pages, not converted kinds) |
| `test_equations.py` | `domain.equations`: the numbers a question names (English and Spanish, spellings, joiners, ranges, the cap of four) and the prose that names none, `canonical`, where a paper tags a number and where its display equation starts, the chunk picked when two hold the tag, the header (also for an equation cut by a boundary), units and counted nouns that are not numbers, the content words and the title overlap, the sentence before an equation, and linear time |
| `test_index_config.py` | `index_config`: `candidates` and `contexts` read without the index adapter, other keys ignored, bad values named |
| `test_index_service.py` | `IndexService`: incremental `build`, `update` of given documents, pruning, `status`, `remove`, the mismatch refusal, and `close()` on every path including a failing embedder |
| `test_blank_pages.py` | The blank-page rule: ink measurement (white, tinted and speckled paper, a page number, a title page, a dark page), `is_blank` and its thresholds, the `[routing]` keys, and `IngestService` leaving such pages empty without calling OCR |
| `test_ingest_service.py` | `IngestService` with fakes for every port: per-page routing, the cache and its invalidation, partial and forced-OCR variants, images, converters, metadata sidecars, degraded conversions, domain errors |
| `test_latex_fixes.py` | Regressions of the LaTeX adapter (`latex_source`, `pandoc_latex`) |
| `test_latex_sections.py` | Section-by-section conversion and the reference-loss guard, with pandoc replaced by a rule (counts the runs, the budget, the documents handed to pandoc), and the string helpers behind them |
| `test_latex_circuits.py` | Quantum circuits as LaTeX code blocks (`protect_circuits`): each encoding, boxes, TikZ pictures, table cells, what is left alone (math, preamble, listings), circuits that never close, linear time, and the plain-text path's block |
| `test_latex_equations.py` | Equation numbers written as `\tag` and the references to them (`number_equations`): each environment, `\nonumber`, `\tag`, `subequations`, `\numberwithin`, appendices, `\theequation` forms, counter commands, the macros of the paper, what stops the numbering, verbatim text and circuits left alone, malformed input, and linear time |
| `test_latex_pandoc_rewrites.py` | The source rewrites that keep pandoc 3.9 from failing: `@`-macro shim, REVTeX reference entries, citation keys, small layout forms |
| `test_latex_source.py` | Pure LaTeX-source helpers and the pandoc adapter's Markdown helpers |
| `test_metrics.py` | `application.metrics`: normalisation (Markdown, hyphenation), capping of runaway output, `char3_f1`, `bootstrap_ci` |
| `test_ocr_guards.py` | The OCR output checks of `domain/ocr_guards.py`: loops (and the tables, matrices, contents pages and reference lists that must not count as one), the capacity of a page, the verdict order, the `max_tokens` raise, repeated pages |
| `test_ocr_ladder.py` | `run_ladder` with scripted replies: stricter retries, more room (same settings) for a dense page, a refused larger limit, the least bad attempt, olmOCR's own ladder |
| `test_ocr_preflight.py` | The start-up check: `check()` of both engines (a missing `mlx_vlm`, an unreachable server, a refused key), the ingest service checking once before the first page that needs OCR and never for text-layer, blank or cached work, `docingest ingest` stopping with one message, the crawl stopping |
| `test_ocr_profiles.py` | OCR profile clean-up code pinned against each profile's `code_version` (an edit fails until it is re-pinned, with a bump if results can change), and `code_version` in both engine fingerprints |
| `test_ocr_rejection.py` | What `IngestService` does with OCR output it cannot trust: the text layer if there is a usable one, else an empty page, a warning in the manifest, pages that repeat an earlier page, truncation, images |
| `test_routing_policy.py` | `domain.routing.decide`, configurable `RoutingPolicy` thresholds, the scan-with-a-text-layer rule and its two keys, unknown `[routing]` keys rejected |
| `test_math_routing.py` | The math rule (off by default): the page read by OCR with its reason, the rule off, each threshold at its edge, `min_chars` as the floor, a page under both, a low-alphabetic page and a scan that also match (both reasons), the math rule without the scan rule, moved thresholds, the text layer as the fallback, the keys validated, the cache key (the three keys in it only while the rule is on, and the default key equal to the one from before the rule) |
| `test_scan_layer_routing.py` | Scans that carry an OCR text layer through `IngestService`: read by OCR with the reason in the manifest, born-digital and figure pages untouched, the rule off, the text layer as the fallback when the OCR output is rejected, no text-layer structure for a page read by OCR and flat text for the fallback page, the keys in the PDF cache key |
| `test_services.py` | `AskService` (plain path) and `CrawlService` with fakes |
| `test_stats.py` | `application.stats`: paired bootstrap, `quantile`, `bootstrap_ci` |
| `test_timing.py` | `tests/timing.py` on a fake clock: linear work passes after three rounds, quadratic work fails all three attempts, a burst of load in one attempt or one round is not held against the code, the floor and the ceiling, the collector switched off and back, `timeout_notes` |
| `test_text.py` | `domain.text`: garbled-text detection, the math-symbol and equals-sign counts (the edges of every symbol block, `ϕ`, `ˆ`, `™`), de-hyphenation, page splitting |
| `test_structure.py` | `domain.structure`, `furniture` and `headings` on synthetic lines: running headers and page numbers, paragraphs, hyphenation, headings, footnotes |
| `test_qasm.py` | `domain.qasm.to_qasm`: exact programs for each gate, control, open control, measurement, barrier, parameter and sign; the header, origin and part suffix; `values` bindings; and every refusal (an unparsed circuit, an unknown kind, an out-of-range wire, an unbound symbol, `U`, `R_k`, `iSWAP`, a controlled measurement) |
| `test_qasm_export.py` | `QasmExportService`: selection by id prefix, source name and ambiguity; file naming and content; `--set`; refusals that do not stop the other circuits; warnings for no circuits and an unreadable Markdown; the output override; and the `export_documents` path the post-ingest hook uses |
| `test_quantum_sim_fixtures.py` | The `circuit/1` documents saved in `packages/quantum-sim/tests/fixtures/bridge/` are what `gate_list` returns for the LaTeX sources next to them (the other half of quantum-sim's bridge tests; docingest never imports `quantum_sim`), and an unknown command or a text token put in any of those sources is never read as `parsed` |
| `test_wire_names.py` | docingest's copy of quantum-sim's wire-name grammar (`_WIRE_NAME`) gives the same verdict as quantum-sim on every sample of `packages/quantum-sim/tests/fixtures/wire_names.json`; a word in `\text` or `\mathrm` is never a name |

### contract/

| Module | Implementations under test |
|---|---|
| `test_ocr_contract.py` | `FakeOcr`, `OpenAICompatibleOcr` against a local stub `/v1/chat/completions` server, `MlxVlmOcr` with a pinned Qwen3-VL-2B 4-bit model (`model` marker) |
| `test_retrieval_contract.py` | `FakeEmbedder`, `FakeIndex` and `FakeReranker` (from `fakes.py`): the `Embedder`, `ChunkIndex` and `Reranker` contracts. The [`docingest-index`](../../docingest-index/README.md) package imports this module and runs it against its real adapters (see [Plugin tests](#plugin-tests-reusing-the-contracts)) |
| `test_store_contract.py` | `InMemoryStore` (from `fakes.py`) and `FilesystemStore` |
| `test_qasm_contracts.py` | The golden `circuit/1` fixtures and their exact `.qasm` programs: each parses with `openqasm3`, the text is UTF-8/LF and deterministic, every `GateOp.kind` is handled or refused, the emitter imports only the standard library, and a seeded property test parses the programs of generated circuits |

### integration/

| Module | Covers |
|---|---|
| `test_arxiv_network.py` | `ArxivCrawler` over real HTTP against a local stand-in server (redirects, gzip, retries with `Retry-After`); one live arXiv test (`network`) |
| `test_bench_datasets.py` | The synthetic and olmOCR-bench suite adapters, scorer output parsing, and the `docingest bench` sub-app end to end with a fake OCR engine; one dataset download test (`network`) |
| `test_ask_cli.py` | `docingest ask` through the real CLI and store with fake plugins: the plain path, the index path, `--no-index`, `--bm25`, `--stdin` (one process, one open index, standard output only the answer lines, the usage errors), and the messages for an unbuilt index, another embedder and an index without an embedder |
| `test_cli.py` | `docingest ingest` batch behaviour, option validation, the `adapters` command, a missing `--config` file (pipeline and `bench` commands), a `crawl` query that cannot be sent, a broken plugin |
| `test_chunking_parity.py` | `chunk_pages` gives the same chunks as PaperQA2's `chunk_pdf` on the repository's own documents, an ingested LaTeX paper and random pages (hypothesis); skipped when `paperqa` is not installed |
| `test_filesystem_store.py` | `FilesystemStore` file permissions, no leftover temp files, a corrupt cached manifest treated as a cache miss |
| `test_index_cli.py` | `docingest index build`, `status` and `remove` with fake plugins |
| `test_index_on_ingest.py` | `ingest --index` and `crawl --index`: off by default, only the papers of the run, checked before any work, a failing update keeps the papers |
| `test_qasm_cli.py` | `docingest circuits export` end to end: ingest a Markdown circuit, select it, set `--out` and `--set`, read the `.qasm` on disk, the exit codes for a refusal, `--allow-partial`, no circuit and an unknown document, and `ingest --qasm` / `crawl --qasm` as best-effort hooks |
| `test_magic_detector.py` | `MagicBytesDetector` on real files |
| `test_openai_ocr.py` | `OpenAICompatibleOcr` against a scripted local OpenAI-compatible server: request shape, retries, the retry ladder (stricter retries, more room for a dense page, a server that refuses it), authentication |
| `test_ask_equation_lookup_paperqa.py` | The whole ask path with a mocked LLM and the real `PaperQAAnswerer`: the answer prompt holds the header and the chunk of each paper's Eq. (2) with `skip_summary` and no request holds them without it, the sentence about equation numbers is in the copy rule once, the prompt keeps its size, the retry with fewer sources drops the lowest-ranked chunks and keeps the best paper's, a failing lookup still answers |
| `test_ask_whole_blocks_paperqa.py` | The whole ask path with a mocked LLM and the real `PaperQAAnswerer`: the answer prompt holds the rest of a cut circuit, its gate list and its caption with `skip_summary` and no request holds them without it, the prompt grows by exactly the added characters, a table over three chunks keeps its note and does not grow, the retry with fewer sources keeps the completed best chunk, a failing completion still answers, and with `whole_blocks` off the prompt is the one without the completion |
| `test_ask_gate_lists_paperqa.py` | The whole ask path with a mocked LLM and the real `PaperQAAnswerer`: the answer prompt holds the gate list after the block with `skip_summary` and no request holds one without it |
| `test_pandoc_latex_circuits.py` | Circuits through the real pandoc and pylatexenc: each encoding with its caption, boxes, subfigures, a table cell, math not doubled, and the same block from the whole-paper, section-fallback and plain-text paths |
| `test_pandoc_latex_equations.py` | Equation numbers through the real pandoc and pylatexenc: the numbers and the references in the Markdown, subequations, appendix and `\numberwithin`, an own `\tag`, a circuit next to an equation, and the same numbers from the whole-paper, section-fallback and plain-text paths |
| `test_pandoc_latex_sections.py` | A paper with one broken section (that section is plain text, the others pandoc), page records, malformed and slow input, a reference list pandoc drops (real pandoc and pylatexenc) |
| `test_pandoc_latex.py` | `PandocLatexConverter` with the real pandoc (pypandoc-binary) and pylatexenc on `tests/fixtures/latex`; optional real arXiv sources (`slow`) |
| `test_paperqa_adapter.py` | PaperQA2 token windows (needs the embedder in the local Hugging Face cache); answering from given `contexts` (only those chunks, an empty list answered by PaperQA2's own "cannot answer" text with no model request, the temperature on every request; with `[qa] skip_summary` the raw chunk text with its `$$` blocks reaches the answer prompt, no summary request is sent, keys and page ranges are cited, the retrieval order decides the `answer_max_sources` cut; the answer prompt gains the pinned copy rule and nothing else changes, system prompt and citation format included; without it every chunk is summarized and no request carries the rule) and from the corpus (where `skip_summary` only warns), the retry with half the sources when the server answers the context-length 400 (the rank order kept, the warning, 10, 5, 2, 1, the last error raised, other 400s and the summaries path not retried, the log record of a retried overflow held back for that ask only; once with a stub OpenAI-compatible server on localhost), with litellm's mock reply and PaperQA2's keyword embedder, so no server or download. Skipped when `paperqa` is not installed |
| `test_pdfium_reader.py` | `PdfiumReader` page signals and routing on generated PDFs (including a full-page image under an invisible text layer, a small figure, a plate, the equals-sign and math-symbol signals and the math rule on real pages), corrupt PDFs |
| `test_text_layer_structure.py` | Real pdfium on multi-page PDFs generated by `layout_pdf`: font size, weight and position of each line, the whole pipeline (running header and page number out, paragraphs joined, headings, hyphenation) |
| `test_pipeline_real_adapters.py` | `Container` with the real detector, pdfium, Pillow and filesystem adapters and a fake OCR engine; blank, page-number and scan-with-a-text-layer and math-page PDFs |
| `test_scripts.py` | `scripts/serve_llm.sh` run with a stub `uv` on `PATH`: which variable chooses the served model. Skipped without `bash` |
| `test_synthetic_and_images.py` | Deterministic `make_scan`, `fit_image`, OCR profile post-processing |

### fixtures/

| Path | Content | Used by |
|---|---|---|
| `arxiv/api_query.xml` | A trimmed arXiv API (Atom) search response | `test_arxiv.py`, `test_arxiv_fixes.py`, `test_arxiv_network.py` |
| `arxiv/api_error.xml` | A trimmed arXiv API error response | `test_arxiv.py` |
| `arxiv/oai_2409.13740.xml` | A trimmed OAI-PMH record (license lookup) | `test_arxiv.py`, `test_arxiv_fixes.py`, `test_arxiv_network.py` |
| `bench/olmocr_subset/` | `subset.json` (dataset `repo_id`, `revision`, `categories`, `per_category`, `seed`, and SHA-256 checksums of the JSONL files and of the selected PDFs) and one `.jsonl` test file per category; the PDFs themselves are not included | `test_bench_datasets.py` |
| `bench/scorer_stdout.txt` | Recorded output of the official olmOCR-bench scorer | `test_bench_datasets.py` (`parse_scorer_stdout`) |
| `latex/paper/` | A multi-file paper with the traps seen in arXiv sources: a latin-1 include, a file that includes itself, a local `.sty` whose layout macro makes pandoc loop, a natbib `.bbl`, a commented-out include, verbatim code | `test_pandoc_latex.py` |
| `qasm/` | Four `circuit/1` documents (`bell`, `open-control`, `measure`, `parts`) and the exact OpenQASM 3 program the emitter must write for each; `parts` has two files, one per side of a drawn equality | `test_qasm_contracts.py` |
| `latex/revtex/` | An invented paper with a REVTeX-style `.bbl`: `@` helper macros, `\href` with a line break, `\href@noop`, `\Eprint`, and citation keys with an empty entry and a `$` | `test_pandoc_latex.py` |
| `latex/malformed.tex` | Unclosed group and `itemize`, for the fallback path | `test_pandoc_latex.py` |

## Shared helpers

### conftest.py

| Item | What it does |
|---|---|
| `sys.path.insert(0, ...)` | Puts `tests/` on the import path so `fakes` and `builders` import as top-level modules |
| `OPT_IN` and `pytest_collection_modifyitems` | Adds a skip marker to every test marked `model` or `network` unless the matching environment variable is exactly `1` |
| `cfg` fixture | `AppConfig(output_dir=str(tmp_path / "out"), raw_dir=str(tmp_path / "raw"))`: a default configuration whose outputs go to the test's temporary directory |

### fakes.py

The module docstring states the rule: the fakes are real implementations of the port contracts, not mocks. Tests assert on behaviour and results, not on which methods were called; the only bookkeeping is a few counters, flags and lists (`calls`, `opened`, `closed`, `seen`). `InMemoryStore` and `FakeOcr` run through the same contract tests as the real adapters.

| Fake | Port | Behaviour | Knobs and inspection |
|---|---|---|---|
| `FakeOcr(text="ocr text", model="fake/ocr")` | `OcrEngine` | Returns `OcrResult(text, seconds=0.01, gen_tokens=3, finish_reason="stop")` for any image | `calls`; `model` is `ModelRef(repo_id=model, revision="0" * 40)`; `dpi = 72`; `fingerprint` includes `model` and `text`, so two fakes with different settings produce different cache keys |
| `CheckedOcr(error=None)` | `OcrEngine` + `OcrPreflight` | A `FakeOcr` with `check()`, which raises `error` when given | `checks`, `error` (settable) |
| `ScriptedOcr(*texts, finish="stop")` | `OcrEngine` | A `FakeOcr` that answers call n with `texts[n]` (the last one repeats) and the given finish reason | `calls` |
| `FakePdfReader(docs)` | `PdfReader` | `docs` maps a file **name** to a list of pages; `open(path)` looks up `path.name` and raises `DocumentOpenError` for unknown names | `opened` lists the documents it returned; `fingerprint = "fake-pdf 1"` |
| `FakePdfDocument(pages, title=None)` | `PdfDocument` | `len()`, `page(i)`, `close()` | `closed` |
| `FakePage(signals_, text, image=None)` | `PdfPage` | `signals()` returns the given `PageSignals` and text; `render()` returns `image`, by default a white 850x1100 page | |
| `digital(text)` | helper | A `FakePage` whose `n_chars` is the number of non-whitespace characters of `text` and `alpha_ratio=0.9`. It routes to the text layer only when `text` has at least 50 such characters (the default `RoutingPolicy.min_chars`) | |
| `scanned(image=None)` | helper | A `FakePage` with no text and one full-page image (`n_chars=0`, `n_images=1`, `image_coverage=1.0`) whose rendering has a block of text on it (`inked()`); routes to OCR and is not blank | |
| `blank()` | helper | A `FakePage` with no text and a white rendering: the blank-page rule leaves it empty | |
| `make_service(docs, converters, ocr, images, **kw)` | `IngestService` | A service on `FakeDetector`, `FakePdfReader(docs)`, `InMemoryStore` and the given fakes; `kw` goes to the constructor (`policy`, `guards`) | |
| `FakeDetector()` | `TypeDetector` | Kind from the suffix only: `.pdf`, `.png`, `.docx`, `.tex`, `.md`; anything else raises `UnsupportedInputError` | `KINDS` |
| `FakeImages(n_frames=1, fingerprint="fake-images 1", size=(10, 10))` | `ImageSource` | Returns `n_frames` white images of `size` for any path | `fingerprint` (part of the image cache key) |
| `FakeConverter(segments, method=PageMethod.LATEX, **kw)` | `DocumentConverter` | Returns `Conversion(segments=segments, method=method, engine="fake", **kw)`; pass `title`, `metadata`, `warnings` or `degraded` through `kw` | `calls`; `fingerprint` includes the method |
| `InMemoryStore()` | `DocumentStore` | Reference implementation of the store contract: canonical results, variants (partial or forced-OCR runs) and degraded fallbacks kept apart; locations are `mem://...` strings | `canonical`, `variants`, `degraded` dictionaries |
| `FakeCrawler(records, payload=..., fail_keys=set())` | `SourceCrawler` | `search()` returns the first `limit` records; `fetch()` writes `<key>.tex` with `payload` into `dest_dir` and returns format `"latex"`; keys in `fail_keys` raise `DocumentOpenError` | |
| `record(key, title="A paper")` | helper | A `SourceRecord` with `SourceMetadata(title=title, arxiv_id=key, year=2024)` | |
| `add_doc(store, doc_id, pages, name=None)` | helper | Stores a text document in a store like an ingested one; `pages` is `[(text, section title)]` | returns the manifest |
| `FakeEmbedder(dims=16, fingerprint=..., query_instruction="")` | `Embedder` | A hashed bag of words (`crc32` of each word modulo `dims`): texts that share words get close vectors; the same text always gives the same vector | `calls` counts `embed_documents` calls |
| `FakeIndex(fingerprint=..., embedder_fingerprint=...)` | `ChunkIndex` | Reference implementation of the index contract: documents kept in memory, exact cosine search, ties in insertion order; `upsert` and `remove` are staged and only `commit()` makes them visible to `search` | `keys()` shows staged changes; `closed` counts `close()` calls and `keywords` records the `keywords` argument of each `search` |
| `FakeReranker()` | `Reranker` | Scores a chunk by the share of the question's words it contains | |
| `FakeQA()` | `QuestionAnswerer` | Async `ask()` returns `f"answer to {question!r} from {len(documents)} docs"` | `seen` holds the source names of the documents it received, `markdown` their Markdown texts, `contexts` the chunks it was given |

There is no shared fake for `BenchmarkSuite`. `tests/unit/test_benchmark.py` defines `MemSuite` (samples whose reference is their id, scored by exact match), `TaggedSuite` (images tagged with their sample id) and `EchoOcr` (a `FakeOcr` subclass that returns that id, can fail every Nth call and counts `unload()` calls), plus the `spec()` and `runner()` helpers for `BenchmarkRunner`. A new unit test in `tests/unit/` can import them with `from test_benchmark import ...`, as `test_bench_fixes.py` does.

### timing.py

Two helpers for the tests that would otherwise read a clock. Neither test layer reads the wall clock (the only seconds left are the CPU ceilings of `assert_linear`, which pin the cost of the old slow code): a test that asserts elapsed seconds fails on a loaded laptop and on a shared CI runner for reasons that have nothing to do with the code.

| Helper | What it does |
|---|---|
| `assert_linear(fn, small, big, *, factor=8, floor=0.05, ceiling=None, rounds=5, attempts=3)` | Passes when `fn(big)` costs less than `max(factor * cost of fn(small), floor)` in CPU time of the test process, `big` being four times `small`. Linear work costs about 4 times as much and quadratic work 16 times, so 8 separates them. `ceiling` is an extra absolute limit in seconds on `fn(big)`, kept by the tests that pin the cost of the old slow code. `run_job` is the `fn` for inputs that are already a function to call |
| `timeout_notes(conversion)` | The notes of a `Conversion` that say pandoc ran out of time (`pandoc timed out after Ns`, `no time left for this run`), from the paper's warnings and from those of each segment. Not every timeout leaves such a note, so it names the timeouts and `conversion.degraded` covers the rest |

How `assert_linear` copes with a machine that is not quiet:

1. The two sizes run one after the other, round by round (small, big, small, big, ...), so a burst of load slows both and not only the one that happened to run during it.
2. The fastest run of each size is kept. From the third round on, a check that passes stops there, so a quiet machine pays three rounds of each size.
3. A check that still fails after five rounds is measured again, up to three attempts in all, and the test fails only when every attempt fails, with every measurement in the message. Quadratic work fails every attempt.
4. The cyclic garbage collector is off during the runs, so a big heap (the other tests of a session import PaperQA2) does not make a linear scan look slower on the larger input.

What this does not catch is a slowdown of less than about twice that is still linear: these tests guard the growth of the cost, and speed belongs to the benchmark scripts. When you add a linear-time test, build both inputs first and pass them in, so building them is not measured:

```python
from timing import assert_linear


@pytest.mark.parametrize("make", UNITS.values(), ids=UNITS)
def test_reading_is_linear_in_the_size_of_the_source(make):
    assert_linear(gate_list, make(N // 4), make(N), ceiling=10)
```

For work that needs setup, make the setup part of the input: have a function build the store and return the function that runs the work, and pass `run_job` as `fn` (see `test_the_lookup_is_linear_in_the_size_of_the_paper`).

`packages/docingest-index/tests/test_latex.py` has the same method as a private function, because packages do not import each other's tests.

The integration tests that run the real pandoc, or a shell script standing in for it, check what the converter records and not how long it took. A hang ends in the converter's timeout, which marks the result `degraded`, so those tests assert that the result is not `degraded` (the converter sets it for every timeout that ends in a result, so it covers a paper whose pieces all time out) and `timeout_notes(conv) == []` (which says which run it was), next to the checks each test already had. `timeout_notes` alone would miss a paper whose pieces all time out, which ends with the first error as its only note. Two tests count instead of time: `test_a_slow_pandoc_cannot_take_longer_than_the_timeout_in_total` has the stand-in append a line to a log for every run it gets and asserts that the sections which started fit the shared budget (a slower machine starts fewer, so it still passes; without the shared budget all twelve start), and `test_timeout_falls_back_to_plain_text` has the stand-in write its PID, then asserts that the process is gone once `convert` returned.

### builders.py

Dependency-light builders for real input files. Every builder writes to the path you pass (use `tmp_path`) and returns it.

| Builder | Produces |
|---|---|
| `LONG` | A sentence repeated three times, long enough to pass the text-layer thresholds |
| `pdf(path, content, resources=b"", extra=(), page_box=b"/MediaBox [0 0 612 792]", *, font_encoding=b"")` | A minimal one-page PDF written byte by byte, with Helvetica as `/F1` (`font_encoding` is added to the font dictionary); `content` is the page content stream, `extra` adds objects numbered from 6 |
| `text_pdf(path, *lines)` | A born-digital one-page PDF with the given text lines (routes to the text layer when the text is long enough) |
| `image_pdf(path)` | A one-page PDF whose only content is a 1275x1650 image with text drawn in pixels, saved at 150 dpi (routes to OCR) |
| `blank_pdf(path, pages)` | A PDF with `pages` empty pages, made with pypdfium2 |
| `layout_pdf(path, pages)`, `paragraph(text, x=, top=, size=, width=)` | A multi-page PDF where every text run is placed by hand as `(x, y, size, bold, text)` (Helvetica or Helvetica-Bold); `paragraph` wraps a text into the runs of its lines |
| `pictures_pdf(path, boxes, *lines, invisible=False)` | A one-page PDF with the text lines and one image drawn over each box (x, y, width, height in points); boxes may overlap or tile the page. `invisible=True` draws the text in render mode 3, as an OCR program does over its scan |
| `symbols_pdf(path, *lines)` | `text_pdf` with a WinAnsi font, so a line may hold `±`, `×` or `÷` (the math signs that fit Latin-1) |
| `picture_pdf(path, *lines, box=(0, 0, 612, 792), invisible=False)` | A one-page PDF with an image over `box` (x, y, width, height in points; the full page by default) and the text lines; `invisible=True` draws the text in render mode 3, as an OCR program does over its scan, so the page has a text layer and shows only the image. A small `box` makes the image a figure |

## Markers and opt-in environment variables

Markers are registered in `[tool.pytest.ini_options]` of `pyproject.toml`. The gating is in `tests/conftest.py`.

| Marker or variable | Meaning | Enable with | Tests today |
|---|---|---|---|
| `model` | Loads multi-GB OCR weights | `DOCINGEST_MODEL_TESTS=1` | The `mlx-vlm` parameter of `test_ocr_contract.py`. It needs Apple Silicon with the `mlx` extra, and downloads the pinned model from Hugging Face if it is not cached |
| `network` | Talks to remote services (arXiv, Hugging Face) | `DOCINGEST_NETWORK_TESTS=1` | `test_live_arxiv_search_and_fetch` (a few requests, spaced as arXiv's terms require) and `test_prepare_subset_downloads_a_seeded_nested_sample` (olmOCR-bench download) |
| `slow` | Takes more than a few seconds | Runs by default; deselect with `-m "not slow"` | `test_real_arxiv_sources` in `test_pandoc_latex.py` |
| `DOCINGEST_LATEX_SAMPLES` | Folder of raw arXiv source downloads (`https://arxiv.org/src/<id>`), which cannot be redistributed and so are not in the repository | Point it at a folder containing `1706.03762.src`, `2409.13740.src`, `2303.08774.src` and `math_0211159.src` | Each missing file skips its own parameter of `test_real_arxiv_sources` |

The value must be exactly `1`: `DOCINGEST_MODEL_TESTS=true` does not enable the tests.

The gating as a flow:

```mermaid
flowchart TD
    COL["pytest collects a test"] --> MK["Has a model or network marker?"]
    MK -->|no| RUN["Runs"]
    MK -->|yes| ENV["Matching variable set to 1?"]
    ENV -->|yes| RUN
    ENV -->|no| SKIP["Skipped: set the variable to 1 to run it"]
```

Other conditional skips, independent of the variables above: tests that need pandoc skip when `PandocLatexConverter().pandoc_version` is `None` (no runnable pandoc binary; pypandoc-binary normally bundles one); a few pandoc tests need POSIX (a shell-script stand-in for pandoc, file permissions) or a non-root user; `test_paperqa_adapter.py` uses `pytest.importorskip("paperqa")`.

`DOCINGEST_TEST_OCR_KEY` appears in `test_openai_ocr.py`, but the tests set and remove it themselves with `monkeypatch`; you never set it. In the same way, the `qa_env` fixture of `test_core_fixes.py` removes `DOCINGEST_LLM`, `DOCINGEST_LLM_SERVE_MODEL` and `OPENAI_API_KEY` for its tests, and `test_scripts.py` runs `scripts/serve_llm.sh` without the `DOCINGEST_LLM*` variables or `PORT` of your shell, so values in your shell do not change their results.

### Adding an opt-in marker

1. Register it in `markers` under `[tool.pytest.ini_options]` in `pyproject.toml` (required by `--strict-markers`).
2. If it should be skipped by default, add `"<marker>": "DOCINGEST_<NAME>_TESTS"` to `OPT_IN` in `tests/conftest.py`.
3. Document it in the table above.

## Coverage

| Setting (`pyproject.toml`) | Value |
|---|---|
| `[tool.coverage.run] source` | `["docingest"]` |
| `[tool.coverage.run] branch` | `true` (branch coverage) |
| `[tool.coverage.report] fail_under` | `85` |
| `[tool.coverage.report] show_missing`, `skip_covered` | `true`, `true` |
| `[tool.coverage.report] exclude_also` | Regular expressions for `if TYPE_CHECKING:`, `raise NotImplementedError` and a literal `...` (Protocol method bodies) |

Coverage is measured only when pytest runs with `--cov` (`scripts/check.sh` and both CI jobs do). pytest-cov reads `fail_under` from this configuration, so the run fails when total coverage is below 85%. Model and network tests are skipped in CI, so the threshold has to hold without them: code reached only by an opt-in test still needs a default test.

## Continuous integration

`.github/workflows/ci.yml` (at the repository root) runs two jobs on every push and pull request, from this package's folder:

| Job | Installed | Test command | Consequence for tests |
|---|---|---|---|
| `linux` (ubuntu-latest) | Core and dev dependencies, no extras | `uv run --frozen pytest -q --cov` | Tests must pass without `mlx-vlm`, `paper-qa` or `docling`. Import optional libraries lazily or use `pytest.importorskip` |
| `macos` (macos-latest) | All extras | `uv run --frozen pytest -q --cov` | Optional adapters are importable; model and network tests are still skipped |

Ruff (lint and format) also runs over `tests/`, with `PLR0913`, `PT018` and `E501` ignored there (`[tool.ruff.lint.per-file-ignores]`).

## Writing tests

### Conventions

- **Fakes over mocks.** Build services from `fakes.py` and assert on results (the manifest, the stored Markdown, the counters), not on call sequences.
- **No network and no model in the default suite.** When an adapter talks HTTP, start a local server on `127.0.0.1` port 0 inside the test (see `stub_server()` in `test_ocr_contract.py` and `FakeOpenAIServer` in `test_openai_ocr.py`), or inject a fake transport and clock (see `FakeTransport` and `FakeClock` in `test_arxiv.py`). Anything that needs the real service gets the `network` or `model` marker.
- **Temporary files only.** Write inputs and outputs under `tmp_path`; use the `cfg` fixture when a test needs an `AppConfig`, so the store writes into the temporary directory.
- **Name tests after the behaviour**, for example `test_partial_run_never_replaces_complete` or `test_swapping_the_ocr_adapter_invalidates_the_cache`.
- **Regression tests** for a reviewed bug go into the matching `*_fixes.py` module, and the test name says what the old code got wrong.
- **Unique file names** across `tests/` (see [Layout](#layout)).

### Testing an application service

Services take their ports as keyword arguments, so a unit test builds one from fakes. This example runs as is when placed in `tests/unit/`:

```python
from fakes import FakeDetector, FakeImages, FakeOcr, FakePdfReader, InMemoryStore, digital, scanned

from docingest.application.ingest import IngestOptions, IngestService
from docingest.domain.models import PageMethod
from docingest.domain.routing import RoutingPolicy

TEXT = "Attention is all you need. " * 10


def test_scanned_page_is_transcribed_once_and_then_cached(tmp_path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"any bytes")  # hashed for the doc id; FakePdfReader looks pages up by name
    svc = IngestService(
        detector=FakeDetector(),
        pdf=FakePdfReader({"scan.pdf": [digital(TEXT), scanned()]}),
        ocr=FakeOcr(text="# Page 2"),
        images=FakeImages(),
        converters={},
        store=InMemoryStore(),
        policy=RoutingPolicy(),
        log=lambda _: None,
    )

    doc = svc.ingest(src)

    assert [p.method for p in doc.manifest.pages] == [PageMethod.TEXT_LAYER, PageMethod.VLM_OCR]
    assert "# Page 2" in svc.store.markdown(doc)
    svc.ingest(src)  # second run is served from the cache
    assert svc.ocr.calls == 1
    svc.ingest(src, IngestOptions(force=True))
    assert svc.ocr.calls == 2
```

For documents that go through a converter (LaTeX, office, text), pass `converters={SourceKind.LATEX: FakeConverter([Segment("Intro text", title="Introduction")])}` and give the input a matching suffix (`.tex`). `AskService` and `CrawlService` follow the same pattern; see `tests/unit/test_services.py`.

### Testing through the composition root

To exercise the real adapters with only the OCR engine replaced, give `Container` an override. This runs the real magic-byte detector, pdfium reader and filesystem store:

```python
from builders import LONG, text_pdf
from fakes import FakeOcr

from docingest.bootstrap import Container
from docingest.domain.models import PageMethod


def test_born_digital_pdf_uses_the_text_layer(cfg, tmp_path):
    svc = Container(cfg, log=lambda _: None, overrides={"ocr": FakeOcr("# OCR page")}).ingest
    doc = svc.ingest(text_pdf(tmp_path / "d.pdf", LONG))
    assert doc.manifest.pages[0].method == PageMethod.TEXT_LAYER
```

`overrides` accepts any port name used by `Container.adapter()` (`"detector"`, `"pdf"`, `"ocr"`, `"images"`, `"office"`, `"latex"`, `"text"`, `"store"`, `"qa"`, `"crawler"`). To select a fake by adapter name instead, as the configuration would, patch the registry:

```python
from fakes import FakeOcr

from docingest import bootstrap
from docingest.bootstrap import Container


def test_select_a_fake_adapter_by_name(monkeypatch, cfg):
    monkeypatch.setitem(bootstrap.REGISTRY["ocr"], "fake", lambda c: FakeOcr(model="reg/fake"))
    cfg.adapters.ocr = "fake"
    assert Container(cfg, log=lambda _: None).adapter("ocr").model.repo_id == "reg/fake"
```

Entry-point plugins are tested by patching `docingest.bootstrap.entry_points`; see `test_entry_point_plugins_are_discovered` in `tests/unit/test_bootstrap.py`.

### Adding an adapter to an existing contract

Every new implementation of a port that already has a contract module must pass it. The contract fixtures are parametrized, so adding an implementation means adding one parameter.

```mermaid
flowchart LR
    OT["test_ocr_contract.py"] --> F1["FakeOcr"]
    OT --> F2["OpenAICompatibleOcr with a local stub server"]
    OT --> F3["MlxVlmOcr, model marker"]
    ST["test_store_contract.py"] --> S1["InMemoryStore"]
    ST --> S2["FilesystemStore"]
    RT["test_retrieval_contract.py"] --> R1["FakeEmbedder, FakeIndex, FakeReranker"]
```

**A new `DocumentStore`**: add a parameter to the `store` fixture in `tests/contract/test_store_contract.py`. `MyStore` below stands for your adapter:

```python
@pytest.fixture(params=["memory", "filesystem", "mystore"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryStore()
    if request.param == "mystore":
        return MyStore(tmp_path / "mystore")
    return FilesystemStore(tmp_path / "out")
```

**A new `OcrEngine`**: add a parameter to the `make_engine` fixture in `tests/contract/test_ocr_contract.py`. The fixture yields a zero-argument factory, because `test_fingerprint_is_stable` builds a second engine with identical settings and compares fingerprints.

1. If the engine talks HTTP, run it against a local stub like `stub_server()` in the same module.
2. If it loads model weights, wrap the parameter as `pytest.param("my-ocr", marks=pytest.mark.model)` so it stays opt-in.
3. If it holds large resources, give it an `unload()` method. The `engine` fixture is module-scoped and calls `unload()` in its teardown, when pytest is done with that parameter, before the next implementation's engine is built.

The contract then checks that the engine satisfies `OcrEngine` at runtime (`isinstance`), exposes a `ModelRef` with a revision, a positive integer `dpi` and a non-empty `fingerprint`, returns a well-formed `OcrResult`, accepts `L` and `RGBA` images, and keeps its fingerprint stable across calls and across instances with the same settings.

Also add an integration module in `tests/integration/` for the behaviour specific to the adapter (error mapping, retries, parsing of real files), as `test_openai_ocr.py` and `test_pdfium_reader.py` do.

### Plugin tests reusing the contracts

A plugin in this repository ([`packages/docingest-index`](../../docingest-index/README.md)) runs the retrieval contract against its real adapters instead of copying it. Its `tests/conftest.py` puts this package's `tests/` and `tests/contract/` on `sys.path`; its `test_contract.py` imports the contract tests and overrides the fixtures `make_embedder` (a factory taking `query_instruction`), `index` and `reranker`. Pytest resolves a fixture by name in the module that holds the test, so the imported tests run against the plugin's adapters. A plugin outside this repository copies `test_retrieval_contract.py` and `fakes.py` and overrides the same three fixtures.

The contract fixtures therefore have a shape other packages depend on: `make_embedder(query_instruction="")` returns an `Embedder`, `index` an empty `ChunkIndex` built for the embedder that `make_embedder()` returns, `reranker` a `Reranker`.

### Writing a contract for a port that has none yet

`OcrEngine`, `DocumentStore` and the three retrieval ports (`test_retrieval_contract.py`) have contract modules today. A new one follows the same shape: one parametrized fixture that yields every implementation, and tests that use only the port's methods. This complete example for `DocumentConverter`, placed at `tests/contract/test_converter_contract.py`, runs against the current code:

```python
"""DocumentConverter contract: every converter implementation must pass the same tests."""

import pytest
from fakes import FakeConverter

from docingest.adapters.converters.plaintext import PassthroughConverter
from docingest.domain.models import PageMethod
from docingest.ports import Conversion, DocumentConverter, Segment


@pytest.fixture(params=["fake", "passthrough"])
def converter(request) -> DocumentConverter:
    if request.param == "fake":
        return FakeConverter([Segment("Hello")], method=PageMethod.PASSTHROUGH)
    return PassthroughConverter()


def test_satisfies_the_port(converter):
    assert isinstance(converter, DocumentConverter)
    assert isinstance(converter.fingerprint, str) and converter.fingerprint


def test_convert_returns_segments(converter, tmp_path):
    src = tmp_path / "note.md"
    src.write_text("Hello")
    conv = converter.convert(src)
    assert isinstance(conv, Conversion)
    assert conv.segments and all(isinstance(s, Segment) for s in conv.segments)
    assert isinstance(conv.method, PageMethod) and conv.engine
```

`isinstance` works against the ports because every port Protocol is decorated with `@runtime_checkable`. It checks that the attributes and methods exist, not their signatures, so keep behavioural assertions in the contract as well.

### Testing a CLI command

Use `typer.testing.CliRunner` with `docingest.entrypoints.cli.app`, and pass a temporary `--config` so outputs stay in `tmp_path`:

```python
import json

from builders import LONG
from typer.testing import CliRunner

from docingest.entrypoints.cli import app


def test_ingest_a_markdown_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "note.md").write_text("# Note\n" + LONG)
    cfg_path = tmp_path / "cfg.toml"
    cfg_path.write_text(f'output_dir = "{tmp_path / "out"}"\n')

    result = CliRunner().invoke(app, ["ingest", str(raw), "--config", str(cfg_path)])

    assert result.exit_code == 0, result.output
    index = json.loads((tmp_path / "out" / "index.json").read_text())
    assert [v["source_name"] for v in index.values()] == ["note.md"]
```

For the `bench` sub-app, `test_bench_cli_run_resume_report` in `tests/integration/test_bench_datasets.py` shows how to write a temporary `benchmark.toml` and register a fake OCR adapter so `bench run` and `bench report` run without a model.

### Testing a benchmark suite or candidate

A benchmark suite is any object with `name`, `fingerprint`, `samples()`, `output_path()` and `score()` (the `BenchmarkSuite` port). Build a small in-memory suite like `MemSuite` in `tests/unit/test_benchmark.py`, assert `isinstance(suite, BenchmarkSuite)`, and drive `BenchmarkRunner` with a factory that returns a `FakeOcr` subclass per `CandidateSpec`. Suite adapters that read real data (`SyntheticSuite`, `OlmOcrBenchSuite`) are tested in `tests/integration/test_bench_datasets.py` with `tests/fixtures/bench`.

### Checklist before pushing

1. `uv run pytest` passes and every new skip has a reason you expect.
2. New code is covered by a default (not opt-in) test, or the 85% threshold may fail in CI.
3. `./scripts/check.sh` passes: lint, format, import contracts, types and tests.
