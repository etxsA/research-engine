# Run guide

This guide takes you from a clone of the repository to a cited answer from your own corpus, on a Linux machine with NVIDIA GPUs. It runs every step of the research engine: install, the three model servers, papers in, the chunk index, and `docingest ask`. Each step says what it prints and how long it took in the measurements.

The guide links to the reference for each part instead of repeating it:

| For | Read |
|---|---|
| GPU memory, disk, the vLLM install, start times, default folders | [serving/README.md](../serving/README.md) |
| Every configuration key | [packages/docingest/config/README.md](../packages/docingest/config/README.md) |
| Every command and option | [packages/docingest/README.md](../packages/docingest/README.md#cli-overview) |
| How the index works and what it stores | [packages/docingest-index/README.md](../packages/docingest-index/README.md) and [ADR 0004](../packages/docingest/docs/adr/0004-chunk-index-and-two-stage-retrieval.md) |
| Checking a quantum circuit | [Check a circuit with quantum-sim](#check-a-circuit-with-quantum-sim) below and [packages/quantum-sim/README.md](../packages/quantum-sim/README.md) |
| Checking or converting a question set, a run, a score or a review file | [Check a question set with qa-bench](#check-a-question-set-with-qa-bench) below and [packages/qa-bench/README.md](../packages/qa-bench/README.md) |
| Scoring runs against a question set and comparing two runs | [Score runs with qa-bench](#score-runs-with-qa-bench) below and [packages/qa-bench/README.md](../packages/qa-bench/README.md#the-scorer) |
| Running the web app and its API for a team on a shared server, without sudo | [Run the web app on a shared server](#run-the-web-app-on-a-shared-server) below, [deploy/research-api.sh](../deploy/research-api.sh) and [packages/research-api/README.md](../packages/research-api/README.md) |
| Exporting a paper's circuits as OpenQASM 3 | [Export a paper's circuits as OpenQASM 3](#export-a-papers-circuits-as-openqasm-3) below and [ADR 0006](../packages/docingest/docs/adr/0006-openqasm-3-export.md) |

If the three model servers already run on the machine you use, do steps 1 and 2 and skip step 3, then start at step 4; [Sharing a machine](#sharing-a-machine) shows how to check them.

## Contents

- [1. Check the machine](#1-check-the-machine)
- [2. Install the workspace](#2-install-the-workspace)
- [3. Install vLLM and start the model servers](#3-install-vllm-and-start-the-model-servers)
- [4. Write your configuration](#4-write-your-configuration)
- [5. Get papers in](#5-get-papers-in)
- [6. Build the index](#6-build-the-index)
- [7. Ask](#7-ask)
- [8. Add papers later, and stop the servers](#8-add-papers-later-and-stop-the-servers)
- [Check a circuit with quantum-sim](#check-a-circuit-with-quantum-sim)
- [Check a question set with qa-bench](#check-a-question-set-with-qa-bench)
- [Score runs with qa-bench](#score-runs-with-qa-bench)
- [Run the web app on a shared server](#run-the-web-app-on-a-shared-server)
- [Export a paper's circuits as OpenQASM 3](#export-a-papers-circuits-as-openqasm-3)
- [Measured times](#measured-times)
- [Sharing a machine](#sharing-a-machine)
- [Troubleshooting](#troubleshooting)

## 1. Check the machine

The default setup needs two GPUs with 48 GB each: one for the answering model (about 45,000 MiB in use) and one for the embedder and the reranker together (about 43,000 MiB in use). It also needs about 44 GB of disk for the model downloads, and more for the vLLM environment and caches. The details and the measurements are in [Requirements](../serving/README.md#requirements).

```bash
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv
```

Look at which GPUs are free before you start anything. If other people use the machine, read [Sharing a machine](#sharing-a-machine) first.

What needs a GPU:

| Work | GPU |
|---|---|
| Ingesting born-digital PDFs, LaTeX sources, Markdown and text | No |
| Crawling arXiv | No |
| Ingesting scanned pages and images (OCR) | Yes, in this configuration through the answering model's server |
| Building the index | Yes, the embedder's server |
| `docingest ask` with the index | Yes, all three servers |

A machine without GPUs can still ingest and crawl. `ask` needs a model that speaks the OpenAI API, running somewhere you can reach. [`config/examples/ollama-qa.toml`](../packages/docingest/config/examples/ollama-qa.toml) shows PaperQA2 answering with Ollama models; that setup has no chunk index. The other options are in the [docingest README](../packages/docingest/README.md#paperqa2-integration).

## 2. Install the workspace

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and git. uv installs Python 3.12 if the machine has none.

```bash
git clone https://github.com/etxsA/research-engine.git
cd research-engine
uv sync --locked --all-extras
```

This installs docingest, docingest-index, quantum-sim, qa-bench and the PaperQA2 extra into one environment (`.venv` at the root). It does not install vLLM. The workspace environment is about 6 GB.

Set the Hugging Face cache once, in the shell you will use for the rest of the guide, and put it on a large disk. The model servers download into it, and docingest's `ask` loads a small embedding model from it.

```bash
export HF_HOME=/big/disk/huggingface
```

## 3. Install vLLM and start the model servers

vLLM is a separate environment, not part of the workspace. Follow [Install vLLM](../serving/README.md#install-vllm): a Python 3.12 environment with headers, `ninja` and the CUDA 12.9 build of vLLM 0.30.0, and three lines in `serving/env.local` that point `serve.sh` at it. If vLLM is already installed on the machine, you can reuse it instead; the same section says how.

Two more settings go in `serving/env.local` when they apply:

- A model that is already on disk, for example in a shared models folder, can be used without downloading it: point its role at the snapshot with `LLM_MODEL_PATH`, `EMBED_MODEL_PATH` or `RERANK_MODEL_PATH`. See [Configuration](../serving/README.md#configuration).
- `serve.sh` writes its pid files, logs and compile caches under your home by default. If your home is on a small disk, move them with `SERVING_STATE_DIR` and `SERVING_CACHE_DIR`. The same section lists the defaults and sizes.

Then start the servers, one after another, from the repository root (each command returns when its server is ready):

```bash
serving/serve.sh embed start
serving/serve.sh rerank start
serving/serve.sh llm start
```

Each command prints the model, the GPU, the port and the log file, then the time until the server answered:

```text
starting qwen-embed (qwen3-embedding-4b) on GPU 1 at http://127.0.0.1:8002 (pid N), log <state dir>/qwen-embed.log
ready after 75 s
```

The first start of each role also downloads its model, which makes it longer on a slow connection. The measured start times are in [First start](../serving/README.md#first-start): with the models already downloaded (and, for most of them, an empty compile cache), 75 to 125 s for `embed`, 80 to 100 s for `rerank` and 231 to 385 s for `llm`, so 6.5 to 10 minutes for the three. Once the compile cache is filled by an earlier start, the same commands one after another took 50 s, 55 s and 160 s: all three ready after about 4 minutes (251 s). `llm` runs on the other GPU, so it can start while `embed` and `rerank` do; that took 166 s in total (put `serving/serve.sh llm start &` before the `embed` and `rerank` commands; the `ready after` figures are rounded up to a multiple of 5 s, so one can be above the total). Check them:

```bash
serving/serve.sh embed status        # running (pid N, port 8002, model qwen3-embedding-4b)
curl -sf http://127.0.0.1:8001/health && echo llm ok
curl -sf http://127.0.0.1:8002/health && echo embed ok
curl -sf http://127.0.0.1:8003/health && echo rerank ok
```

The default GPUs are 2 for `llm` and 1 for `embed` and `rerank`. On a machine with other GPU numbers, pass them: `serving/serve.sh llm start 0 8001`. If you change a port, change it in the configuration too (step 4).

## 4. Write your configuration

[`lab-server.toml`](../packages/docingest/config/examples/lab-server.toml) already points at ports 8001 to 8003 and turns the index on. It also turns on the answer settings that the measurements in this guide use: `skip_summary = true` and `answer_max_sources = 10` in `[qa]` (the answer model reads the retrieved chunks themselves, equations included; see [Equations in answers](#equations-in-answers)). `whole_blocks` stays off, because it is optional. Copy it outside the repository and set two paths:

```bash
cp packages/docingest/config/examples/lab-server.toml "$HOME/lab.toml"
```

```toml
output_dir = "/path/to/work/normalized"   # the corpus: one folder per document

[index]
dir = "/path/to/work/index"               # the chunk indexes
```

Use absolute paths so the file works from any folder. The index folder is named after the corpus path, the embedder and the chunk settings, so set both paths once and keep them: moving the corpus starts a new, empty index. `raw_dir` (where `crawl` downloads) can stay `data/raw`, relative to the folder you run docingest from, which is git-ignored.

The rest of the guide runs from `packages/docingest` and passes the file with `-c`:

```bash
cd packages/docingest
CONFIG="$HOME/lab.toml"
```

Pass `-c "$CONFIG"` to every command. Without it, docingest uses `config/pipeline.toml`, which is the Mac setup: another corpus folder (`data/normalized` under the current folder), no index, an answering model expected on port 8080 and MLX OCR. Nothing in this guide works with it. What you see when you forget it is listed under [Troubleshooting](#troubleshooting).

## 5. Get papers in

Either crawl arXiv or ingest your own files. Both write `document.md` and `manifest.json` per paper into `output_dir`.

```bash
uv run docingest crawl 'cat:quant-ph AND ti:transmon' --limit 20 -c "$CONFIG"
uv run docingest ingest /path/to/papers -c "$CONFIG"      # PDF, scans, LaTeX, Office, Markdown; folders are walked
```

`crawl` downloads the LaTeX source of each paper (PDF if there is none) into `<raw_dir>/arxiv`, waits at least 3 seconds between requests as the arXiv terms require, and ingests what it fetched. LaTeX and born-digital PDFs need no GPU. The query syntax is in the [docingest README](../packages/docingest/README.md#arxiv-crawl). arXiv asks crawlers to identify themselves: add a `[arxiv]` table with `contact = "you@example.org"` to your configuration and the address goes into the User-Agent ([key reference](../packages/docingest/config/README.md#arxiv)). This matters most on a shared machine, where many people crawl from one address.

**Old scans take longer.** A PDF that is a scan with an invisible text layer from an old OCR program (typical for papers digitised decades ago; the page shows one image the size of the page) is read by the OCR model, not from that layer, because the model reads it with far fewer errors. That takes about 29 seconds per page on the lab GPU server (up to about 70 seconds for a dense page) where the layer took milliseconds, so a folder of 600 such pages needs about 4 to 5 hours of GPU, once: the result is cached. The first run after upgrading to a version with this rule re-processes every PDF in an existing output folder, not only these pages, because the routing keys and the PDF reader's version are part of the key of a PDF (the born-digital ones take seconds; the pages that were scanned already go to the model again). The log says `OCR (scanned page with a text layer: ...)` for these pages. To keep the old layers and skip the wait, set `scan_layer_to_ocr = false` in `[routing]` ([reference](../packages/docingest/config/README.md#routing)). Born-digital PDFs are not affected, with one exception: a page drawn over a full-page background image (a slide, a poster, a cover) with a long text is read by the model too; use `scan_layer_to_ocr = false` for such a collection.

**Math pages can go to the model too (optional, off by default).** A page with a text layer full of formulas loses their structure in that layer (fractions, scripts and sums come out as loose characters); the model keeps most of it. Set `math_to_ocr = true` in the `[routing]` table of your configuration and a page with at least `math_min_equals` (default 5) equals signs or at least `math_min_density` (default 0.04) math-symbol characters is read by the model. On 123 reference pages this lowered the math error rate from 0.122 to 0.072. It costs about 31 seconds of GPU per routed page at the default thresholds: about 12.4 hours for the reference corpus (1,446 pages), about 19 hours per 10,000 arXiv papers (only the PDF-only papers reach this path; LaTeX papers do not). It also has a risk the text layer does not: in 3 of the 17 pages the defaults route (about 1 in 6) the model changed a symbol or an index in a way that still reads as math, so check the routed pages if the formulas must be quoted. A collection in Greek should leave the rule off, because Greek letters count as math symbols. Turning the rule on re-processes every PDF in the output folder once, because the keys are part of the cache key; while it is off nothing changes and cached results stay valid. Measurements: [config reference](../packages/docingest/config/README.md#routing).

What the commands print:

- `crawl` starts with the number of records (`20 record(s) for 'cat:quant-ph AND ti:transmon'`) and then logs each record it fetched and ingested.
- `ingest` prints a line per file and, when it succeeds, where the result is: `ok -> <output_dir>/<first 16 characters of the doc id>/document.md`. A file that was ingested before with the same settings also prints a `[cache] <file> -> <folder> (config <hash>)` line before its `ok ->` line, and is not processed again.
- Both end with an `Ingestion summary` table (document, kind, pages, page methods, seconds, doc id). `crawl` adds `fetched N, ingested N`.
- A file that fails is listed in a `Failed inputs` table and the command exits with status 1 after it has processed the others.

## 6. Build the index

```bash
uv run docingest index build -c "$CONFIG"
```

This chunks every paper, sends the chunks to the embedder and commits the index. It prints one line. For 100 papers (9,404 chunks):

```text
added 100, updated 0, unchanged 0, pruned 0; embedded 9404 chunks in 200.3s
```

The time is the build's own; with Python start-up the command took 201.1 s. Run it again and nothing is embedded:

```text
added 0, updated 0, unchanged 100, pruned 0; embedded 0 chunks in 0.2s; nothing to commit
```

That took 0.90 s with start-up. A smaller example: 5 papers, 388 chunks, `embedded 388 chunks in 8.3s`.

Run one build at a time. `build` takes the lock that keeps writers apart when it starts, before it embeds anything, so a second build started at the same time stops at once with `error: another process is writing to the index <folder>; wait for it to finish`. See [Sharing a machine](#sharing-a-machine).

`index status` shows what is indexed, what is searchable and what a build would do:

```bash
uv run docingest index status -c "$CONFIG"
```

Look at the rows `indexed`, `searchable`, `corpus` and `to add`. After a complete build, `indexed` and `searchable` equal the corpus and `to add` is 0.

## 7. Ask

```bash
uv run docingest ask "What limits T1 in transmons?" -c "$CONFIG"
```

`ask` checks the index first, embeds the question, takes the 50 best chunks (dense vectors plus BM25 for English questions and for questions with LaTeX), reranks them and gives the best 10 to PaperQA2, which writes the answer. With `skip_summary = true` and `answer_max_sources = 10` in `[qa]`, as the example configuration has them, the answer model reads the 10 chunks themselves, equations included. Without them (the default) PaperQA2 first summarizes each chunk in about 100 words, and the summaries lose most LaTeX (see [Equations in answers](#equations-in-answers)). The output is the answer with its sources cited inline as `(<doc id prefix> pages N-M)`, followed by a reference list with one entry per cited page range, so a paper cited for two ranges appears twice, and the same paper often appears two to four times in one answer. To read a cited page, see [Open a cited source](#open-a-cited-source). `warning:` lines come first if the index is behind the corpus. `ask` prints no timings; measure it with `time`.

Before the answer you may also see lines that are harmless:

- a `Loading weights` progress bar: the small CPU embedding model (`sentence-transformers/all-MiniLM-L6-v2`) of PaperQA2. Only `ask` without an index (or with `--no-index`) loads it; with the index, PaperQA2 gets the chunks as given and embeds nothing. It is fetched into `HF_HOME` the first time; `uv run docingest model-path embedding` fetches it ahead of time.
- `Failed to calculate cost for qwen-local` lines: litellm has no price for a local model. There is about one per summary request, so about a dozen per question with `skip_summary = false`, and one line with `skip_summary = true`.
- on a driver that supports only CUDA 12 (such as 535), a torch `CUDA initialization: The NVIDIA driver on your system is too old` warning. The workspace's torch is a CUDA 13 build, and `ask` runs it on the CPU only. It is not related to the vLLM install.

None of them affects the answer. Two options change one question: `--no-index` answers with PaperQA2's own retrieval, and `--bm25 english|always|never` overrides the keyword setting. See [Spanish questions](#spanish-questions) for when to use the second.

### Many questions in one process

Every `docingest ask` starts Python, opens the index, copies its vectors to 32-bit floats and lists the corpus before it retrieves anything. For a script that asks many questions, `ask --stdin` does that once. It reads one JSON object per line and writes one JSON line per answer, in order:

```bash
printf '%s\n' \
  '{"id": "1", "question": "What limits T1 in transmons?"}' \
  '{"id": "2", "question": "coherencia transmon", "bm25": "never", "return_contexts": true}' \
  | uv run docingest ask --stdin -c "$CONFIG" > answers.jsonl
```

The same options as on the command line go in the line (`use_index`, `bm25`), plus `whole_blocks` (true or false, instead of the `[qa]` key, for this question) and `return_contexts` (the chunks the answer model read). Each answer line holds `answer`, `warnings`, `sources`, `timings`, the `index_generation` and an `error` that is null unless that question failed; a failed question does not stop the next one. Questions run one at a time, so the model server sees what it sees from the plain command. The index is reloaded by itself when a build commits; a change to the configuration file needs a new process. In Python, `Container(cfg).ask_session` gives the same session as an object. Nothing is stored. The fields are listed in the [command reference](../packages/docingest/src/docingest/entrypoints/README.md#docingest-ask).

### Equations in answers

By default PaperQA2 does not give the answer model the 10 chunks, it gives it a summary of about 100 words per chunk, and the summaries drop most LaTeX. For papers where the equations matter, set these keys in the `[qa]` table of your configuration. `lab-server.toml` already sets the first two:

```toml
[qa]
skip_summary = true        # the answer model reads the chunks, equations included
answer_max_sources = 10    # how many of the [index] contexts it reads; the default is 3
whole_blocks = false       # true: also complete the equation, circuit or table that a chunk cuts (see below)
```

With `skip_summary` the 10 summary requests of every question disappear and the answer prompt holds the 10 chunks as they are in the index, in the order the reranker gave them, cited as before. Because the model now reads raw circuits and equations, the answer prompt also tells it to take every circuit, gate, qubit index and equation only from the context, copied exactly with its LaTeX, to keep each one with the paper it comes from (no gate or term moved from one paper's circuit or equation into another's, and an equation number belongs to the paper it is printed in), and to say when the context does not show a gate or a term instead of guessing. The rule is fixed and needs no setting. The prompt is then about 4,000 tokens (6,100 for the 10 largest chunks of a 200-paper sample), against the 16,384 of the default `LLM_MAX_LEN` and a reply of at most 1,024. Chunks of 900 characters are far from the limit; with `chunk_chars` of about 4,000 or more, or a smaller `LLM_MAX_LEN`, the server answers HTTP 400 instead, and `ask` then asks again with half the sources (10, then 5, 2 and 1), printing `warning: answer prompt too long for the model; retried with 5 sources` at each step; if even one source does not fit, the server's error is shown. The details are in [config/README.md](../packages/docingest/config/README.md#reading-the-chunks-instead-of-summaries). Keeping `answer_max_sources = 3` is also valid and gives the 3 best chunks.

With `skip_summary` the answer prompt also holds a plain gate list right after each circuit code block of the chunks (wires numbered top to bottom, one step per drawn column: `Step 2: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)`), because a model can read two control dots joined by a wire as a CNOT. The list is made from the block when the question is asked, so there is nothing to rebuild, no setting to turn on and the circuit code itself is unchanged; a part it cannot read is written as `unparsed`, never guessed. Circuits written inside display math get no list. The format and its limits are in [config/README.md](../packages/docingest/config/README.md#gate-lists-after-circuit-blocks).

A chunk can stop or start inside a display equation, a circuit code block or a table. To complete it when the question is asked, add `whole_blocks = true` next to `skip_summary` (it is off by default and has no effect without `skip_summary`): the rest of the block, and the caption after it, is read from the paper and added to the chunk, without repeating text that another chunk holds and up to three chunks' length (a longer block is shown as it is, with a note that it is only part). The index and the chunks it holds do not change and nothing has to be rebuilt. On the 101-question math and circuit set, 72 questions got a longer prompt; checked by hand, 3 of those answers were better, 2 were worse and 67 were the same, and the prompt grew by about 160 tokens per question on average. The details are in [config/README.md](../packages/docingest/config/README.md#blocks-that-a-chunk-cuts).

When the question names an equation number (`Eq. (2)`, `Equation 7`, `ecuación (4)`), `ask` also looks the number up in the papers it has already retrieved and adds the chunk that holds that `\tag{n}`, under a header such as `Equation (2) of paper <doc id prefix>, <title>:`, for the first two papers per number whose title shares at least four content words with the question (a question that does not name the paper in its words gets no added chunk) and at most four chunks, taking the places of the lowest-ranked chunks so the prompt keeps its size. It is made when the question is asked, with no setting and no rebuild. The details are in [config/README.md](../packages/docingest/config/README.md#equation-numbers-in-the-question).

The key has no effect with `--no-index`: `ask` prints `warning: [qa] skip_summary applies to chunks from the index ...` and summarizes as before (with `lab-server.toml`, that is the warning you see for `ask --no-index`). It changes only the answer, so the corpus and the index stay valid.

### Open a cited source

The id in a citation, `(<doc id prefix> pages N-M)`, is the first 16 characters of the document's id: the name of its folder under `output_dir`, the same one `ingest` prints after `ok ->`. The pages are the page markers of its `document.md`. For a LaTeX paper the markers number its sections, not printed pages. With a made-up id:

```bash
ID=0123456789abcdef                                  # the 16 characters from the citation
OUT=/path/to/work/normalized                         # your output_dir
grep -m1 '^# ' "$OUT/$ID/document.md"                # the title
sed -n '/^<!-- page 4 /,/^<!-- page 6 /p' "$OUT/$ID/document.md"    # pages 4 and 5; the line of page 6 ends the range
```

`$OUT/$ID/manifest.json` records where the paper came from (`source_name`, `title`, `metadata`) and, for each page, how its text was made (`method`). The reference list under the answer gives the same paper's title and authors.

## 8. Add papers later, and stop the servers

After the first build, `--index` keeps the index in step as papers arrive, with no separate build:

```bash
uv run docingest crawl 'cat:quant-ph AND ti:qubit' --limit 20 --index -c "$CONFIG"
uv run docingest ingest /path/to/more-papers --index -c "$CONFIG"
```

Each prints a line like `index: added 1, updated 0, unchanged 1; embedded 30 chunks in 0.9s` after the `Ingestion summary` table (for `crawl`, before `fetched N, ingested N`). If the index update fails, the papers stay stored and the message says to run `index build`.

Stop the servers you started when you are done. Go back to the repository root first (`cd ../..`). Each command stops only that server:

```bash
serving/serve.sh llm stop
serving/serve.sh embed stop
serving/serve.sh rerank stop
```

## Check a circuit with quantum-sim

quantum-sim simulates a quantum circuit with NumPy and says what state it prepares, what it measures, and whether two circuits are equal up to global phase. It runs on the CPU, needs no model server and no GPU, and step 2 already installed it. It reads the `circuit/1` format, which is the format of the gate lists that docingest makes from a paper's LaTeX circuits (`CircuitSteps.to_dict()`), and prints one JSON document. Run the commands from the repository root.

A circuit/1 file is an object whose `parts` is a list of parts (more than one when the drawing has an equals sign), each part a list of steps and each step a list of items, with wires numbered from 1 at the top. Write a Bell pair and run it:

```bash
cat > bell.json <<'EOF'
{"format": "circuit/1", "wires": 2,
 "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
            [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]}
EOF
uv run quantum-sim run bell.json
```

The result has `"status": "ok"`, the `conventions` it used, and for each part of the circuit the `state` (amplitudes as `[re, im]`) and the `probabilities`: `"values": [0.5, 0.0, 0.0, 0.5]` for `|00>`, `|01>`, `|10>` and `|11>`. `--input 10` starts from another basis state, `--unitary` adds the matrix, and on a circuit that has a symbol (a gate labelled `R_y(\theta)`, say) `--set 'theta=\pi/2'` gives it a value; a name the circuit does not have is refused.

`check` takes a request that holds the circuit and what to compare it with: another circuit (`{"a": ..., "b": ...}`), a state or a unitary. Compare the same Bell circuit with the state it should prepare:

```bash
cat > bell-check.json <<'EOF'
{"circuit": {"format": "circuit/1", "wires": 2,
             "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
                        [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]},
 "target": {"state": [0.7071067811865476, 0, 0, 0.7071067811865476]}}
EOF
uv run quantum-sim check bell-check.json
```

It prints `"equal_to_target": true` and one comparison with `"equal": true`. Both commands exit with 0 when they did the work (a check that finds the circuits different is still 0, with `"equal_to_target": false`), with 1 when the input was refused (the result says why) and with 2 when the command line does not parse.

To read a circuit from a paper, ask docingest for its gate list as data. The circuits of an ingested paper are the fenced `latex` blocks of its `document.md` (see [Open a cited source](#open-a-cited-source) for where it is), and `gate_list` reads a block's source. This writes the identity H Z H = X, drawn as one quantikz circuit with an equality sign, and checks it, because `check` compares the part left of a drawn `=` with the part right of it:

```bash
uv run python - <<'EOF' > hzh.json
import json
from docingest.domain.circuits import gate_list

source = r"""\begin{quantikz}
& \gate{H} & \gate{Z} & \gate{H} & \qw & \push{=} & \gate{X} & \qw
\end{quantikz}"""
print(json.dumps(gate_list(source).circuits[0].to_dict()))
EOF
uv run quantum-sim check hzh.json
```

The result has `"equal_to_target": true` and a comparison between `part 1` and `part 2` with `"method": "unitary"` and a `max_deviation` of about 2e-16. For a circuit of a paper, replace `source` with `circuit_code_blocks(text)[i].source`, where `text` is the `document.md` and `circuit_code_blocks` comes from the same module, and run `run` or `check` on the dictionary. Check that `gate_list(source).status` is `parsed` first. Both commands also read standard input, so the Python step can be piped straight into `uv run quantum-sim check`.

The conventions are Nielsen and Chuang: wire 1 is the top wire and the most significant bit of a basis label (the opposite of Qiskit), wires are numbered from 1, and rotations are R_a(theta) = exp(-i theta sigma_a / 2), and results are compared up to global phase at 1e-9. Every result prints them, and [packages/quantum-sim/docs/conventions.md](../packages/quantum-sim/docs/conventions.md) lists the gate table and every accepted label spelling.

Limits:

- Statevectors up to 20 wires, unitaries up to 10 wires (two circuits of 11 to 20 wires are compared on 8 random states), at most 10,000 operations, 50,000 items in a document and 32 Mi characters of input.
- Version 1 refuses what it cannot simulate: a measurement before the end of its wire, a classically controlled gate, a reset or a postselection, an angle or a gate label outside its closed grammar, and a wire legend or text that might hide one of those.
- It never skips a part to make a circuit simulable, and its own refusal is the last word: `status` `parsed` in docingest's gate list is necessary, not sufficient. A circuit that docingest read as `partial` or `unparsed` (a command or wrapper the reader does not know, a `\cw` that no measurement made classical) is refused. So is a `parsed` one whose wire legend shows a bundle, a register, a row of dots or a classical wire, and one with a ket drawn after a gate. The result is `"status": "refused"`, exit status 1, and the reasons.
- docingest does not mark a `yquant` `cbit` register as classical, so quantum-sim takes it for a qubit: compare the wire count in the result with the drawing.

The full list is in [packages/quantum-sim/README.md](../packages/quantum-sim/README.md#limits) and [the gate lists as data](../packages/docingest/src/docingest/domain/README.md#the-steps-as-data).

## Check a question set with qa-bench

qa-bench reads, checks and converts the files of the question-answer benchmark: `qset/1` (a question set), `qa-run/1` (the answers of one run), `qa-score/1` (what a scorer computed) and `reviews/1` (decisions about the items). It runs on the CPU, needs no model server and no GPU, and step 2 already installed it. Run the commands from the repository root. Each file is JSON lines whose first line names its format.

Write a set with one question and check it:

```bash
cat > my-set.qset.jsonl <<'EOF'
{"format": "qset/1", "set": "my-set/v1", "created": "2026-01-03T09:00:00+00:00"}
{"id": "Q-001", "lang": "en", "lang_origin": "native", "question": "What does the toy paper call its coupling?", "answer": "J", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "The coupling is called J."}]}]}
EOF
uv run qa-bench validate my-set.qset.jsonl
```

It prints `ok my-set.qset.jsonl: qset/1, 1 records, md5 ...` and exits with 0. The md5 is the identity of the set: a run or a score file names the set by it, so it changes when a single character of the file changes. A file that breaks a rule is printed as `file:line: pointer: message`, one line per problem (the first 20 per file, all with `--all`), and the exit status is 1. A run, a score or a review file is checked against its set with `--set my-set.qset.jsonl`: the set name and md5 must match and every answer must be for an item of the set.

To bring an older question-set file into the format, name its shape. The shapes are `mathcirc-v1`, `circuit-tasks-v1`, `circuit-claims-v1`, `qubitqa-pilot` and `qasper`; the package README says what each one reads. This turns a tab-separated list of circuit claims into a set:

```bash
printf 'paper_id\tblock_index\tclaim_quote_short\ttarget_kind\ttarget\tinput_state\tnotes\nd4d4d4d4d4d4d4d4\t1\tH H = I\tidentity\tI (exact)\t-\tcheck\n' > claims.tsv
uv run qa-bench convert circuit-claims-v1 claims.tsv -o claims.qset.jsonl --set-name claims/v1 --corpus my-corpus
uv run qa-bench validate claims.qset.jsonl
```

The converter records the source file's name and md5 in the header of the new set. `--created 2026-01-03T09:00:00+00:00` pins the time written in the header (the default is now, in UTC), so the same input gives the same file and the same md5. `convert` does not overwrite a file that exists unless you add `--force`. `--back` writes a set made this way as a source file again; the rows are the same, but the key order and spacing of the original are not kept.

Limits:

- `validate` and `convert` check and convert files; `score` (next section) scores runs that already exist. `qa-bench` has no command to start a run yet.
- A file is read strictly. A key given twice on a line, `NaN`, `Infinity`, a number out of range, an unpaired surrogate escape (`\ud800`) and nesting deeper than 64 levels are refused.
- A shape that is not in the list is refused with the list of shapes; the command does not guess one.

## Score runs with qa-bench

`qa-bench score` reads a question set and the runs made on it and writes a `qa-score/1` file: the metrics of each answer, the mean and 95% interval of each group of questions, and, for each pair of runs you name, the paired bootstrap difference (second run minus first), its interval and how many questions went up and down. It never calls a model, needs no GPU and gives the same bytes for the same inputs.

A run points at its set by md5, so first write a set with key facts (the strings, numbers or expressions a right answer must contain) and ask `validate` for its md5:

```bash
cat > score-set.qset.jsonl <<'EOF'
{"format": "qset/1", "set": "score-demo/v1", "created": "2026-01-03T09:00:00+00:00"}
{"id": "Q-001", "lang": "en", "question": "What does the toy paper call its coupling?", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "The coupling is called J."}]}], "key_facts": [{"type": "text", "value": "J"}]}
{"id": "Q-002", "lang": "en", "question": "How many toy modes does the paper couple?", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "It couples 2 modes."}]}], "key_facts": [{"type": "num", "value": 2, "rel": 0.02}]}
EOF
MD5=$(uv run qa-bench validate score-set.qset.jsonl | sed 's/.*md5 //')
```

Write two runs of it by hand (a real run file comes from a runner; the package README describes the format). The first run writes the number as a word, the second as a digit:

```bash
cat > run-a.qa-run.jsonl <<EOF
{"format": "qa-run/1", "run_id": "run-a", "created": "2026-01-04T09:00:00+00:00", "set": {"name": "score-demo/v1", "md5": "$MD5"}, "arm": {"name": "harness"}, "model": {"id": "toy-model"}, "code": {"runner": "by hand"}, "corpus": {"id": "my-corpus"}}
{"id": "Q-001", "repeat": 1, "answer": "The coupling is J.", "contexts": []}
{"id": "Q-002", "repeat": 1, "answer": "It couples two modes.", "contexts": []}
EOF
cat > run-b.qa-run.jsonl <<EOF
{"format": "qa-run/1", "run_id": "run-b", "created": "2026-01-04T09:00:00+00:00", "set": {"name": "score-demo/v1", "md5": "$MD5"}, "arm": {"name": "harness"}, "model": {"id": "toy-model"}, "code": {"runner": "by hand"}, "corpus": {"id": "my-corpus"}}
{"id": "Q-001", "repeat": 1, "answer": "The coupling is J.", "contexts": []}
{"id": "Q-002", "repeat": 1, "answer": "It couples 2 modes.", "contexts": []}
EOF
uv run qa-bench score --set score-set.qset.jsonl --run run-a.qa-run.jsonl --run run-b.qa-run.jsonl \
  --pair run-a:run-b -o demo.qa-score.jsonl
uv run qa-bench validate demo.qa-score.jsonl --set score-set.qset.jsonl
```

`score` prints `wrote demo.qa-score.jsonl: qa-score/1, 43 records, md5 ...` and exits with 0. The file has one JSON object per line: the header (scorer, set md5, the runs, bootstrap settings and notes), an `item` record per answer, a `group` record per run, group and metric, and a `compare` record per pair, group and metric. Look at the comparison of the two runs:

```bash
grep '"compare"' demo.qa-score.jsonl | grep '"group": "all"' | grep '"metric": "all_keys"'
```

It shows `"base": 0.5, "var": 1.0, "diff": 0.5, ..., "up": 1, "down": 0`: the first run missed the number because it wrote "two", and a number word does not count (see the limits). With `--number-words` both runs score 1.0. The metrics ending in `_words` (`all_keys_words`, `key_share_words`, ...) always give the result with number words counted, so one table shows both.

What the options do:

- `--pair BASE:VAR` (repeat for more pairs) names runs by their `run_id`. `diff` is VAR minus BASE, `ci` its 95% bootstrap interval over questions, `up` and `down` the questions that rose and fell. `--noise A:B` names two runs of the same arm; their difference becomes the `floor` of each comparison, and `counts` is true only when the interval excludes 0 and the difference is larger than the floor.
- Groups come from the set: `all`, `lang=en`, `form=...` and every tag with 2 to 8 values (`category=a`). `--group-by lang,tag:category` picks the fields, `--group category=a|b` adds the union of two values. `--no-group-ci` skips the interval of each group, which saves time on a large set.
- `--bootstrap python_random` (the default) or `numpy_pcg64` picks the random generator of the bootstrap. The two give different last digits, and the header records which one was used. Both are written in the standard library, so numpy is not needed.
- `--created TIME` pins the time in the header (the default is now, in UTC). An output file that exists is not overwritten unless you add `--force`.

Limits:

- The questions are always taken in the order of the set file, never the order of a run file, because the bootstrap resamples positions. A run written in another order gives the same numbers.
- Number words (two, ocho) do not count for a number key unless you ask: they count wherever they stand, and a hand check found about half of them wrong. A lenient hit (an expression key matched by symbol recall only) in a category `f` question does not count, so for `f` the lenient columns equal the exact ones, and the key record says `unchecked`. The digits of a citation key (`(<id> pages 3-4)`) are not read as numbers, and a text key's alternative that is only a Greek letter name is ignored. [The scorer](../packages/qa-bench/README.md#the-scorer) lists the rules.
- Not scored: reviews, multiple choice, circuit tasks and the multi-paper metrics. A run made for another set, an answer to a question the set does not have, or a pair that names an unknown run is refused with exit status 1.

## Run the web app on a shared server

This section puts the web app and its API on a Linux server that several people share, so a team opens one address in the browser and signs in with a personal account. It needs no sudo and no GPU. One script, [`deploy/research-api.sh`](../deploy/research-api.sh), starts and stops the service, installs a build of the web app, takes backups and creates accounts. The API serves the built web app itself, from the same address, so there is one process and one port (8090 by default) and no second web server. How the API does that is in [The web app](../packages/research-api/README.md#the-web-app).

Everything below uses placeholders: `<server>` for the machine, `<user>` for your account on it, `<tailscale-address>` for the server's Tailscale address and `/path/to/research-api` for the data folder. Replace them with your own values.

### What you need

- A Linux server with a clone of this repository and the workspace installed ([step 2](#2-install-the-workspace): `uv sync --locked --all-extras`, which creates `.venv` for the script). The server needs no Node.
- [Tailscale](https://tailscale.com/) on the server and on every laptop that will open the app, all in the same tailnet, or an SSH account on the server for a tunnel (see [Open it from a laptop](#open-it-from-a-laptop)).
- `bash`, `curl` and `python3` on the server, as any Linux server has. Not even `setsid` is required (the script falls back to `perl`, which is how it runs on macOS).
- On your own machine, Node 22.22 or later and pnpm 12 to build the web app ([apps/web/README.md](../apps/web/README.md#run-it)), and `rsync` or `scp` to copy the build.
- For the demo without GPUs you need nothing else: [fake mode](#fake-mode-a-demo-without-gpus).

The data folder is outside the repository. The script uses `$RESEARCH_API_HOME`, else `~/research-api`. If the server keeps its big disk somewhere else, say so once in `deploy/env.local`, which git ignores. Because cron and a login shell both run the script from the clone, they then agree on the folder. The file is shell code that the script runs, so it must be yours and closed to others (`start` refuses an `env.local` that other accounts can read, and no command runs one they can change):

```bash
# deploy/env.local (not committed)
RESEARCH_API_HOME=/path/to/research-api
```

```bash
chmod 600 deploy/env.local
```

A variable set in the environment wins over `env.local`, so `RESEARCH_API_HOME=/path/to/scratch deploy/research-api.sh status` looks at the scratch folder, never at the real one.

What the script keeps there:

| Path | Holds | Mode |
|---|---|---|
| `config.toml` | the settings | 0600 |
| `db/`, `data/`, `logs/` | the SQLite database, data files, `api.log` (made by the service) and `process.log` (what the process prints) | 0700 |
| `run/` | pid file, start time, mode | 0700 |
| `backups/` | database copies | 0700 |
| `web/<id>/`, `web/current` | installed builds of the web app and the link to the one served | 0700 |
| `incoming/` | where a build is copied before it is installed (public files only) | 0700 |

The disk of a shared server is open to everyone, and so is the folder above it. So `start` refuses to run, and says which `chmod` to run, when the data folder, the settings file or anything private inside it can be read or changed by another account. The service itself makes the same check on its folders, database and settings. `incoming/` and `web/` hold only the public build and are not part of that check.

### First start

1. **Write the settings.** Copy the example, make it private and edit the lines marked CHANGE. If the data folder is not `~/research-api`, first put its path in `deploy/env.local` (`RESEARCH_API_HOME=/path/to/data`, then `chmod 600 deploy/env.local`) and use that path below:

   ```bash
   mkdir -m 700 -p /path/to/research-api
   cp deploy/config.example.toml /path/to/research-api/config.toml
   chmod 600 /path/to/research-api/config.toml
   tailscale ip -4                       # the address for bind_host
   ```

   `bind_host` is the server's Tailscale address, `public_origin` is the one URL the team types (`http://<tailscale-address>:8090`), and `db_path`, `data_dir` and `log_dir` are inside the data folder. Set `docingest_config` to the docingest settings file you use (the one that names the papers, the index and the model servers); without it the service starts, but every question gets a 503 and no paper can be read. The service refuses to listen on `0.0.0.0`, a LAN address or a host name, and the page works only under `public_origin`, so give everyone that one URL. Check that the port is free (`ss -ltn | grep 8090` prints nothing); the script also refuses to start on a port that is taken. Every key is in the [research-api README](../packages/research-api/README.md#run-it).

   **Models for benchmark runs.** A run request names a model from the `[[models]]` entries of this file. The `harness` and `oracle` arms answer through docingest, so they use docingest's own model (the `DOCINGEST_LLM` of the service's environment, else the `[qa] llm` of the docingest file, else the pinned `[ocr]` model) whatever the request says, and the service refuses a request that names another one: a `422` that names the right model. List docingest's model under the name its server serves it as, which is the last `/` part of `[qa] llm` (`qwen-local` for `llm = "openai/qwen-local"`, as in `lab-server.toml`); the closed-book `bare` arm takes any listed model. The run file records docingest's model and revision, and a run that was stopped and resumed after docingest was set up with another model ends `failed` instead of mixing two models in one file. `deploy/config.example.toml` has the entry, commented, for you to fill in. [The arms](../packages/research-api/README.md#the-arms) has the exact comparison.

   **The oracle arm.** It answers each question from the gold passage texts (`md_text`) of the set, with no retrieval, and the service refuses it (`422`) on a set where a gold passage has none; the math and circuit set is the one that has them all. A question with no gold paper is allowed: it is asked with no passage, and PaperQA2 answers `I cannot answer this question due to having no papers.` without calling the model. The answer model reads those texts as they are only with `skip_summary = true` in `[qa]` (the lab file has it), and `answer_max_sources` must be at least the number of passages of a question or docingest drops the rest; a cut is recorded as `usage.truncated` in the run. A passage docingest cannot find in its paper is still answered, cited with all the paper's pages and marked in `ext.warnings`. [The arms](../packages/research-api/README.md#the-arms) has the details.

2. **Build the web app on your own machine and copy it.** The server never needs Node:

   ```bash
   cd apps/web
   pnpm install --frozen-lockfile && pnpm build           # writes dist/
   printf '{"commit": "%s"}\n' "$(git rev-parse --short HEAD)" > dist/version.json   # names the build
   rsync -a --delete dist/ <user>@<server>:/path/to/research-api/incoming/
   ```

3. **Start the service and install the build** (on the server, in the clone):

   ```bash
   deploy/research-api.sh start
   deploy/research-api.sh install-web
   ```

   `start` brings the database up to date (`research-api db upgrade`), starts the service in the background in its own process group, waits until `/api/v1/health` answers and prints the address. Until a build is installed, the address answers `503: The web app is not installed on this server`; `install-web` copies `incoming/` to `web/<id>/` (`<id>` is the commit in `version.json`, else `build-` and the UTC time; `--id NAME` sets it) with private permissions, then switches the `web/current` link in one step. The service serves the new build at once, with no restart and without ending anyone's session. It keeps the five newest builds, so going back is pointing `web/current` at an older folder (`ln -sfn <older id> web/current` in the data folder).

4. **Create the admin account yourself, in your own terminal.** The person who runs the server is the only admin:

   ```bash
   deploy/research-api.sh users add <username> --name "Your Name" --role admin
   ```

   It prints a random one-time password of 20 characters on standard output and says so on standard error. Open the address in a browser, sign in with it, and the app makes you choose your own password (at least 12 characters, not a common one) before anything else works. You type that password in the browser; it is never printed or logged. Do not paste a password into a chat, a ticket or a commit. Then add each teammate the same way (`--role member`) and give each one their one-time password privately; they change it at their first sign-in:

   ```bash
   deploy/research-api.sh users add <username> --name "Their Name"
   deploy/research-api.sh users list
   deploy/research-api.sh users reset <username>          # a lost password: a new one-time password, sessions end
   deploy/research-api.sh users disable <username>        # takes effect at their next request
   ```

   There is no sign-up page and no HTTP endpoint that makes accounts. The other `users` actions (`enable`, `role`) are in the [research-api README](../packages/research-api/README.md#accounts).

5. **Check it:**

   ```bash
   deploy/research-api.sh status      # pid, mode, uptime, the installed build, and what /api/v1/health answers
   deploy/research-api.sh logs -n 20  # api.log: one JSON line per request and per sign-in
   ```

   Sign-in attempts are rate limited (five wrong passwords per username in 15 minutes by default), and the limiter uses the address of the connection. The script starts the service with `--no-proxy-headers`, so an `X-Forwarded-For` header sent by any account on the server cannot choose that address, and with `--no-access-log`, because a raw access log would hold paths and query strings. A test starts the real service through the script and checks both.

### Open it from a laptop

**Through Tailscale (the normal way).** With Tailscale running on the laptop, open exactly the `public_origin`, for example `http://<tailscale-address>:8090/`. The browser says "not secure" because the page is plain HTTP; inside a tailnet the traffic is already encrypted end to end by WireGuard, which is why release 1 does without HTTPS. Anyone in the tailnet can reach the port and try to sign in, so ask whoever manages the tailnet for an access rule that opens port 8090 only to the team's devices. If the team prefers the server's MagicDNS name, put that name in `public_origin` and use it everywhere: the sign-in is refused under any other spelling of the address, the IP address included.

**Through an SSH tunnel.** Without Tailscale on the laptop, forward the port and open `localhost`:

```bash
ssh -N -L 8090:127.0.0.1:8090 <user>@<server>
# then open http://localhost:8090/
```

This suits fake mode (below), which listens on loopback and accepts any loopback address. A tunnel to the live service (`-L 8090:<tailscale-address>:8090`) works only if `public_origin` is `http://localhost:8090`, which also stops the Tailscale URL from signing in, so use it for one person or a trial, not for the team.

### Fake mode: a demo without GPUs

```bash
deploy/research-api.sh start --fake
```

Fake mode needs no settings file, no database to keep and no model server. It starts from stand-ins, listens on 127.0.0.1 only (`--port N` picks another port) and makes four test accounts in a database that is deleted when the service stops: `ana` (admin), `beto`, `dani` and `temp` (the last one must change its password at first sign-in). Their made-up passwords are public, in [`packages/research-api/tests/fixtures/seed-users.json`](../packages/research-api/tests/fixtures/seed-users.json). So reach it through the SSH tunnel above, and keep anything private out of it: every account on a shared server can reach loopback. The sign-in and the circuit check run their real code in fake mode; anything that would need a model server or private data is a stand-in as those parts arrive. The page is served the same way as in live mode, so run `install-web` first. To go back to the real service, `stop` and `start` without `--fake`.

### Day to day

| Task | Command |
|---|---|
| Is it up? | `deploy/research-api.sh status` (exit 0 when it answers, 1 when it is not running, 3 when it runs but does not answer) |
| Logs | `logs` (api.log), `logs -f` to follow, `logs --process` for what the process printed (start-up errors) |
| Stop | `deploy/research-api.sh stop` |
| Update the code | `git pull && uv sync --locked --all-extras && deploy/research-api.sh stop && deploy/research-api.sh start` |
| Update the web app | build, write `dist/version.json`, `rsync` to `incoming/`, `install-web` (no restart) |

`stop` sends SIGTERM to the service's own process group only, never to another process of yours or anyone's, waits up to 30 seconds so running requests finish and the database closes, and kills it only after that. If the pid file is stale or names another process (a recycled pid, or another copy of the service on another port), `stop` says "not running" and signals nothing. `start` and `stop` share one lock, so a `stop` that meets a `start` in progress waits for it and then stops what it started. Both run with `umask 077`, so what they write is private whatever the umask of the shell or of the cron line.

**Restart after a reboot.** The script does not install anything. If you want the service to come back after the server restarts, add this line yourself with `crontab -e` (it is a standing rule on the server, so it is your decision). The `sleep` gives the network, Tailscale and the disks time to come up; `umask 077` keeps the log file private, which `start` requires:

```cron
@reboot umask 077; sleep 60; PATH=/usr/local/bin:/usr/bin:/bin /path/to/research-engine/deploy/research-api.sh start >> /path/to/research-api/logs/cron.log 2>&1
```

Remove the line with `crontab -e` when you no longer want it. If a manual `start` and this line meet, the second waits for the first and then reports "already running"; only one service starts.

### Backups and restore

```bash
deploy/research-api.sh backup
```

makes `backups/research-api-<UTC time>.sqlite3` with SQLite's own backup call, which gives a consistent copy while the service runs, and checks it with `PRAGMA integrity_check`. If `data/` holds files it also writes `research-api-<UTC time>-data.tar.gz`. It keeps the newest 14 copies (`RESEARCH_API_BACKUP_KEEP`). A daily backup is one more crontab line, which you add yourself:

```cron
17 3 * * * umask 077; /path/to/research-engine/deploy/research-api.sh backup >> /path/to/research-api/logs/cron.log 2>&1
```

The backups are on the same disk as the data. Copy them somewhere else now and then; they hold password hashes and, later, your sets and answers, so treat them as private.

To restore, stop the service, put the copy in place and start again. The commands use absolute paths, so it does not matter where you run them from (`deploy/research-api.sh` is in the clone, the rest is in the data folder):

```bash
/path/to/research-engine/deploy/research-api.sh stop
cp /path/to/research-api/backups/research-api-<UTC time>.sqlite3 /path/to/research-api/db/research-api.sqlite3
rm -f /path/to/research-api/db/research-api.sqlite3-wal /path/to/research-api/db/research-api.sqlite3-shm
chmod 600 /path/to/research-api/db/research-api.sqlite3
# Only if you saved data files: tar adds to data/ and does not remove, so files made after the backup would stay.
# For exactly what the backup held, move data/ aside and extract into an empty one:
mv /path/to/research-api/data /path/to/research-api/data.before-restore && mkdir -m 700 /path/to/research-api/data
tar -xzf /path/to/research-api/backups/research-api-<UTC time>-data.tar.gz -C /path/to/research-api/data
/path/to/research-engine/deploy/research-api.sh start
```

Delete `data.before-restore` once you have checked the result. `start` runs `db upgrade`, so a copy from an older release is brought up to date. Accounts and sessions return as they were at the time of the copy.

Try a restore once on a scratch folder before you need it. The scratch folder has its own settings and its own port, and the real service keeps running. Run the block below in the clone of the repository, in one shell: the commands use the relative path `deploy/`. `RESEARCH_API_HOME` is set in that shell only; it wins over `env.local`, so every `deploy/research-api.sh` command in that shell works on the scratch folder. `env.local` should hold only `RESEARCH_API_HOME`: if it also set `RESEARCH_API_CONFIG`, exporting just `RESEARCH_API_HOME` would still point at the real settings file.

```bash
export RESEARCH_API_HOME=/path/to/scratch
mkdir -m 700 "$RESEARCH_API_HOME" "$RESEARCH_API_HOME/db"
cp deploy/config.example.toml "$RESEARCH_API_HOME/config.toml"
chmod 600 "$RESEARCH_API_HOME/config.toml"
# edit it: port = 8091, the same port in public_origin, and db_path, data_dir and log_dir inside /path/to/scratch
cp /path/to/research-api/backups/research-api-<UTC time>.sqlite3 "$RESEARCH_API_HOME/db/research-api.sqlite3"
chmod 600 "$RESEARCH_API_HOME/db/research-api.sqlite3"
deploy/research-api.sh start        # the scratch copy, on port 8091
deploy/research-api.sh install-web /path/to/research-api/incoming   # the scratch folder has no web app yet
deploy/research-api.sh users list   # the accounts of the copy
deploy/research-api.sh status       # the scratch service answers
deploy/research-api.sh stop         # stops the scratch copy only
unset RESEARCH_API_HOME
rm -rf /path/to/scratch             # it holds a copy of the password hashes
```

To also sign in from a browser, open `http://<tailscale-address>:8091/` in a private window: the session cookie belongs to the host and not to the port, so signing in on `:8091` in your normal window would replace your real session. With the tailnet rule this guide recommends (port 8090 only), a laptop cannot reach `:8091`; in that case check the copy with `users list` and `status` on the server, as in the block above.

### Troubleshooting

| You see | Cause and fix |
|---|---|
| `... can be read by other accounts; run: chmod go-rwx '...'` | A private file or folder is open. Run the `chmod` it names (use `chmod -R` for a folder tree). |
| `permission denied on folder ...` | The account cannot enter a folder inside the data folder, so what is in it was not checked. Run the `chmod u+rwx` it names. |
| `no settings file at ...` | Do step 1 of [First start](#first-start). |
| `something already listens on ...:8090` | Another process holds the port: pick another with `port` in `config.toml` (and `public_origin`), or, in fake mode only, `start --fake --port N` for a trial. In live mode the sign-in compares the whole origin, port included, so `start --port N` alone would answer 403 at sign-in: change `port` and `public_origin` together. |
| `the service exited; last lines of ...` | Read what follows it. A bind error means `bind_host` is not an address of this machine (is Tailscale up?); a settings error names the key. |
| The page says `The web app is not installed on this server` | Run `install-web`. |
| The sign-in answers 403 | The address in the browser is not `public_origin`, or the `Host` is not the server's. Open exactly the address in `public_origin`. |
| `429` at sign-in | Too many wrong passwords for that name; wait for `Retry-After`. |
| An old tab asks for a script and shows an error after an update | The tab runs the previous build. Reload it. |

## Export a paper's circuits as OpenQASM 3

The circuits of an ingested paper are the fenced `latex` blocks of its `document.md`. docingest can write each one as an OpenQASM 3 program, the text format that quantum SDKs and hardware providers read. The command is a read path over the corpus: it changes nothing in the store, needs no model server and no GPU, and can be run as often as you like.

From the package folder, with the configuration from step 4:

```bash
cd packages/docingest
uv run docingest circuits export -c "$CONFIG"
```

It writes one file per part of a circuit to `<output_dir>/qasm/`, named by the first 16 characters of the paper's id, the 1-based block, the circuit inside that block and the part:

```text
3fa9c2d1e8b47a60-b01-c01-p01.qasm
```

Each file starts with its provenance and wire convention. OpenQASM fixes no bit order, so docingest states its own: wire 1 at the top of the drawing is `q[0]`, and wires are never reordered.

```text
// Generated by docingest (qasm format 1)
// source: 2409.13740v1.tex, block 3, circuit 1, part 1/1
// wire 1 (top of the drawing) = q[0]; OpenQASM fixes no bit order
// wires: 1 = \ket{0}; 2 = \ket{0}
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;

h q[0];
cx q[0], q[1];
```

Select one paper with `--doc` (a full id, a unique id prefix of at least 8 characters, or the source name), write somewhere else with `--out`, and bind a free parameter with `--set`:

```bash
uv run docingest circuits export --doc 3fa9c2d1 -c "$CONFIG"
uv run docingest circuits export --doc 2409.13740v1.tex --out /tmp/qasm -c "$CONFIG"
uv run docingest circuits export --set 'theta=pi/4' -c "$CONFIG"
```

A circuit that docingest could not read completely, or that has no exact OpenQASM 3 spelling (`U`, `R_k`, `iSWAP`, an unbound symbol), is refused with the reason, and the command exits 1. The other circuits are still written; add `--allow-partial` to keep exit 0. The full refusal list is in [ADR 0006](../packages/docingest/docs/adr/0006-openqasm-3-export.md#gate-mapping).

To export in the same run that stores the papers, add `--qasm` to `ingest` or `crawl`. That step is best effort: a refusal or an unwritable folder is a warning, the papers stay stored, and the exit code is the ingestion's.

```bash
uv run docingest ingest /path/to/papers --qasm -c "$CONFIG"
```

Go back to the repository root with `cd ../..` when you are done.

## Measured times

Each row says what it was measured with. The first block is the servers, the second the commands. Where two numbers are given, they are two measurements: a lab run and a second run on a fresh install (48 GB GPUs, driver 535).

| Step | Time | Conditions |
|---|---|---|
| Start `embed`, `rerank`, `llm`, warm compile cache | 50 s, 55 s, 160 s (one after another: 251 s for the three); 55 s, 65 s, 170 s printed with `llm` started in parallel (166 s timed for the three) | models already downloaded, lab server |
| Start `embed` | 75 s and 125 s | empty compile cache in the second run, not recorded for the lab run |
| Start `rerank` | 80 s and 100 s | same |
| Start `llm` | about 231 s and 385 s | models already downloaded, empty compile cache in both; CUDA graph capture and `torch.compile` |
| Start `llm` with `LLM_EAGER=1` | about 90 s | no CUDA graphs; decoding about 3 times slower |
| `index build`, first time | 200.3 s printed, 201.1 s with start-up | 100 papers, 9,404 chunks |
| `index build`, nothing changed | 0.2 s printed, 0.90 s with start-up | same corpus |
| `index build`, small corpus | 8.3 s printed, 9.4 s with start-up | 5 papers, 388 chunks |
| `crawl --limit 5` with ingest | 32 s | 5 LaTeX sources, the 3 s spacing between requests included |
| `ask`, one question per process | 19.0 to 24.9 s, mean 21.1 s | 9 math and circuit questions on 1,249 papers (119,564 chunks), `skip_summary = true`, `answer_max_sources = 10`, 10 chunks read; program start-up included; the first question after the servers started took 21.8 s, so there is no slow first question |
| `index status` | 2.5 s | same corpus |
| One question inside the evaluation harness | 18.5 s and 21.2 s (medians) | another corpus (QASPER) and the 9,404-chunk corpus; no CLI start-up, so not comparable with the rows above |

The times of the commands depend on the papers and, for `crawl`, on arXiv.

## Sharing a machine

Several people may use one GPU machine. These rules keep your work and theirs apart:

1. **Look before you start.** `nvidia-smi` shows the memory in use per GPU. `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` shows which processes hold it.
2. **Reuse running servers.** If the three servers already answer on 127.0.0.1 ports 8001, 8002 and 8003, do not start more:

   ```bash
   for port in 8001 8002 8003; do curl -sf -m 2 "http://127.0.0.1:$port/health" > /dev/null && echo "$port up" || echo "$port down"; done
   curl -s http://127.0.0.1:8001/v1/models      # the served name: qwen-local (qwen-embed on 8002, qwen-rerank on 8003)
   ```

   The served names are the ones in `lab-server.toml`, so it works unchanged. The `model` and `revision` in `[embedder]` are only recorded in the index name, not checked against the server: make sure they name what the running embedder serves, or the index will claim vectors of a model that did not make them. GPU 1 holds one embedder and one reranker; a second pair does not fit next to it.
3. **Use your own folders.** Keep your configuration file with your own `output_dir` and `[index] dir`. The pid files, logs and compile caches of `serve.sh` default to folders under your home (`SERVING_STATE_DIR`, `SERVING_CACHE_DIR`), so they are already yours. So is uv's cache unless you share it.
4. **One writer per index.** A build (and `ingest --index`, `crawl --index`) takes the index's lock when it starts, before it embeds anything. While another process holds the index, it fails at once with `error: another process is writing to the index <folder>; wait for it to finish`, and does no embedding. Start one build at a time. `ingest --index` and `crawl --index` report a held lock as `error: the index was not updated: ...`; the papers stay stored. Reading (`ask`, `index status`) is safe next to a build. Two people who share one `output_dir` and one `[index] dir` share one index folder; give each person their own unless you mean to share.
5. **Never stop other people's processes.** `serve.sh stop` ends only the servers recorded in your own state folder. Do not kill a vLLM process that you did not start. If a GPU is busy, start your server on another one (`serve.sh embed start 0 8012`) or with a lower memory fraction (`EMBED_GPU_MEM`, `RERANK_GPU_MEM`), and point your configuration at the new port: `[embedder] base_url`, `[reranker] base_url`, and for the answering model both `[ocr] base_url` and `[qa] llm_base`.

## Troubleshooting

### Model servers

- **`serve.sh` prints `already running (pid N, <model>)`.** A server recorded in your state folder is alive, so `start` does nothing and does not change its settings. Stop it first (`serve.sh <role> stop`) to restart with other settings.
- **`vLLM exited; last log lines:`.** The five lines are only the end of the log. Open the whole log; its path was printed when you started the server (`<state dir>/<served name>.log`, by default under `~/.local/state/research-engine`). Usual causes:
  - `RuntimeError: The NVIDIA driver on your system is too old (found version 12020)` near the top of the log, ending in `Engine core initialization failed`: the vLLM build does not match the driver. Reinstall as in [Install vLLM](../serving/README.md#install-vllm).
  - GPU memory: another process holds it. Check `nvidia-smi`, then lower the fraction (`LLM_GPU_MEM`, `EMBED_GPU_MEM`, `RERANK_GPU_MEM`) or use another GPU: `serving/serve.sh embed start <GPU> <PORT>`.
  - The port is taken by another server: pick another port and update the configuration.
  - A kernel compile error at start-up: missing Python headers or `ninja`. See [Install vLLM](../serving/README.md#install-vllm).
  - CUDA graph capture fails for the answering model: `LLM_EAGER=1 serving/serve.sh llm start` as a fallback (slower decoding). It has no effect if `env.local` sets `LLM_EAGER=0`, because `env.local` is read after the environment.
- **`not ready after 10 minutes, see <log>`.** The server may still be downloading or compiling. It keeps running; follow the log with `tail -f` and use `curl .../health`.
- **It downloads into the wrong folder.** Without `HF_HOME` the models go to `$SERVING_CACHE_DIR/huggingface`, not `~/.cache/huggingface`. Check that `HF_HOME` is exported in the shell that runs `serve.sh`.
- **It does not download.** Either `HF_HUB_OFFLINE=1` is set before the models are in the cache, or the machine cannot reach the Hub.
- **Odd rerank scores.** The Qwen3 reranker needs the chat template in `serving/templates/` with its exact newlines. Do not edit the template or start the reranker without it. See [templates/README.md](../serving/templates/README.md).

### Commands that talk to the servers

- **`error: embedding server http://127.0.0.1:8002/v1/embeddings unreachable after 6 attempt(s)`**, or the same for `reranker server http://127.0.0.1:8003/rerank`. The server is not running or is on another port. The command retried for about 30 seconds before it gave up. Check `serve.sh embed status` and `curl .../health`, and compare the port with `base_url` in your configuration.
- **A traceback of thousands of lines from `ask`; its last lines say `AllModelsExhaustedError` and `Connection error`.** The answering model's server (`qwen-local`, port 8001) is unreachable. Check `serve.sh llm status` and `[qa] llm_base`. During `ingest`, a scanned page fails in a different way: the file lands in the `Failed inputs` table with `OCR server http://127.0.0.1:8001/v1/... unreachable after N attempt(s)`. Check `[ocr] base_url` for that one.
- **`... answered HTTP 401 (check api_key_env)`, or 403.** The server wants a key: set the variable named in `api_key_env` of that table.

### Ingestion

- **`error: [adapters] ocr = "mlx-vlm" needs the mlx_vlm package, which cannot be imported here`**, then `stopped at <file>: the remaining inputs were not processed` (exit status 1). You ran `docingest ingest` without `-c "$CONFIG"`, or with a configuration that keeps the default OCR, which runs the model inside the process with `mlx_vlm` and exists on Apple Silicon only. Before this check existed the same mistake showed up as `ModuleNotFoundError: No module named 'mlx_vlm'` in the `Failed inputs` table, once for every scanned file (16 of 41 files in one run). Two ways out: pass `-c "$CONFIG"` with a configuration that sets `[adapters] ocr = "openai-compatible"` and `[ocr] base_url` to your OCR server (the lab example is in [`config/examples/lab-server.toml`](../packages/docingest/config/examples/lab-server.toml)), or, on a Mac, install the extra with `uv sync --all-extras` (or add `--extra mlx` to the extras you already use: `uv sync --extra mlx` alone removes the others). PDFs that have a text layer on every page never reach OCR and are ingested whichever OCR is configured, so the check only fires at the first page that needs it.
- **`error: the OCR server http://127.0.0.1:8001/v1 ([ocr] base_url ...) is not reachable`.** The OCR server is not running or is on another port. Check `serve.sh llm status` and compare the port with `[ocr] base_url`. The check is one `GET /models` with a 5 second timeout; `refused the request (HTTP 401)` means the server wants a key, set the variable named in `[ocr] api_key_env`.
- **`blank page: 0 embedded chars (<=10), 0.00000 of the pixels inked (<0.0005)` in the log, `method=blank` in `document.md`.** The page has no text and no ink, so it was not sent to OCR and is empty on purpose (OCR used to invent a page of text for it). A scanned page that you think is blank but is not reported as such has a dark border, a shadow along the spine or stains: the rule leaves it to OCR. The thresholds are the `blank_*` keys of `[routing]` ([reference](../packages/docingest/config/README.md#routing)).
- **`math-heavy page with a text layer: N '=' signs, math symbols D% of the characters` in the log, `method=vlm_ocr` for a PDF that has a text layer.** The optional math rule is on (`math_to_ocr = true` in `[routing]`) and this page reached one of its two thresholds. The thresholds are `math_min_equals` and `math_min_density`; the probe in `manifest.json` has the two measured values (`eq_signs`, `math_density`) for every page read by a version that has the rule, so you can see how close a page that stayed on its text layer came. Results cached before that version show 0 for both.
- **`scanned page with a text layer: images cover 99% of the page, N embedded chars` in the log, `method=vlm_ocr` for a PDF that has a text layer.** The page is an old scan with an OCR layer, and the rule that reads such pages with the model applied (see [Get papers in](#5-get-papers-in)). If it does not apply to a page you expect, the images cover less than `scan_image_cover` of it (the log shows `text layer` for it).
- **A contents page shows `Title 23` where the PDF has `Title . . . . . 23`.** That is intended: docingest removes the dot leaders between an entry and its page number on every PDF and image page, so the page reads the same whether the OCR model wrote them or not. Ellipses in prose and section numbers such as `1.2.3` are not touched. See [dot leaders](../packages/docingest/README.md#dot-leaders-on-contents-lines).
- **`warning: OCR output rejected (repeating text); the text layer was used`**, or `... the page was left empty`; `(more text than the page can hold)`; `(same text as page N)`. The model looped, wrote far more than a page holds, or wrote the same text for two pages, and docingest threw that output away. The manifest keeps the reason in the page's `warnings`. If the page has a usable text layer it is used, otherwise the page is empty; `docingest ingest --force` runs it again.
- **`warning: OCR stopped at the token limit (N tokens): the page may be cut off`.** A dense page (a large table) did not fit even in the larger try, which allows twice `[ocr] max_tokens` (up to 8192), or the server refused that limit (HTTP 400: its context cannot hold prompt plus 8192). Raise `[ocr] max_tokens` (and the server's context length) if you need such pages whole.

### The index

- **`error: the index is empty`.** The index was never built, or the settings now select another index folder. The folder depends on `output_dir`, the `[embedder]` `model` and `revision`, `[qa]` `chunk_chars` and `overlap`, and the chunker version, so a docingest update that changes the chunker also counts. A change to any of them starts an empty index beside the old one. Run `docingest index build`, or restore the old settings.
- **`error: the index holds N documents but none is committed`.** An earlier build was interrupted before it committed. Run `index build`.
- **`error: the index holds vectors of embedder ...`** (it goes on: `but the configured one is ...`). The folder was written with another embedder identity than the one in your `[embedder]` table. Restore those settings, or use another `[index] dir`, then run `index build`. Vectors of two models are never mixed.
- **`warning: K of N documents of the corpus are not in the index`.** You ingested papers after the last build. The answer ignores them. Run `index build`.
- **`warning: the index has changes that are not committed yet`** and **`warning: M of K retrieved chunks belong to documents that are no longer in the corpus`.** Run `index build`; it commits pending changes and prunes the papers that left the corpus.
- **`error: another process is writing to the index <folder>; wait for it to finish`.** One writer at a time; the lock is taken when a build starts: see rule 4 of [Sharing a machine](#sharing-a-machine). Wait for the other build, or find out who holds the folder.
- **`error: the index <folder> kept changing while it was being opened; ask again`.** Builds committed repeatedly while `ask` or `status` opened the index. Run the command again.
- **`error: no ingested documents`** from `index build` (it goes on: run `docingest ingest` first), or a traceback ending with the same text from `ask`. The corpus at `output_dir` is empty. Check that you passed `-c "$CONFIG"` and that `output_dir` in it is the folder you ingested into.
- **`Invalid value: --index needs [adapters] index and embedder to be set`** (exit status 2). `ingest --index` or `crawl --index` ran without `-c "$CONFIG"`, or with a configuration that has no index.
- **`ask` takes minutes, loads weights and builds the corpus every time.** It ran with `--no-index` or a configuration without `[adapters] index`, so PaperQA2 embeds the corpus on every question.

### Spanish questions

The keyword (BM25) part of the retrieval is on only for questions that look English, because in the measurements it helped English questions and added noise to Spanish ones. A Spanish question is answered from the dense ranking and the reranker, and in a test it got a cited answer in Spanish. One exception: a question that quotes LaTeX (`$\frac{a}{b}$`, `\ket{0}`) also gets the keyword part, over the words of the formula only, because the dense ranking alone misses formulas in any language. `--bm25 always` turns the keyword part on for it, `--bm25 never` turns it off for an English one. The reasons are in [ADR 0004](../packages/docingest/docs/adr/0004-chunk-index-and-two-stage-retrieval.md).
