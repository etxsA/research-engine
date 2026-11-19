# Model servers

The research engine runs three local [vLLM](https://docs.vllm.ai/) servers with OpenAI-compatible APIs and one CPU
speech-to-text server. `serve.sh` starts, stops and inspects them; `models.tsv` pins every model to a repository and
revision.

| Role | Served name | Default model | Default GPU and port | Used for |
|---|---|---|---|---|
| `llm` | `qwen-local` | Qwen3.8-27B (AWQ 4-bit) | GPU 2, port 8001 | answers, evidence summaries, OCR |
| `embed` | `qwen-embed` | Qwen3-Embedding-4B | GPU 1, port 8002 | vectors for chunks and questions (`/v1/embeddings`) |
| `rerank` | `qwen-rerank` | Qwen3-Reranker-8B | GPU 1, port 8003 | re-scoring retrieved chunks (`/rerank`) |
| `stt` | `local-stt` | Parakeet-TDT-0.6B-v3 (ONNX) | CPU, port 8004 | speech to text (`/v1/audio/transcriptions`), see [Speech to text](#speech-to-text-stt) |

```bash
serving/serve.sh llm start            # or: start <GPU> <PORT> <model name from models.tsv>
serving/serve.sh embed start
serving/serve.sh rerank start
serving/serve.sh rerank status
serving/serve.sh rerank print-cmd     # the exact vLLM command, without starting it
serving/serve.sh rerank stop          # stops only this server (it runs in its own process group)
```

Clients always use the served names, so switching a model (for example `embed start 1 8002 qwen3-embedding-8b`)
needs no client change. The embedder and the reranker fit together on one 48 GB GPU at 0.45 of its memory each.

For the whole path from a clone to an answer, including a shared GPU machine and troubleshooting, see the [run guide](../docs/run-guide.md).

## Speech to text (stt)

The `stt` role is not vLLM. `stt/stt_server.py` is a small FastAPI wrapper (Apache-2.0, like the rest of the repo)
around [onnx-asr](https://github.com/istupakov/onnx-asr) (MIT), which runs a Parakeet-TDT model with onnxruntime on
the CPU, behind the Silero VAD (MIT, ONNX, bundled in onnx-asr), so silence gives an empty text instead of invented
words. No GPU is used; the default is 8 CPU threads (`STT_THREADS`).

| Model name | Repository (pinned in `models.tsv`) | License |
|---|---|---|
| `parakeet-tdt-0.6b-v3` (default) | `istupakov/parakeet-tdt-0.6b-v3-onnx`, ONNX conversion of `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 |
| `fluid-speech-pro` | `FluidVoice-Labs/fluid-speech-pro-onnx`, fine-tune of Parakeet TDT 0.6B v3 | CC-BY-4.0 |

`fluid-speech-pro` is a weeks-old repository. Its pinned revision was read from the Hub on 2026-11-19 and its
provenance is not yet checked; keep `parakeet-tdt-0.6b-v3` as the default until it is, and measure both on the same
recorded questions before switching (`serve.sh stt start 8004 fluid-speech-pro`).

**Install** (any Linux machine with Python 3.12 and `uv`; nothing here needs `sudo`):

```bash
uv venv --python 3.12 /path/to/stt-env
uv pip install --python /path/to/stt-env/bin/python -r serving/stt/requirements.txt
umask 077 && mkdir -p ~/.local/state/research-engine && python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.local/state/research-engine/stt.key
echo 'STT_PYTHON=/path/to/stt-env/bin/python' >> serving/env.local
```

The key file holds one line and must be mode 0600 (the server refuses to start otherwise). Its default place is
`$SERVING_STATE_DIR/stt.key`; set `STT_API_KEY_FILE` for another one. The research API client will send the same key.

**Start and test:**

```bash
export HF_HOME=/big/disk/huggingface        # the first start downloads about 2.5 GB
serving/serve.sh stt start                  # or: start <PORT> <model name from models.tsv>
serving/serve.sh stt status
serving/serve.sh stt print-cmd
serving/serve.sh stt stop

curl -s http://127.0.0.1:8004/health
curl -s http://127.0.0.1:8004/v1/audio/transcriptions \
  -H "Authorization: Bearer $(cat ~/.local/state/research-engine/stt.key)" \
  -F file=@question.wav -F language=es -F model=local-stt
# {"text": "...", "language": "es", "duration": 3.2}
```

`POST /v1/audio/transcriptions` takes a multipart `file` (WAV, 16 kHz, mono, 16-bit PCM) with optional `language`
(`es` or `en`; the model detects the language itself, so the hint is only echoed) and `model` (`local-stt`), or the
raw WAV as the body with `Content-Type: audio/wav` and the same fields as query parameters. Answers: 401 without
the Bearer key (checked before the body is read), 411 without `Content-Length`, 413 above 2 MiB or 60 s, 422 for
anything that is not that WAV or for an unknown `language` or `model`. `GET /health` needs no key and answers once
the models are loaded. Audio is kept in memory only and never written to disk or to the log; clips are processed one
at a time. The server listens on 127.0.0.1, so it is not reachable from other machines.

The wrapper tests need no model (a stub recognizer stands in for it, and the VAD path is stubbed):

```bash
uv venv --python 3.12 /tmp/stt-test && uv pip install --python /tmp/stt-test/bin/python -r serving/stt/requirements-dev.txt
/tmp/stt-test/bin/python -m pytest serving/stt
```

**Attribution (CC-BY-4.0).** The speech recognition model is NVIDIA Parakeet-TDT-0.6B-v3
(https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3, CC-BY-4.0), converted to ONNX by istupakov
(https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx, CC-BY-4.0); changes: the ONNX conversion. If
`fluid-speech-pro` is used: "Fluid Speech Pro by FluidVoice. Built on NVIDIA Parakeet TDT 0.6B v3 and Moondream
Parakeet Ultra." (the repository's NOTICE, CC-BY-4.0). Keep this text in the user-facing About page as well.

## Requirements

The defaults need two GPUs with 48 GB each: one for the answering model, one for the embedder and the reranker together. The figures below are `nvidia-smi` totals per GPU, measured with the default models on such machines (NVIDIA driver 535.230.02, CUDA 12.2 as `nvidia-smi` reports it).

| Role | GPU memory in use (MiB) | Model download (about) | Also measured |
|---|---|---|---|
| `llm` | 45,147 and 44,155 (two runs) at `LLM_GPU_MEM=0.9` | 20 GB | weights 19.24 GiB; KV cache at the default context of 16,384 tokens: 17.06 GiB without eager mode, 22.2 GiB with `LLM_EAGER=1` |
| `embed` | 22,664 at `EMBED_GPU_MEM=0.45` | 8 GB | |
| `rerank` | 21,608 at `RERANK_GPU_MEM=0.45` | 16 GB | |

- **One GPU for the pair.** The embedder and the reranker together used 43,017 to 43,019 MiB (two runs) and 44,635 MiB in a later run. The answering model does not fit next to them on a 48 GB GPU. The GPU 1 figures (embed, rerank, pair) include about 650 MiB that another process held; it was not subtracted.
- **Smaller GPUs.** Lower the `*_GPU_MEM` fractions or pick a smaller model from `models.tsv`. No other size was measured.
- **Disk.** The three models are about 44 GB. The vLLM environment ([below](#install-vllm)) is about 11 GB and the compile caches about 235 MB. uv keeps its own cache of the downloaded wheels; after the workspace and two vLLM installs it held about 21 GB (uv hard-links from it when it is on the same disk). Where each of these goes by default and how to move it is under [Configuration](#configuration).
- **Software.** Linux with an NVIDIA driver and `nvidia-smi`, and a vLLM environment with Python 3.12.

## Install vLLM

vLLM is not part of the uv workspace. It brings its own torch and CUDA libraries, so it lives in a separate environment that `serve.sh` reaches through `VLLM_PYTHON`, `VLLM_BIN` and `SERVING_EXTRA_PATH` (or through `vllm` on `PATH`). The install below ran on the lab machines:

| Package | Version |
|---|---|
| vLLM | 0.30.0+cu129 (CUDA 12.9 build) |
| torch | 2.13.0+cu129 |
| transformers | 5.17.0 on the first install, 5.19.0 on a fresh install of the recipe (not pinned; both ran) |
| flashinfer_python | 0.6.18.post1 |

Besides the Python packages, vLLM needs a Python 3.12 **with its headers** (Triton compiles kernels at start-up) and `ninja` on `PATH`. uv's own Python builds include the headers; a Python from the operating system may need its development package (on Debian and Ubuntu, `python3.12-dev`).

Install the CUDA 12.9 build from the vLLM release page, and let uv take torch from the matching PyTorch index:

```bash
uv venv --managed-python --python 3.12 /path/to/vllm-env
uv pip install --python /path/to/vllm-env/bin/python --torch-backend cu129 \
  https://github.com/vllm-project/vllm/releases/download/v0.30.0/vllm-0.30.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl ninja
```

Why not `uv pip install vllm==0.30.0`? On PyPI that version is the CUDA 13.0 build (torch 2.13.0+cu130), which needs a newer driver than 535. On driver 535 it installs without error, `torch.cuda.is_available()` even prints `True`, and the first real CUDA call fails with `The NVIDIA driver on your system is too old (found version 12020)`, which in the server log ends as `Engine core initialization failed`. The CUDA 12.9 build exists only as the release wheel above. `--torch-backend cu129` is needed as well: adding the PyTorch index with `--extra-index-url` makes uv stop with `No solution found when resolving dependencies`. On a driver new enough for CUDA 13.0 the PyPI build may be the simpler choice; that was not tested. `ninja` from PyPI puts a `ninja` program into the environment's `bin` folder.

Check the result. The first command runs a real CUDA operation and shows which build you got:

```bash
/path/to/vllm-env/bin/python -c "import vllm, torch; print(vllm.__version__, torch.__version__, torch.version.cuda, torch.zeros(1, device='cuda').device)"
/path/to/vllm-env/bin/python -c "import sysconfig, pathlib; print((pathlib.Path(sysconfig.get_paths()['include']) / 'Python.h').exists())"
```

The first should print `0.30.0 2.13.0+cu129 12.9 cuda:0`; the second `True`. `The NVIDIA driver on your system is too old` means the build does not match the driver.

Then point `serve.sh` at the environment in `serving/env.local`:

```bash
VLLM_PYTHON=/path/to/vllm-env/bin/python
VLLM_BIN=/path/to/vllm-env/bin/vllm
SERVING_EXTRA_PATH=/path/to/vllm-env/bin     # puts ninja on PATH
```

An existing vLLM install can be reused instead. Set the same three lines to its Python 3.12, its `vllm` and the folder that holds `ninja`, or activate its environment before running `serve.sh` and leave them unset, so that `vllm` is found on `PATH`. `VLLM_SITE_PACKAGES` is for a Python that should load the packages of another environment through `PYTHONPATH`.

## First start

The first `serve.sh <role> start` downloads the pinned revision from `models.tsv`, so it needs network access and the disk space above. Where it downloads: into `HF_HOME` if that is set, otherwise into `$SERVING_CACHE_DIR/huggingface` (by default `~/.cache/research-engine/huggingface`), because `serve.sh` sets `XDG_CACHE_HOME` to its cache folder. Put the cache on a large disk and export `HF_HOME` in the shell that runs `serve.sh`; a plain assignment in `env.local` does not reach the server, so write `export HF_HOME=...` there if you keep it in that file. Once the three models are present, export `HF_HUB_OFFLINE=1` so that a start never contacts the Hub. A model that is already on disk can be used without a download: see `LLM_MODEL_PATH` under [Configuration](#configuration).

```bash
export HF_HOME=/big/disk/huggingface
serving/serve.sh embed start
serving/serve.sh rerank start
serving/serve.sh llm start
```

`start` returns when the server answers on `/health` and prints `ready after N s`. N is a multiple of 5 and can be up to 5 s above the real time. After 10 minutes `start` gives up waiting and prints the log path, but the server keeps running: follow the log and check with `status`. Times measured with the models already downloaded. The compile cache was empty in the second run and for the lab `llm` start; for the lab `embed` and `rerank` starts its state was not recorded. The last column is a later run on the lab server with the compile cache already filled by earlier starts, the three commands one after another:

| Role | Lab run | Second run, fresh install | Later run, warm cache |
|---|---|---|---|
| `embed` | 75 s | 125 s | 50 s |
| `rerank` | 80 s | 100 s | 55 s |
| `llm` | about 231 s | 385 s | 160 s |

With the warm cache the three servers were ready after 251 s (4 min 11 s). `llm` uses the other GPU, so it can start at the same time as the `embed` and `rerank` commands (`serving/serve.sh llm start &`, then the other two). In one measurement with a warm cache the commands printed `ready after` 55 s (`embed`), 65 s (`rerank`) and 170 s (`llm`), and `time` gave 166 s until all three were ready, 85 s earlier; the printed figures are rounded up to a multiple of 5 s, so the largest can be above the total.

In the second run, `llm` spent 70 s in `torch.compile`, 105 s in the profiling warm-up and about 75 s in two CUDA graph captures. The compile results are kept in `SERVING_CACHE_DIR`, so later starts skip the compilation: `embed` started again with its cache present in 60 s. The warm-cache start of `llm` without eager mode is the last column above. With `LLM_EAGER=1` (no CUDA graphs, no `torch.compile`) the start took about 90 s, but single-stream decoding was about 3 times slower. Download time comes on top of every time in the table.

## Configuration

Copy `env.example` to `env.local` (ignored by git) and set what differs on your machine: the Python and vLLM install,
the cache and state folders, the Hugging Face cache, or a local snapshot per role (`LLM_MODEL_PATH` and so on).
Nothing in this folder names a machine, a user or an absolute path.

To use a model that is already on the machine instead of downloading it, point its role at the snapshot folder
(`EMBED_MODEL_PATH` and `RERANK_MODEL_PATH` likewise; `--revision` is then left out and the revision is the one of that folder):

```bash
LLM_MODEL_PATH=/path/to/models--cyankiwi--Qwen3.8-27B-AWQ-INT4/snapshots/<revision>
```

Where things go by default, and how to move them. All of them default to folders under your home, which can be a small disk:

| What | Default | Size | Move it with |
|---|---|---|---|
| pid files and server logs | `~/.local/state/research-engine` | small | `SERVING_STATE_DIR` |
| vLLM, Triton and `torch.compile` caches | `~/.cache/research-engine` | about 235 MB | `SERVING_CACHE_DIR` |
| downloaded models | `$SERVING_CACHE_DIR/huggingface` | about 44 GB | `HF_HOME` |
| uv's cache of downloaded wheels | uv's default | up to about 21 GB | `UV_CACHE_DIR` |

## Notes

- `llm` starts with the chat template's thinking mode off, so answers and OCR output contain no reasoning text.
  `LLM_EAGER=1` adds `--enforce-eager` (slower decoding; only as a fallback if CUDA graph capture fails).
- The two rerankers based on causal language models (Qwen3-Reranker, mxbai-rerank-v2) are scored through two token
  logits (`--hf-overrides` in `models.tsv`) and need the chat templates in `templates/`. See
  [templates/README.md](templates/README.md) for why those differ from vLLM's examples by one newline.
- Qwen3-Reranker takes a task instruction per request (`"instruction"` field of `/rerank`); without one it uses
  "Given a web search query, retrieve relevant passages that answer the query".
