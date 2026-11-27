# Model servers

The research engine runs three local [vLLM](https://docs.vllm.ai/) servers with OpenAI-compatible APIs, one CPU
speech-to-text server and one GPU text-to-speech server. `serve.sh` starts, stops and inspects them; `models.tsv` pins every model to a repository and
revision.

| Role | Served name | Default model | Default GPU and port | Used for |
|---|---|---|---|---|
| `llm` | `qwen-local` | Qwen3.8-27B (AWQ 4-bit) | GPU 2, port 8001 | answers, evidence summaries, OCR |
| `embed` | `qwen-embed` | Qwen3-Embedding-4B | GPU 1, port 8002 | vectors for chunks and questions (`/v1/embeddings`) |
| `rerank` | `qwen-rerank` | Qwen3-Reranker-8B | GPU 1, port 8003 | re-scoring retrieved chunks (`/rerank`) |
| `stt` | `local-stt` | Parakeet-TDT-0.6B-v3 (ONNX) | CPU, port 8004 | speech to text (`/v1/audio/transcriptions`), see [Speech to text](#speech-to-text-stt) |
| `tts` | `local-tts` | Qwen3-TTS 0.6B CustomVoice | one GPU (`TTS_GPU`, default 0), port 8005 | text to speech (`/v1/audio/speech`), see [Text to speech](#text-to-speech-tts) |

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

For the whole path (install, the research API settings, the microphone on a laptop, troubleshooting) see [Ask by voice](../docs/run-guide.md#ask-by-voice) in the run guide. This section is the reference for the server itself.

The `stt` role is not vLLM. `stt/stt_server.py` is a small FastAPI wrapper (Apache-2.0, like the rest of the repo)
around [onnx-asr](https://github.com/istupakov/onnx-asr) (MIT), which runs a Parakeet-TDT model with onnxruntime on
the CPU, behind the Silero VAD (MIT, ONNX, downloaded from `istupakov/silero-vad-onnx` at the revision pinned in
`models.tsv`, line `stt-vad`; it is not bundled in onnx-asr), so silence gives an empty text instead of invented
words. No GPU is used; the default is 8 CPU threads (`STT_THREADS`).

| Model name | Repository (pinned in `models.tsv`) | License |
|---|---|---|
| `parakeet-tdt-0.6b-v3` (default) | `istupakov/parakeet-tdt-0.6b-v3-onnx`, ONNX conversion of `nvidia/parakeet-tdt-0.6b-v3` | CC-BY-4.0 |
| `fluid-speech-pro` | `FluidVoice-Labs/fluid-speech-pro-onnx`, fine-tune of Parakeet TDT 0.6B v3 | CC-BY-4.0 |

`fluid-speech-pro` was checked on 2026-11-19, which found: metadata consistent (license, NOTICE, base chain
Parakeet v3 via `moondream/parakeet-ultra`), weights not independently verified, publisher FluidVoice-Labs
unverified, ONNX repository created 2026-11-13. It scored slightly better than Parakeet on the sample below, but
not significantly, so `parakeet-tdt-0.6b-v3` (NVIDIA) stays the default; switch with
`serve.sh stt start 8004 fluid-speech-pro`. The default is `DEF_MODEL` in `serve.sh`.

Measured on the lab server CPU (shared machine, 8 threads, one clip at a time, through the HTTP route):

| Measure | parakeet-tdt-0.6b-v3 | fluid-speech-pro |
|---|---|---|
| Real-time factor (8 threads) | about 0.09 (about 11x real time) | about 0.09 |
| Latency per clip, median / p95 | about 0.8 s / about 2 s | about 0.8 s / about 2 s |
| RAM (RSS) idle after load | 2.3 GB | 2.3 GB |
| RAM peak after 100 requests | 3.05 GB | 3.5 GB |
| Load until `/health` | 5 s warm, 35 s on the first start (2.5 GB download included) | 35 s on the first start |
| FLEURS 50 clips WER, Spanish | 4.56% | 3.65% |
| FLEURS 50 clips WER, English | 14.37% | 12.31% |

The WER is not number-normalized (FLEURS spells numbers out), which inflates the English figures; 50 clips per
language is too few to call a 1 to 2 point gap significant. Latency at 4 threads was not measured.

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
export SERVING_CACHE_DIR=/big/disk/cache    # the first start downloads about 2.5 GB (HF_HOME also works)
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
anything that is not that WAV (a WAV cut mid-sample is read up to its last whole sample) or for an unknown `language` or `model`. `GET /health` needs no key and answers once
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

## Text to speech (tts)

The `tts` role is a small FastAPI wrapper, `tts/tts_server.py` (Apache-2.0, like the rest of the repo), around
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache-2.0, Qwen team). One process loads one model once and
turns one sentence at a time into a mono 16-bit WAV at the model's rate (24 kHz). It runs on one GPU chosen by
`TTS_GPU` (default 0, through `CUDA_VISIBLE_DEVICES`) and listens on 127.0.0.1 only. Spanish and English are both
supported by every model.

| Model name | Repository (pinned in `models.tsv`) | License | What it does |
|---|---|---|---|
| `qwen3-tts-0.6b-customvoice` (default) | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | Apache-2.0 | 9 preset speakers: `aiden`, `dylan`, `eric`, `ono_anna`, `ryan`, `serena`, `sohee`, `uncle_fu`, `vivian` |
| `qwen3-tts-1.7b-customvoice` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | Apache-2.0 | the same speakers, larger |
| `qwen3-tts-0.6b-base` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` | Apache-2.0 | clones a voice from a clip of a few seconds: the voices are the `NAME.wav` + `NAME.txt` pairs of `TTS_VOICES_DIR` |
| `qwen3-tts-1.7b-voicedesign` | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` | Apache-2.0 | designs a voice from a text description; used to make a reference clip; the server refuses to load it |

**The default voice.** A client may ask for the voice `default` (the research API does, unless
`tts_voice_es` and `tts_voice_en` name a speaker). The server maps it to `TTS_DEFAULT_VOICE`: `vivian` for the
CustomVoice models, and for the Base model the first clone in `TTS_VOICES_DIR` by name. A name that is not a voice
of the loaded model stops the load with a message that lists the voices. `GET /v1/models` shows the voices, the
`default_voice` and the alias. Any other unknown voice is still a 400.

The preset speakers are Chinese or English natives; they all speak Spanish. A designed voice is cloned to keep it
stable: VoiceDesign speaks one reference sentence, and the Base model clones that clip, so every sentence has the
same voice (a new design draw per sentence changes the voice from clip to clip).

**Two backends, same model files.** `TTS_BACKEND=faster` (default) runs the talker and the code predictor as CUDA
graphs ([faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts), MIT, on the `qwen-tts-hf` fork of
`qwen-tts`, Apache-2.0). `TTS_BACKEND=qwen` runs the plain `qwen-tts` loop, which is bound by Python and
kernel-launch time: it needs about two seconds per second of speech, too slow to read an answer aloud. Each backend
has its own pinned requirements file (`requirements.txt` for `faster`, `requirements-qwen.txt` for `qwen`).

Measured on one 48 GB workstation GPU of a shared lab machine (GPU 0, CUDA 12.4 wheels on a 535 driver, bf16,
sampling on, one request at a time, `max_new_tokens` 1024, models in the local cache). Each cell
is the median of 3 runs after a warm-up call; the real-time factor (RTF) is generation time divided by audio
length, so below 1 is faster than playback. The 5 Spanish and 2 English sentences of the check are 75 to 126
characters long and give 4 to 18 s of audio.

| Voice | Load (s) | GPU memory (GB) | Spanish sentence, s to full audio (median) | English sentence, s (median) | RTF (median) | Word error rate through Parakeet, es / en |
|---|---|---|---|---|---|---|
| 0.6B CustomVoice `ryan` | 9.1 | 4.3 | 2.5 | 1.8 | 0.29 | 0.095 / 0.038 |
| 0.6B CustomVoice `aiden` | 9.1 | 4.3 | 2.3 | 1.6 | 0.30 | 0.112 / 0.000 |
| 0.6B CustomVoice `vivian` | 9.1 | 4.3 | 2.2 | 1.8 | 0.30 | 0.095 / 0.035 |
| 1.7B CustomVoice `ryan` | 10.9 | 6.5 | 3.6 | 1.6 | 0.33 | 0.540 / 0.000 |
| 1.7B CustomVoice `aiden` | 10.9 | 6.5 | 2.2 | 1.6 | 0.36 | 0.102 / 0.000 |
| 1.7B CustomVoice `vivian` | 10.9 | 6.5 | 2.8 | 2.1 | 0.34 | 0.081 / 0.038 |
| 1.7B VoiceDesign (new draw each time) | 11.0 | 5.7 | 2.1 | 1.6 | 0.34 | 0.135 / 0.000 |
| designed voice cloned with 0.6B Base | 9.8 | 4.6 | 1.6 | 1.5 | 0.31 | 0.101 / 0.000 |

Load is the time from the call to a ready model, CUDA graph warm-up included. GPU memory is the whole process in
the benchmark; the server itself holds about 3.3 GB with the 0.6B CustomVoice model once it is ready. The plain
`qwen` backend gave an RTF of 1.8 to 2.5 for the same voices (0.6B: 11.8 to 12.9 s per sentence, median over
sentences), so a 5 s sentence took about 10 s; it is not used. The word error rate is a rough check that the
speech is understandable (the 5 Spanish sentences carry names such as Hamiltoniano and `T2`), not a measure of
how pleasant a voice is. The 1.7B `ryan` figure comes from one clip that did not stop and ran to 28 s;
the server stops that case by capping the generated tokens at `24 + 2 x characters`. Whether a voice sounds
good is for a listener to judge; the clips are not in the repository.

**Install** (any Linux machine with an NVIDIA GPU, Python 3.12 and `uv`; nothing here needs `sudo`):

```bash
uv venv --python 3.12 /path/to/tts-env
# torch first, from the CUDA 12.4 index: the default wheel of a newer torch needs a newer driver than 535
uv pip install --python /path/to/tts-env/bin/python --index-url https://download.pytorch.org/whl/cu124 torch==2.6.0 torchaudio==2.6.0
uv pip install --python /path/to/tts-env/bin/python -r serving/tts/requirements.txt
umask 077 && mkdir -p ~/.local/state/research-engine && python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.local/state/research-engine/tts.key
printf 'TTS_PYTHON=/path/to/tts-env/bin/python\nTTS_GPU=0\n' >> serving/env.local
```

The key file holds one line and must be mode 0600 (the server refuses to start otherwise). Its default place is
`$SERVING_STATE_DIR/tts.key`; set `TTS_API_KEY_FILE` for another one. The research API will send the same key.
`qwen-tts-hf` brings in packages the server does not use (gradio, librosa, the sox bindings); a missing `sox`
program only prints a warning.

**Start and test:**

```bash
serving/serve.sh tts start                  # or: start <PORT> <model name from models.tsv>
serving/serve.sh tts status
serving/serve.sh tts print-cmd
serving/serve.sh tts stop

curl -s http://127.0.0.1:8005/health        # {"ok":true} once the model is loaded, 503 while it loads
curl -s http://127.0.0.1:8005/v1/models -H "Authorization: Bearer $(cat ~/.local/state/research-engine/tts.key)"
curl -s http://127.0.0.1:8005/v1/audio/speech \
  -H "Authorization: Bearer $(cat ~/.local/state/research-engine/tts.key)" -H "Content-Type: application/json" \
  -d '{"model": "local-tts", "input": "Las fuentes están en pantalla.", "voice": "ryan", "response_format": "wav", "language": "es"}' \
  -o answer.wav
```

`POST /v1/audio/speech` takes JSON: `model` (`local-tts`), `input` (1 to 500 characters, a sentence: longer text would hit the 1024-token cap and be cut; the client sends
one sentence at a time), `voice` (a name from `GET /v1/models`, case-insensitive), `response_format` (`wav` only) and
`language` (`es`, `en` or `null` for the model to decide). It answers `200 audio/wav` (mono, 16-bit PCM, 24 kHz,
`Cache-Control: no-store`). Other answers: 401 without the Bearer key (a JSON body that does not parse can still get 422 first), 400 for an unknown
voice or model, 422 for a body that does not fit the shape above, 503 with `Retry-After` while the model loads.
`GET /health` needs no key. Text and audio are kept in memory only and never written to disk or to the log; sentences
are generated one at a time.

The wrapper tests need no model, no torch and no GPU (a stub engine stands in for the model):

```bash
uv venv --python 3.12 /tmp/tts-test && uv pip install --python /tmp/tts-test/bin/python -r serving/tts/requirements-dev.txt
/tmp/tts-test/bin/python -m pytest serving/tts
```

## Requirements

The defaults need two GPUs with 48 GB each: one for the answering model, one for the embedder and the reranker together. The figures below are `nvidia-smi` totals per GPU, measured with the default models on such machines (NVIDIA driver 535.230.02, CUDA 12.2 as `nvidia-smi` reports it).

| Role | GPU memory in use (MiB) | Model download (about) | Also measured |
|---|---|---|---|
| `llm` | 45,147 and 44,155 (two runs) at `LLM_GPU_MEM=0.9` | 20 GB | weights 19.24 GiB; KV cache at the default context of 16,384 tokens: 17.06 GiB without eager mode, 22.2 GiB with `LLM_EAGER=1` |
| `embed` | 22,664 at `EMBED_GPU_MEM=0.45` | 8 GB | |
| `rerank` | 21,608 at `RERANK_GPU_MEM=0.45` | 16 GB | |

- **One GPU for the pair.** The embedder and the reranker together used 43,017 to 43,019 MiB (two runs) and 44,635 MiB in a later run. The answering model does not fit next to them on a 48 GB GPU. The GPU 1 figures (embed, rerank, pair) include about 650 MiB that another process held; it was not subtracted.
- **Reading aloud.** The `tts` role needs one more GPU slot: the default model holds about 3.3 GB once ready (4.3 GB in the benchmark below) on the GPU of `TTS_GPU` (default 0), which is a different GPU from the answering model's in the defaults. It is optional: without it the web app reads answers with the browser's voice.
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

An existing vLLM install can be reused instead. Set the same three lines to its Python 3.12, its `vllm` and the folder that holds `ninja`, or activate its environment before running `serve.sh` and leave them unset, so that `vllm` is found on `PATH`. `VLLM_SITE_PACKAGES` is for a Python that should load the packages of another environment through `PYTHONPATH`; it is set for the vLLM roles only, so the stt and tts servers keep their own packages.

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
