#!/usr/bin/env bash
# Start, stop or inspect the model servers of the research engine on one machine: three vLLM servers and the
# CPU speech-to-text server.
#
#   serving/serve.sh <role> start [GPU] [PORT] [MODEL] | stop | status | print-cmd [GPU] [PORT] [MODEL]
#   serving/serve.sh stt start [PORT] [MODEL] | stop | status | print-cmd [PORT] [MODEL]   (CPU only, no GPU argument)
#
# Roles and defaults (served model names stay fixed, so clients never change):
#   llm     answers questions and runs OCR    served as qwen-local   GPU 2, port 8001, qwen3.8-27b-awq
#   embed   turns texts into vectors          served as qwen-embed   GPU 1, port 8002, qwen3-embedding-4b
#   rerank  re-scores retrieved passages      served as qwen-rerank  GPU 1, port 8003, qwen3-reranker-8b
#   stt     turns speech into text (CPU)      served as local-stt    port 8004, parakeet-tdt-0.6b-v3
# MODEL is a name from models.tsv (pinned repository and revision). print-cmd shows the command without starting it.
# Machine-specific settings come from the environment or from serving/env.local (see env.example); nothing in this
# folder names a machine, a user or a path outside it.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
[ -f "$HERE/env.local" ] && . "$HERE/env.local"

STATE=${SERVING_STATE_DIR:-$HOME/.local/state/research-engine}
CACHE=${SERVING_CACHE_DIR:-$HOME/.cache/research-engine}
PY=${VLLM_PYTHON:-python3}
STT_PY=${STT_PYTHON:-python3}

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

ROLE=${1:-}; ACTION=${2:-status}
case "$ROLE" in
  llm) SERVED=qwen-local; DEF_GPU=2; DEF_PORT=8001; DEF_MODEL=qwen3.8-27b-awq; MEM=${LLM_GPU_MEM:-0.9}; MAXLEN=${LLM_MAX_LEN:-16384} ;;
  embed) SERVED=qwen-embed; DEF_GPU=1; DEF_PORT=8002; DEF_MODEL=qwen3-embedding-4b; MEM=${EMBED_GPU_MEM:-0.45}; MAXLEN=${EMBED_MAX_LEN:-8192} ;;
  rerank) SERVED=qwen-rerank; DEF_GPU=1; DEF_PORT=8003; DEF_MODEL=qwen3-reranker-8b; MEM=${RERANK_GPU_MEM:-0.45}; MAXLEN=${RERANK_MAX_LEN:-8192} ;;
  stt) SERVED=local-stt; DEF_GPU=; DEF_PORT=8004; DEF_MODEL=parakeet-tdt-0.6b-v3; MEM=; MAXLEN= ;;
  *) usage ;;
esac
PIDF=$STATE/$SERVED.pid; LOG=$STATE/$SERVED.log

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

model_line() {  # name -> "repo<TAB>revision<TAB>overrides<TAB>template" for this role
  awk -F'\t' -v r="$ROLE" -v n="$1" '$1 == r && $2 == n { print $3 "\t" $4 "\t" $5 "\t" $6 }' "$HERE/models.tsv"
}

build_stt_cmd() {  # fills CMD for the stt server: model name $1, port $2. Runs on the CPU, so there is no GPU.
  local line repo rev ovr tpl vad vrepo vrev key
  line=$(model_line "$1")
  [ -n "$line" ] || { echo "unknown stt model '$1' (see $HERE/models.tsv)" >&2; exit 1; }
  IFS=$'\t' read -r repo rev ovr tpl <<< "$line"
  vad=$(ROLE=stt-vad model_line silero-vad)
  IFS=$'\t' read -r vrepo vrev ovr tpl <<< "$vad"
  key=${STT_API_KEY_FILE:-$STATE/stt.key}
  [ -f "$key" ] || { echo "no API key file at $key (create it, see serving/README.md; or set STT_API_KEY_FILE)" >&2; exit 1; }
  CMD=(env "STT_MODEL_REPO=$repo" "STT_MODEL_REVISION=$rev" "STT_VAD_REPO=$vrepo" "STT_VAD_REVISION=$vrev"
       "STT_THREADS=${STT_THREADS:-8}" "STT_API_KEY_FILE=$key")
  [ -n "${STT_MODEL_PATH:-}" ] && CMD+=("STT_MODEL_PATH=$STT_MODEL_PATH")
  [ -n "${STT_VAD_PATH:-}" ] && CMD+=("STT_VAD_PATH=$STT_VAD_PATH")
  CMD+=("$STT_PY" -m uvicorn --app-dir "$HERE/stt" --factory stt_server:create_app_from_env
        --host 127.0.0.1 --port "$2" --log-level warning)
}

