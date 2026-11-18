# qa-bench

The shared formats of the question-answer benchmark and the tools that read, write, check, convert and score them. Four JSON-lines formats carry a benchmark from the questions to the numbers: `qset/1` (a question set), `qa-run/1` (the answers of one run), `qa-score/1` (what a scorer computed from runs) and `reviews/1` (human and checker decisions). The package depends on `jsonschema` only; it does not import docingest, quantum-sim, numpy or an HTTP client, and none of them imports it.

**Status: the formats, their tools and the scorer.** Schemas, strict readers and deterministic writers, the rules between files, applying reviews to a set, converters of older question sets and answer captures, the scorer, and the `qa-bench` command (`validate`, `convert` and `score`) are implemented and tested on synthetic data. The runner is a separate change.

## Contents

- [Use it](#use-it)
- [The formats](#the-formats)
- [Rules the schemas cannot show](#rules-the-schemas-cannot-show)
- [Reviews](#reviews)
- [Converters](#converters)
- [The scorer](#the-scorer)
- [The commands](#the-commands)
- [Code map](#code-map)
- [Tests and checks](#tests-and-checks)

## Use it

```python
from qa_bench.formats import check_against_set, read_qset, read_run, write

qset = read_qset("my-set.qset.jsonl")          # FormatError with every problem if it breaks a rule
run = read_run("my-run.qa-run.jsonl.gz")       # plain or gzip
check_against_set(run, qset)                   # [] when the run belongs to this set file
md5 = write("copy.qset.jsonl", qset)          # the md5 of the bytes written: the set's identity
```

| Function | Returns |
|---|---|
| `load(path, expected=None)`, `load_bytes(data, ...)`, `parse(text, ...)` | a `Loaded` with `.file` (a `FormatFile`) and `.problems`; nothing is raised for bad content |
| `read(path, expected=None)`, `read_qset`, `read_run`, `read_score`, `read_reviews` | a `FormatFile` (`format`, `header`, `records`, `md5`) or a `FormatError` that carries every `Problem` |
| `build(format, header, records)` | a `FormatFile` made in memory, after every line and every file rule has been checked |
| `write(path, file)` | writes the file (gzip when the name ends in `.gz`, with no time stamp) and returns the md5 of the uncompressed bytes it wrote |
| `check_line(format, record, header=...)` | the problems of one line against the JSON Schema (`line`, JSON pointer `path`, `message`) |
| `check_against_set(file, qset)` | what a run, score or reviews file gets wrong about its set |
| `qa_bench.reviews.apply_reviews`, `apply_to_set` | the items after the latest decision of each, and a new set version |
| `qa_bench.convert.to_qset`, `from_qset` | an older set file as a valid `qset/1` file, and back |
| `qa_bench.convert.capture_to_item`, `parse_citations` | an ask capture record as a `qa-run/1` item; the References list as citations |
| `qa_bench.score.score(qset, runs, created=..., options=...)` | a `Scored` with the `qa-score/1` file (`.file`) and the warnings; `ScoreError` when the inputs cannot be scored |

## The formats

Each format is one JSON object per line. Line 1 is the header and the only line with `format`. The JSON Schemas (draft 2020-12) are package data in [`src/qa_bench/formats/schemas/`](src/qa_bench/formats/schemas); `load_schema("qset/1")` returns one.

| Format | File | Line 1 | Other lines |
|---|---|---|---|
| `qset/1` | one question set | `set`, `created`, `source`, `notes` | one item per question: `id`, `lang`, `form`, `corpus`, `gold_papers`, key facts, circuit, tags, ... |
| `qa-run/1` | one run: one set, one arm (`bare`, `harness`, `oracle`, `agentv1`), one model | what is needed to rerun it | one answer per `(id, repeat)`: answer, citations, contexts, candidates, timings, usage, error |
| `qa-score/1` | what a scorer computed | scorer, set, runs, bootstrap settings | `record: item`, `group` or `compare` |
| `reviews/1` | decisions, append only | `set`, `created` | `target: item` (accept, edit, reject) or `answer` (right, partial, wrong, not_attempted) |

Shared conventions:

- Ids match `[A-Za-z0-9][A-Za-z0-9._-]*`. A doc id is docingest's sha256 (64 hex); a 16-hex prefix is accepted where a source only has that.
- Dates are ISO 8601 with an offset (`2026-01-03T09:00:00+00:00` or `Z`); the check is written in the package, so it does not depend on which optional packages are installed.
- Unknown fields are refused. The open objects are `ext` (fields only one source set has), `tags`, `metrics`, `seeds`, `arm.overrides` and a review's `edit`.
- Within `/1` only optional fields are added; anything else makes `/2` with a converter.
- The md5 of a set or a run is the md5 of its text, never of the gzip bytes. A run points at its set by that md5. A file read keeps the md5 of the text it was read from; `write` returns the md5 of the text it wrote.
- A file is read strictly: a key given twice on a line, `NaN`, `Infinity`, a number too large for a float (`1e400`), an unpaired surrogate escape (`\ud800`, which UTF-8 cannot hold) and arrays or objects nested more than 64 levels deep (the same limit on every machine) are refused, so a file that passes the check can always be written, and a blank line is skipped. Lines end at a line feed only, so U+2028, U+2029 and U+0085 inside a string are text. Writing uses `ensure_ascii=False` and the default separators. A file already in that form is written back byte for byte, with the same md5. A file in another form (CRLF line ends, other spacing, `\u` escapes, blank lines) is written in that form, so it gets a new md5; use the md5 that `write` returns.

## Rules the schemas cannot show

A schema sees one line. `load` and `build` also check these:

| Format | Rule |
|---|---|
| `qset/1` | ids are unique; `mirror_of` names an item of the file |
| `qa-run/1` | `(id, repeat)` is unique; a `bare` run has no contexts |
| `qa-score/1` | every run named by a record, and by `noise_floor`, is in the header's `runs`; no record appears twice |
| `reviews/1` | none beyond the schema |

`check_against_set(file, qset)` adds the rules between files:

| File | Rule |
|---|---|
| `qa-run/1` | `set.name` and `set.md5` are the set's; every answer is for an item of the set; an `oracle` answer's contexts are the gold passages (`md_text`, in set order), except on an error line, which has no answer and no contexts to check |
| `qa-score/1` | `set.name` and `set.md5` are the set's; every `item` record is for an item of the set |
| `reviews/1` | `set` is the set's name; every decision names an item of the set |

## Reviews

`apply_reviews(items, lines, set_md5=None, roles=("human",))` follows the rule of `reviews/1`: for each item the decision with the latest `date` holds (dates compare as instants), and on equal dates the later line in the file. `accept` sets the item's `reviewer`, `edit` replaces the named fields and sets `reviewer`, `reject` drops the item. The result lists what was accepted, edited, rejected and not reviewed, the decisions made on another set md5 (`stale`, still applied) and the decisions about ids the set does not have (`unknown`). An edit that names no fields (the schema allows an `edit` line without an `edit` object) or leaves an item `qset/1` refuses raises `ReviewError`.

Only lines with `role: human` count unless `roles` says otherwise: an independent agent's check does not make an item gold and does not drop it. `apply_to_set(qset, reviews, created=..., set_name=...)` gives the new version as a valid `qset/1` file; its header notes name the set it came from by md5 and the counts, among them the decisions made on another version of the set. Rejecting an item that a mirror names is refused, because the result would break the `mirror_of` rule. Whether an item has the two different human accepts that make it gold is a question for the caller; this function does not compute it.

## Converters

The older question-set files are read by shape. `to_qset(shape, data, set_name=..., created=..., corpus=..., source_file=...)` returns a valid `qset/1` file whose header records the source file name and md5; `from_qset(shape, qset)` gives back the rows of the source file.

| Shape | Source | Notes |
|---|---|---|
| `mathcirc-v1` | JSON lines, one gold paper per question | passages are grouped by paper; category, circuit kind, survival, subfield and mirrored category become `tags`; the rest goes to `ext`; `corpus` is required |
| `circuit-tasks-v1` | JSON lines, `kind` `verify` or `create` | `circuit_verify` (about a block of a paper) or `circuit_create` (corpus `none`, no gold paper) |
| `circuit-claims-v1` | TSV | `circuit_claim`; the id is `CL-<paper_id>-b<block_index>`; an `input_state` of `-` is left out |
| `qubitqa-pilot` | JSON lines, multiple choice | `choices`; a row with a parent is a mirror; the row names its corpus; an unanswerable row is refused for lack of a `search_record` |
| `qasper` | JSON lines, several reference answers | the first reference is the `answer`, all of them stay in `ext.refs`; no gold papers (the source has an arXiv id, the format needs a doc id) |

Every shape round-trips: reading the converted set back gives the same rows (equal as JSON objects, or as TSV cells). The bytes are the same only when the source writes its keys in the order the converter writes them; the key order, the spacing and the spelling of the source are not kept. `corpus` is an argument, not a built-in: the converters name no corpus of their own. A row that does not fit its shape raises `ConversionError` with the row id.

Answer captures: `capture_to_item(record, retrieval=None, max_tokens=1024)` turns one record of the ask capture script (the answer, the formatted answer with its References list, the contexts the answer model got, the reranked candidates, timings, server counters, the answer prompt) into a `qa-run/1` item. It keeps what the scorers read; what the format has no field for goes to `ext` (`given`, `answer_prompt`, `llm_calls`, `retrieval`). `parse_citations(text, full_ids)` reads `N. (<16 hex> pages A-B)` entries and maps each to the full doc id. The converters of the other captures (QASPER arms, the smoke test, stripped captures) were one-off scripts tied to files on a server and are not part of the package.

## The scorer

`score` turns a question set and the runs made on it into a `qa-score/1` file. It never calls a model, and the same inputs give the same bytes (the time in the header is an argument).

```python
from qa_bench.formats import read_qset, read_run, write
from qa_bench.score import NamedRun, Options, score

qset = read_qset("my-set.qset.jsonl")
runs = [NamedRun(p, read_run(p)) for p in ("main.qa-run.jsonl.gz", "branch.qa-run.jsonl.gz")]
options = Options(pairs=(("main", "branch"),))     # run ids; diff = branch minus main
scored = score(qset, runs, created="2026-01-05T09:00:00+00:00", options=options)
write("main-vs-branch.qa-score.jsonl", scored.file)
```

**What it writes.** An `item` record per answer (its metrics, and for a question with key facts one record per key), a `group` record per run, group and metric (`n`, `mean`, and a 95% bootstrap interval `ci` for a group of two or more questions), and a `compare` record per pair, group and metric (`base`, `var`, `diff` = variant minus base, `ci`, `up` and `down` as counts of questions, and `floor` and `counts` when a noise pair is given). A repeated question counts once: the metrics of its repeats are averaged before the groups and the bootstrap, so the bootstrap resamples questions (cluster `id`).

**Order.** Questions are taken in the order of the question-set file, never the order of a run file. The bootstrap resamples positions of a list, so another order moves the last digit of an interval; the tests shuffle the run files to pin this.

**Metrics.** Names are open in `qa-score/1`; these are written:

| Metrics | When | Meaning |
|---|---|---|
| `all_keys`, `key_share` | the question has `key_facts` | all keys found in the answer (exact), and the share of keys found |
| `all_keys_lenient`, `key_share_lenient` | same | the same with the lenient rule (an expression key counts at a symbol recall of 0.8 or more; see below) |
| `all_keys_words`, `all_keys_lenient_words`, `key_share_words`, `key_share_lenient_words` | same | the four metrics above with number words counted; equal to them when the options count number words |
| `num_word_only`, `lenient_unchecked` | same | some number key is matched only by a number word; some lenient hit of category `f` was left out because it is not an exact hit |
| `chunks`, `evidence` | the run kept the text the model was given (`ext.given`) or its prompt (`ext.answer_prompt`, else `ext.summaries`) | share of keys found in the chunks, and in the prompt |
| `math_spans`, `unsupported_math` | both of the above | math spans in the answer, and how many are in neither the chunks nor the prompt |
| `cannot_answer`, `answer_chars` | key facts | the answer matches the cannot-answer pattern; its length |
| `psg10`, `psg50`, `rr1`, `rr1_strict` | `ext.retrieval` holds ranks | the gold passage ranks within 10 or 50 after hybrid retrieval, first or first strictly after reranking |
| `paper_recall10`, `paper_recall50`, `all_gold_present` | the question has gold papers and the run is not `bare` (`paper_recall50` also needs `candidates`) | the share of the gold papers with a chunk among the first 10 contexts and among the first 50 candidates, and 1.0 when every gold paper has a chunk among all the contexts the run gave, else 0.0 (see below) |
| `contexts_used` | always | the number of contexts of the answer |
| `keys_per_paper_mean`, `keys_per_paper_min` | the question has key facts and some key names a gold paper (`key_facts[].paper`) | for each gold paper with at least one key, the share of its keys found exactly in the answer (every key type); the mean over those papers and the weakest one |
| `answer_f1`, `answer_good`, `refused` | the set item has `ext.refs` and the run has `ext.short` | token F1 of the short answer against the best reference, F1 at least 0.5, and the answer says it cannot answer |
| `wall_s`, `prompt_tokens`, `completion_tokens`, `llm_requests`, `answer_prompt_tokens`, `error` | always (a number is null when the run lacks it) | cost of the answer and whether it failed |

A metric whose inputs a run lacks is `null` on the item and is left out of the groups.

**Multi-paper metrics.** A paper is told by the first 16 hex characters of its doc id. A context or candidate counts as evidence unless it has `is_reference: true`; a context without `is_reference` counts as not a reference.

- `paper_recall10` uses the first 10 contexts, in the order of the run, and `paper_recall50` the first 50 `candidates`, in the order given; `paper_recall50` is `null` when the answer has no `candidates`, and an empty list of `candidates` counts as none.
- `all_gold_present` depends on the number of contexts the run gave, not on 10: it is 1.0 when every gold paper has a chunk among all of them. It equals `paper_recall10 == 1` only with 10 contexts or fewer; with more (for example an arm that gives 15 or 20) it does not.
- `contexts_used` is the number of contexts of the answer.
- All three retrieval metrics are `null` for a question without gold papers and for a `bare` run, which retrieves nothing. An arm that retrieves and gave an empty list of contexts scores 0.0 (a retrieval failure), as does an answer whose contexts hold no gold paper. An answer line with an `error` (a timeout, say) usually has no contexts, so on an arm that retrieves it also scores 0.0 and lowers the recall means; read the number of errors (`error`) next to them. The `oracle` arm scores 1.0 when its contexts carry the gold doc ids, there are at most 10 gold passages and the line is not an error line.
- A `null` is left out of the group mean, which reports its own `n`; a paired comparison uses only the questions that have a value in both runs, and its `n` counts them.
- `keys_per_paper_mean` and `keys_per_paper_min` count the exact hits of every key type (expression, text and number) among the keys whose paper is a gold paper of the question; keys with no paper, or of a paper that is not gold, are ignored, and both are `null` when no key is left. There is no lenient variant, and they follow `--number-words` like `key_share` (there is no `_words` variant).

**Key matching** uses the matcher in `qa_bench/score/mathnorm.py`, version 2 of the benchmark's matcher, vendored. Its code is unchanged (a test pins the md5 of everything after its docstring); the scorer calls its functions and decides these rules itself, in `score/keys.py`:

- *Number words are off.* An integer key from 2 to 20 matches the digits in an answer only. The matcher can also read `eight` or `ocho`, but a number word counts wherever it stands ("eight or nine gates" for a circuit other than the one asked about), and a hand check of such hits found about half of them wrong. A key that only a number word would match is flagged `word_only` on its key record, and the answer counts in `num_word_only`; the metrics ending in `_words` give the result with the words counted, so a table can show both columns. `Options(number_words=True)` (`--number-words`) makes them count in the main columns; the header then says `v2+words`. The main columns without number words are not the "checked" column of the hand checks: they also drop the word hits a person found right, and the `_words` columns with `word_only` are the list to check by hand. The matcher's own details of the word route (a non-breaking hyphen or an en dash in a compound, "hundred and two") are left as they are: they only touch the `_words` columns.
- *Citation keys are not numbers.* `(<16 hex> pages a-b)` (one key, or several in one pair of parentheses) is cut from the text (the answer, and the chunks and the prompt for `chunks` and `evidence`) before a number key is matched, so the digits of a page range do not make a hit. Text and expression keys see the text as it is. The rule covers the form docingest writes; other forms (a bare `<id> pages 3-4`, square brackets, "pages 1-2, 3-4") are left alone.
- *A lenient hit in category `f` does not count.* For an item whose tag `category` is `f`, a key matched only by symbol recall is not a lenient hit: the letters of a short equation occur in any prose, a refusal included, and a hand check of the answers that cite the gold paper found most of those hits wrong or partly right. The key record says `unchecked` (the name is kept; it now marks a lenient hit that does not count), `lenient_unchecked` counts the answers, and for category `f` the lenient metrics equal the exact ones. Exact hits are never touched.
- *A text key may be followed by `^`.* The phrase still has to start and end at a word boundary, and a `^` right after it counts as a boundary, so the key `U_k` is found in `U_k^\dagger`.
- *A lone Greek name does not count as an alternative.* A text key's `alts` that normalize to one word which is the name of a Greek letter (`ψ+` becomes `psi`) are ignored (the names the matcher gives to Greek letters, and `varphi`, `varepsilon`, `varpi`, `varrho`, `varsigma`), because they would match that word anywhere in an answer. Every other alternative, short ones included, and the key's own `value` are used. The rule lives in the scorer, not in the set, so a set file keeps its md5 and older runs can still be scored on it.

**Groups.** `all`; then `field=value` for each value of each item field that splits the set (`lang`, `form`, `content`, `split`, `found_by`) and of each tag with 2 to 8 values (`category=a`). `Options(group_by=("lang", "tag:category"))` chooses the fields, `Options(groups=("category=a|b",))` adds a union (any of the values). A tag whose name is not lower-case letters and `_`, or whose value contains `=`, cannot be a `qa-score/1` group name and is left out; among the automatic fields that happens quietly, but a field you name with `group_by` or `--group-by` that gives no group (a typo, a field no question has, a name outside `a-z_`) is a warning, kept in the header notes and printed on stderr.

**The bootstrap.** 2,000 resamples of the questions, seed 1, a 95% interval rounded to 4 decimals. Two generators, because two earlier families of results used different ones:

| Engine | Header `engine` | What it is |
|---|---|---|
| `python_random` (default) | `python_random` | `random.Random(1)` restarted for every group and metric; sorted resample means at positions 50 and 1949 |
| `numpy_pcg64` | `numpy_pcg64` | numpy's `default_rng(1).integers` stream and `numpy.percentile`, written in pure Python (`score/npcompat.py`), so numpy is not a dependency |

numpy gives no stream guarantee between versions; `npcompat` follows numpy 2.x, checked against 2.5.3, and the tests pin values taken from it (and compare many more when numpy is installed).

**Refused.** A run whose set name or md5 is not the set file's, an answer to a question the set does not have, an oracle run with an answer whose contexts are not the gold passages (an error line is not checked), two runs with one `run_id`, a pair that names an unknown run or a run with itself, a pair given twice, a group that cannot be written. A run with questions unanswered, or searched on a corpus the set does not name, is a warning (printed by the command and kept in the header notes).

**Not done.** Folding `reviews/1` grades into the metrics, a bootstrap cluster by paper or mirror pair (only `id`), multiple choice, the circuit grader, and the model call that extracts a short answer for QASPER (the extracted answer is read from `ext.short`).

## The commands

```bash
qa-bench validate FILE... [--set QSET] [--format FORMAT] [--all]
qa-bench convert SHAPE INPUT -o OUTPUT --set-name NAME [--corpus ID] [--created TIME] [--notes TEXT] [--back] [--force]
qa-bench convert ask-capture INPUT -o OUTPUT --header FILE [--retrieval FILE] [--force]
qa-bench score --set QSET --run RUN... -o OUTPUT [--pair BASE:VAR]... [--noise A:B] [--group-by FIELDS]
               [--group LABEL=V1|V2]... [--bootstrap python_random|numpy_pcg64] [--no-group-ci]
               [--number-words] [--created TIME] [--force]
```

`validate` takes the format from line 1 (`--format` requires one), reads gzip by its first bytes, prints `ok <file>: <format>, <n> records, md5 <md5>` for each good file and `<file>:<line>: <pointer>: <message>` for each problem (the first 20 per file, all with `--all`). With `--set` it also checks runs, scores and reviews against the set file. `convert SHAPE` writes a `qset/1` file; `--created` pins the header time (default: now, in UTC) so a conversion can be repeated byte for byte; `--back` reads a `qset/1` file made from `SHAPE` and writes its rows again as a source file. An output file that exists is not overwritten unless `--force` is given. `convert ask-capture` writes a `qa-run/1` file (gzip when the name ends in `.gz`) from capture records under the run header in `--header` (a JSON file), with `--retrieval` giving the retrieval rank records by `id`. The shapes are `mathcirc-v1`, `circuit-tasks-v1`, `circuit-claims-v1`, `qubitqa-pilot` and `qasper`.

`score` reads the set and the runs strictly, refuses what [The scorer](#the-scorer) lists, prints `wrote OUTPUT: qa-score/1, N records, md5 ...` and the warnings (on stderr). `--pair` takes run ids and may be repeated; `--created` pins the header time (default: now, in UTC); an existing output is not overwritten without `--force`. The file name recorded for each run is the file's name without its folder.

Exit status: 0 when the work was done, 1 when a file or a row was refused or a file cannot be read or written (one line says why, never a traceback), 2 for a command line that does not parse.

## Code map

| Path | What |
|---|---|
| `src/qa_bench/formats/` | `schemas/` (the four JSON Schemas), `schema.py` (check one line), `jsonl.py` (strict read, deterministic write, md5, gzip), `files.py` (`FormatFile`, `load`, `read`, `build`, `write`, the rules inside a file), `crosscheck.py` (the rules between files), `problems.py` |
| `src/qa_bench/reviews.py` | `apply_reviews`, `apply_to_set`, `latest_decisions` |
| `src/qa_bench/score/` | `scorer.py` (`score`, `Options`, `ScoreError`), `keys.py` (key-fact metrics), `answers.py` (retrieval, cost, QASPER), `papers.py` (gold papers among the contexts and candidates, keys per paper), `groups.py`, `bootstrap.py` (the two engines), `npcompat.py` (numpy's generator and percentile in pure Python), `mathnorm.py` (the vendored matcher) |
| `src/qa_bench/convert/` | one module per source shape, `captures.py`, and `shapes.py` (the table and the whole-file functions) |
| `src/qa_bench/cli.py` | the `qa-bench` command |
| `tests/` | one module per area; `fixtures/` holds synthetic files only |

Import contracts (`pyproject.toml`): the layers are `cli`, then `convert`, `reviews` and `score` (independent of each other), then `formats`; those four use the standard library and `jsonschema` only (no `httpx`, `numpy`, `docingest`, `docingest_index`, `quantum_sim`); `qa_bench` never imports `research_api`, `docingest` or `quantum_sim`. The other directions are contracts in the `pyproject.toml` of docingest, docingest-index and quantum-sim.

## Tests and checks

```bash
./scripts/check.sh    # ruff, ruff format, import-linter, pyright, pytest with a coverage floor of 85%
```

The fixtures under `tests/fixtures/` are made up (fake doc ids, invented questions and reviewers): no private question set, run or answer is in this repository. The tests cover every fixture line against the schemas, about thirty broken lines that must be refused, the file rules, gzip and md5 identity, reviews, the round trip of every source shape, the capture converter and the command. The scorer is tested on small sets whose numbers are worked out by hand, on a 101-question comparison of two runs (headline counts, intervals from a copy of the old comparison script, a noise floor, every category), on the key rules, the groups, the two bootstrap engines (against the old script and against values taken from numpy), the vendored matcher and the command, including that the bytes do not depend on the interpreter's hash seed. `pytest` skips the tests that need numpy when it is not installed.
