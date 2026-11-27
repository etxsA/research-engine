# Circuit retrieval and assembly

**Status: design proposal.** This note describes how circuits could be taken from papers as
evidence, stored with their provenance and handed to an agent. It is not a description of shipped
behavior, except where it says what the code does today. It comes from the design notes of
LaloAsetsu, cleaned up and fitted to the current code.

## What exists today

- `docingest` reads circuits from the LaTeX of a paper (`quantikz`, `Qcircuit`, `yquant`, and
  `tikzcd` with circuit commands). The author's exact source stays in `document.md` as a fenced
  `latex` block.
- `gate_list(source)` turns a block into `circuit/1` data. The readers refuse by default: a token
  that no rule understands makes the circuit `partial`, and the simulator then refuses it.
- `quantum-sim` runs and checks `circuit/1` documents, and writes OpenQASM 3 only for what its own
  reader accepts ([ADR 0006](../packages/docingest/docs/adr/0006-openqasm-3-export.md)).
- Circuits are read on demand from `document.md`. There is no separate circuit file in the store.

## What this proposal adds

Circuits that are not LaTeX, such as a figure in a scanned paper or a plain-text description, and a
catalog that can be searched. Everything below is a proposal and needs a decision before work
starts.

### Safety rule

The pipeline keeps the evidence and never turns an uncertain reading into runnable code. A circuit
is executable only when all of these hold:

1. Wire order, operations, controls, measurements and parameters are fully resolved.
2. The record passes the `circuit/1` schema and the supported-operation checks.
3. `quantum-sim` accepts it and returns a validation result.
4. The source citation, the extraction method, the confidence and the parser or model version are
   recorded.

An agent must refuse to simulate, export or assemble a record that is `needs_review`, `rejected`,
partial or unsupported. It may still retrieve the original evidence and say why the record was
refused.

### Sources

**LaTeX and source text.** The deterministic reader already works this way. It emits a circuit only
when it consumed the whole source. Unknown commands, unclear controls and unsupported operations
are kept as refusal reasons.

**Text descriptions.** A constrained grammar could read explicit sentences such as "apply H to q0,
then CNOT with control q0 and target q1". It must not guess a missing wire order, gate arity,
parameter value or control direction. A description that lacks one of these stays a candidate for
review.

**Figures.** A layout parser (see [rag-multimodal-upgrade.md](rag-multimodal-upgrade.md)) finds
figure regions. A vision or OCR adapter would return observations: wires and their order, gates and
labels, controls, targets, swaps and measurements, parameters and phase factors, and the page,
region, bounding box, model and confidence of each. Observations are evidence, not circuits. A
circuit is rebuilt only when geometry and meaning are clear. Low-confidence labels, crossing wires,
unresolved controls and unclear parameter text give `needs_review`. The image and every observation
stay available for annotation or a later rerun.

### Candidate record

| Field | Meaning |
|---|---|
| `id` | Stable record identifier |
| `document_id` | Content-addressed source document |
| `page`, `region` | Page and figure or layout region |
| `source_span` | Character or byte span, when there is source text |
| `source_kind` | `latex`, `text`, `image` or `layout` |
| `original_evidence` | Original code, description, OCR text or image reference |
| `observations` | Gates, wires, controls, measurements and parameters |
| `normalized_circuit` | The validated `circuit/1`, when there is one |
| `confidence` | Extraction confidence from 0 to 1 |
| `review_status` | `accepted`, `needs_review` or `rejected` |
| `validation` | Schema, simulator and export checks, with refusal reasons |
| `extractor` | Parser or vision adapter and its version |
| `model_revision` | Vision or OCR model revision, if one was used |
| `artifact_hash` | Hash of the evidence and the normalized circuit |

Records are never overwritten. A new parser or model version writes a new extraction revision.

### `circuit/1` stays as it is

`circuit/1` remains the format between `docingest`, `quantum-sim` and agents: 1-based wires, ordered
steps, explicit targets, closed and open controls, and explicit operation kinds. Provenance and
validation would sit around a circuit, not inside the format. Unsupported operations stay explicit
and are refused, never approximated.

### Storage and retrieval

A first version of this idea stored the readings in a `circuits.json` file beside each document.
That file was dropped. Main reads circuits on demand from `document.md`, so a stored file would go
stale when a reader improves and nobody re-ingests, and it would be a new data structure the
project did not need. If a stored form is ever needed for figure observations, it should carry the
reader version and be rebuilt when that version changes. That is a decision for the lead.

For search, the research API could project records into SQLite with a key made of document,
source region or span, extraction revision and artifact hash. The projection must be rebuildable
from the files. Filters would cover citation, page, gate family, symbolic parameter, source,
confidence, review status and validation status. A result must name the same index generation as
its text evidence. A stale or missing embedding must not be shown as searchable.

### Agent contract

An agent may retrieve a circuit with its evidence, explain steps, parameters, provenance and
validation, compare two validated circuits through `quantum-sim`, and ask for human review of an
open candidate. For a refused record the tool answer should carry the exact open evidence and a
structured reason, for example `unknown gate`, `wire order unresolved`,
`image confidence below threshold` or `unsupported measurement`.

### OpenQASM 3

Export exists already and is described in ADR 0006. For records that come from figures, the same
rule applies: export only after `circuit/1` validation, keep document, page, source hash,
extraction revision and validation result with the program, and check round trips against
`quantum-sim` semantics. Text that looks like the paper is not a validation.

### Suggested order

1. Finish the LaTeX and constrained-text readers.
2. Add the vision or OCR observation adapter and a review queue.
3. Project records into SQLite and add circuit-aware retrieval.
4. Enforce the validation rule in the agent tools.
5. Measure accepted, rejected and review-needed circuits on the corpus.

## Running MinerU on scans

If the optional MinerU adapter is used for scans, keep concurrency at one and process a failed
document on its own. A native exit code `-11` is a crash of the runtime. It is not evidence that a
paper has no circuit. Retrying with the text-only mode is an explicit fallback for papers whose
text layer is readable. It must never replace OCR for papers that are only images.