build_cmd() {  # fills CMD for model name $1 on port $2
  local line repo rev ovr tpl src
  if [ "$ROLE" = stt ]; then build_stt_cmd "$1" "$2"; return 0; fi
  line=$(model_line "$1")
  [ -n "$line" ] || { echo "unknown $ROLE model '$1' (see $HERE/models.tsv)" >&2; exit 1; }
  IFS=$'\t' read -r repo rev ovr tpl <<< "$line"
  # A local snapshot (for example a shared model folder) can replace the download: <ROLE>_MODEL_PATH.
  local var; var="$(echo "$ROLE" | tr '[:lower:]' '[:upper:]')_MODEL_PATH"
  src=${!var:-$repo}
  if [ -n "${VLLM_BIN:-}" ]; then CMD=("$PY" "$VLLM_BIN" serve); else CMD=(vllm serve); fi
  CMD+=("$src" --served-model-name "$SERVED" --host 127.0.0.1 --port "$2"
        --max-model-len "$MAXLEN" --gpu-memory-utilization "$MEM")
  [ "$src" = "$repo" ] && CMD+=(--revision "$rev")
  case "$ROLE" in
    llm)
      CMD+=(--default-chat-template-kwargs '{"enable_thinking": false}')   # direct answers, no reasoning text
      [ "${LLM_EAGER:-0}" = 1 ] && CMD+=(--enforce-eager) ;;             # eager mode only as a fallback
    embed | rerank) CMD+=(--runner pooling) ;;
  esac
  [ "$ovr" != - ] && CMD+=(--hf-overrides "$ovr")
  [ "$tpl" != - ] && CMD+=(--chat-template "$HERE/templates/$tpl")
  return 0
}

case "$ACTION" in
start | print-cmd)
  if [ "$ROLE" = stt ]; then GPU=; PORT=${3:-$DEF_PORT}; NAME=${4:-$DEF_MODEL}   # CPU: no GPU argument
  else GPU=${3:-$DEF_GPU}; PORT=${4:-$DEF_PORT}; NAME=${5:-$DEF_MODEL}; fi
  build_cmd "$NAME" "$PORT"
  if [ "$ACTION" = print-cmd ]; then printf 'CUDA_VISIBLE_DEVICES=%s' "$GPU"; printf ' %q' "${CMD[@]}"; echo; exit 0; fi
  if running; then echo "already running (pid $(cat "$PIDF"), $(cat "$STATE/$SERVED.model"))"; exit 0; fi
  mkdir -p "$STATE" "$CACHE"
  [ -n "${VLLM_SITE_PACKAGES:-}" ] && export PYTHONPATH=$VLLM_SITE_PACKAGES
  [ -n "${SERVING_EXTRA_PATH:-}" ] && export PATH=$SERVING_EXTRA_PATH:$PATH
  export VLLM_USE_FLASHINFER_SAMPLER=${VLLM_USE_FLASHINFER_SAMPLER:-0}   # avoids a CUDA compile at start-up
  export XDG_CACHE_HOME=$CACHE VLLM_CACHE_ROOT=$CACHE/vllm TRITON_CACHE_DIR=$CACHE/triton
  # setsid: the server gets its own process group, so stop ends only this server's processes.
  [ "$ROLE" = stt ] && export OMP_NUM_THREADS=${STT_THREADS:-8}
  CUDA_VISIBLE_DEVICES=$GPU nohup setsid "${CMD[@]}" > "$LOG" 2>&1 &
  echo $! > "$PIDF"; echo "$PORT" > "$STATE/$SERVED.port"; echo "$NAME" > "$STATE/$SERVED.model"
  echo "starting $SERVED ($NAME) on ${GPU:+GPU $GPU}${GPU:-the CPU} at http://127.0.0.1:$PORT (pid $!), log $LOG"
  for i in $(seq 1 120); do
    if curl -sf -m 2 "http://127.0.0.1:$PORT/health" > /dev/null; then echo "ready after $((i * 5)) s"; exit 0; fi
    running || { echo "server exited; last log lines:"; tail -5 "$LOG"; exit 1; }
    sleep 5
  done
  echo "not ready after 10 minutes, see $LOG"; exit 1 ;;
stop)
  if running; then
    PID=$(cat "$PIDF"); echo "stopping $SERVED (pid $PID and its process group)"
    kill -TERM -- "-$PID" 2> /dev/null || kill "$PID" 2> /dev/null || true
    for _ in $(seq 1 30); do kill -0 -- "-$PID" 2> /dev/null || break; sleep 1; done
    kill -KILL -- "-$PID" 2> /dev/null || true
  fi
  rm -f "$PIDF" "$STATE/$SERVED.model"; echo "stopped" ;;
status)
  if running; then
    echo "running (pid $(cat "$PIDF"), port $(cat "$STATE/$SERVED.port"), model $(cat "$STATE/$SERVED.model"))"
  else echo "not running"; fi ;;
*) usage ;;
esac
