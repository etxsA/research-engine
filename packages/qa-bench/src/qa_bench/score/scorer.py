"""Score runs against a question set: ``qset/1`` and ``qa-run/1`` in, ``qa-score/1`` out.

The result has an ``item`` record per answer (its metrics and one record per key), a ``group``
record per run, group and metric (mean and 95% interval) and, for each pair of runs, a ``compare``
record per group and metric (the paired difference, its interval, how many questions went up and
down, and the noise floor when a pair of repeats of the same arm is given). Nothing here calls a
model; the same inputs give the same bytes.

The questions are always taken in the order of the question-set file, whatever the order of a run
file: the bootstrap resamples positions, so another order would move the last digit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from qa_bench.formats import QA_RUN, QA_SCORE, QSET, FormatFile, build, check_against_set
from qa_bench.score import bootstrap
from qa_bench.score.answers import cost_scores, qasper_scores, retrieval_scores
from qa_bench.score.groups import Group, auto_fields, field_warnings, make_groups
from qa_bench.score.keys import MATHNORM_CODE_MD5, key_scores
from qa_bench.score.papers import keys_per_paper, paper_scores

MAX_SHOWN = 5
LABEL_CHARS = 80


class ScoreError(ValueError):
    """The inputs cannot be scored: a run for another set, a repeated answer, an unknown run."""


@dataclass(frozen=True)
class NamedRun:
    """A ``qa-run/1`` file and the file name the score header records for it."""

    name: str
    file: FormatFile


@dataclass(frozen=True)
class Options:
    """What to compute; by default number words do not count in the main columns.

    ``pairs`` are ``(base_run_id, var_run_id)``; ``noise`` is a pair of two runs of the same arm
    whose differences are the noise floor. ``group_by`` lists set fields or ``tag:NAME`` (None:
    every field that splits the set); ``groups`` adds unions written ``label=v1|v2``.
    ``group_ci=False`` skips the interval of each group.
    """

    pairs: tuple[tuple[str, str], ...] = ()
    noise: tuple[str, str] | None = None
    group_by: tuple[str, ...] | None = None
    groups: tuple[str, ...] = ()
    engine: str = bootstrap.PythonRandom.name
    group_ci: bool = True
    number_words: bool = False


@dataclass(frozen=True)
class Scored:
    """A ``qa-score/1`` file and the warnings found while scoring."""

    file: FormatFile
    warnings: tuple[str, ...] = field(default=())


def code_md5() -> str:
    """The md5 of the scoring code (the ``.py`` files of this package, by name)."""
    digest = hashlib.md5(usedforsecurity=False)
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _package_version() -> str:
    try:
        return version("qa-bench")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "unknown"


Metrics = dict[str, Any]


def score_answers(
    questions: Mapping[str, Mapping[str, Any]],
    answers: Sequence[Mapping[str, Any]],
    *,
    number_words: bool = False,
    arm: str | None = None,
) -> list[tuple[str, int, Metrics, list[dict[str, Any]] | None]]:
    """``(id, repeat, metrics, keys)`` for each answer of one run, in the order of the run.

    ``arm`` is the name of the run's arm; the multi-paper metrics need it to tell a bare run, which
    retrieves nothing, from an arm that retrieved an empty list. If it is left out (None) the run is
    taken as an arm that retrieves, so a bare run scored without it gets 0.0 instead of None;
    ``score`` always passes it.
    """
    out: list[tuple[str, int, Metrics, list[dict[str, Any]] | None]] = []
    for answer in answers:
        question = questions[answer["id"]]
        metrics: Metrics = {}
        keys = None
        if question.get("key_facts"):
            metrics, keys = key_scores(dict(question), dict(answer), number_words=number_words)
            metrics.update(retrieval_scores(dict(answer)))
            metrics.update(keys_per_paper(question, keys))
        metrics.update(paper_scores(question, answer, arm=arm))
        if (question.get("ext") or {}).get("refs"):
            metrics.update(qasper_scores(dict(question), dict(answer)))
        metrics.update(cost_scores(dict(answer)))
        out.append((answer["id"], answer["repeat"], metrics, keys))
    return out


def per_question(
    scored: Sequence[tuple[str, int, Metrics, Any]],
) -> dict[str, dict[str, float | None]]:
    """For each question the mean of each metric over its repeats, so the bootstrap resamples
    questions. A metric the run lacks for a question is None."""
    by_question: dict[str, list[Metrics]] = {}
    for qid, _repeat, metrics, _keys in scored:
        by_question.setdefault(qid, []).append(metrics)
    out: dict[str, dict[str, float | None]] = {}
    for qid, repeats in by_question.items():
        row: dict[str, float | None] = {}
        for name in sorted({k for m in repeats for k in m}):
            values = [float(m[name]) for m in repeats if m.get(name) is not None]
            row[name] = sum(values) / len(values) if values else None
        out[qid] = row
    return out


def _check_pairs(run_ids: Sequence[str], options: Options) -> None:
    named = [*options.pairs, *([options.noise] if options.noise else [])]
    for pair in named:
        for rid in pair:
            if rid not in run_ids:
                raise ScoreError(
                    f"the pair {pair[0]}:{pair[1]} names run {rid!r}, which is not given"
                )
        if pair[0] == pair[1]:
            raise ScoreError(f"the pair {pair[0]}:{pair[1]} compares a run with itself")
    if len(set(options.pairs)) != len(options.pairs):
        raise ScoreError("a pair is given twice")


def _check_run(run: NamedRun, qset: FormatFile) -> list[str]:
    """Refuse a run that does not belong to the set; return its warnings."""
    header = run.file.header
    problems = check_against_set(run.file, qset)
    if problems:
        shown = "; ".join(f"line {p.line}: {p.message}" for p in problems[:MAX_SHOWN])
        more = f" (and {len(problems) - MAX_SHOWN} more)" if len(problems) > MAX_SHOWN else ""
        raise ScoreError(f"run {header['run_id']} does not fit the set: {shown}{more}")
    warnings: list[str] = []
    missing = len(qset.records) - len({a["id"] for a in run.file.records})
    if missing:
        warnings.append(
            f"run {header['run_id']}: {missing} of {len(qset.records)} questions have no answer"
        )
    corpora = {q["corpus"] for q in qset.records}
    if len(corpora) == 1 and header["corpus"]["id"] not in ("none", *corpora):
        warnings.append(
            f"run {header['run_id']} searched corpus {header['corpus']['id']}, "
            f"the set says {next(iter(corpora))}"
        )
    return warnings


def _check_inputs(
    qset: FormatFile, runs: Sequence[NamedRun], options: Options
) -> tuple[list[str], list[str]]:
    """Refuse what cannot be scored; return the run ids and the warnings."""
    if qset.format != QSET:
        raise ScoreError(f"the set must be a {QSET} file, not {qset.format}")
    if not runs:
        raise ScoreError("give at least one run")
    for run in runs:
        if run.file.format != QA_RUN:
            raise ScoreError(f"{run.name}: not a {QA_RUN} file")
    run_ids = [r.file.header["run_id"] for r in runs]
    if len(set(run_ids)) != len(run_ids):
        raise ScoreError("two runs have the same run_id")
    if options.engine not in bootstrap.ENGINES:
        raise ScoreError(f"unknown bootstrap engine {options.engine!r}")
    _check_pairs(run_ids, options)
    warnings = [w for run in runs for w in _check_run(run, qset)]
    return run_ids, warnings


class _Table:
    """Group and compare records over the per-question values, in the order of the set."""

    def __init__(
        self,
        order: Sequence[str],
        questions: Mapping[str, Any],
        groups: Sequence[Group],
        engine: bootstrap.Engine,
        *,
        group_ci: bool,
    ) -> None:
        self.order = order
        self.questions = questions
        self.groups = groups
        self.engine = engine
        self.group_ci = group_ci

    def group_records(
        self, run_id: str, values: Mapping[str, Mapping[str, float | None]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in self.groups:
            ids = [i for i in self.order if i in values and group.contains(self.questions[i])]
            for metric in sorted({m for i in ids for m in values[i]}):
                vals = [v for i in ids if (v := values[i].get(metric)) is not None]
                if not vals:
                    continue
                record: dict[str, Any] = {
                    "record": "group",
                    "run_id": run_id,
                    "group": group.name,
                    "metric": metric,
                    "n": len(vals),
                    "mean": round(self.engine.mean(vals), bootstrap.DIGITS),
                }
                if len(vals) >= 2 and self.group_ci:
                    record["ci"] = list(self.engine.ci(vals))
                out.append(record)
        return out

    def compare_records(
        self,
        base: str,
        var: str,
        base_values: Mapping[str, Mapping[str, float | None]],
        var_values: Mapping[str, Mapping[str, float | None]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in self.groups:
            ids = [
                i
                for i in self.order
                if i in base_values and i in var_values and group.contains(self.questions[i])
            ]
            metrics = {m for i in ids for m in base_values[i]} | {
                m for i in ids for m in var_values[i]
            }
            for metric in sorted(metrics):
                pairs = [
                    (x, y)
                    for i in ids
                    if (x := base_values[i].get(metric)) is not None
                    and (y := var_values[i].get(metric)) is not None
                ]
                if not pairs:
                    continue
                diffs = [y - x for x, y in pairs]
                digits = bootstrap.DIGITS
                out.append(
                    {
                        "record": "compare",
                        "base_run": base,
                        "var_run": var,
                        "group": group.name,
                        "metric": metric,
                        "n": len(pairs),
                        "base": round(self.engine.mean([x for x, _ in pairs]), digits),
                        "var": round(self.engine.mean([y for _, y in pairs]), digits),
                        "diff": round(self.engine.diff_mean(diffs), digits),
                        "ci": list(self.engine.ci(diffs)),
                        "up": sum(1 for d in diffs if d > 0),
                        "down": sum(1 for d in diffs if d < 0),
                    }
                )
        return out


def _notes(engine: bootstrap.Engine, options: Options, warnings: Sequence[str]) -> str:
    number_words = (
        "Number words count in the main columns (flagged word_only)."
        if options.number_words
        else "Number words (two, dos, ...) do not count in the main columns; the columns ending in "
        "_words give them, and num_word_only marks the answers where they matter."
    )
    text = (
        f"mathnorm v2 (md5 of its code {MATHNORM_CODE_MD5}). {number_words} "
        "A lenient hit of category f does not count (flag unchecked). Citation keys "
        "'(<16 hex> pages a-b)' are cut from the text before a number key is matched. A text "
        "key may be followed by '^'. A text alternative that is one Greek letter name is "
        "ignored. "
        f"Bootstrap engine '{engine.name}': {engine.description}. "
        "Groups: 'field=v1|v2' means any of the listed values. Metric names: refused = 'cannot "
        "answer' in the long answer (QASPER); cannot_answer = the cannot-answer pattern for "
        "key-fact sets; psg10, psg50, rr1, rr1_strict come from ext.retrieval of the run lines; "
        "paper_recall10 and paper_recall50 are the share of the gold papers with a chunk that is "
        "not a reference list among the first 10 contexts and the first 50 candidates, and "
        "all_gold_present is 1.0 when every gold paper is among all the contexts, else 0.0 "
        "(null for a bare run, 0.0 for an arm that retrieved no context), "
        "contexts_used is the number of contexts, and keys_per_paper_mean and keys_per_paper_min "
        "give the mean and the weakest share of exact key hits per gold paper; a "
        "metric whose inputs the run lacks is null on the item and left out of groups."
    )
    return " ".join([text, *warnings])


def score(
    qset: FormatFile, runs: Sequence[NamedRun], *, created: str, options: Options | None = None
) -> Scored:
    """Score ``runs`` against ``qset``; see :class:`Options`. Raises :class:`ScoreError`."""
    options = options or Options()
    run_ids, warnings = _check_inputs(qset, runs, options)
    engine = bootstrap.ENGINES[options.engine]()
    questions = {q["id"]: q for q in qset.records}
    order = [q["id"] for q in qset.records]
    try:
        fields = (
            list(options.group_by) if options.group_by is not None else auto_fields(qset.records)
        )
        groups = make_groups(qset.records, fields, options.groups)
        if options.group_by is not None:
            warnings += field_warnings(qset.records, fields)
    except ValueError as error:
        raise ScoreError(str(error)) from None
    table = _Table(order, questions, groups, engine, group_ci=options.group_ci)

    records: list[dict[str, Any]] = []
    values: dict[str, dict[str, dict[str, float | None]]] = {}
    for run in runs:
        run_id = run.file.header["run_id"]
        scored = score_answers(
            questions,
            run.file.records,
            number_words=options.number_words,
            arm=run.file.header["arm"]["name"],
        )
        values[run_id] = per_question(scored)
        for qid, repeat, metrics, keys in scored:
            record: dict[str, Any] = {
                "record": "item",
                "run_id": run_id,
                "id": qid,
                "repeat": repeat,
                "metrics": metrics,
            }
            if keys is not None:
                record["keys"] = keys
            records.append(record)
        records += table.group_records(run_id, values[run_id])

    floor: dict[tuple[str, str], float] = {}
    if options.noise:
        base, var = options.noise
        floor = {
            (r["group"], r["metric"]): abs(r["diff"])
            for r in table.compare_records(base, var, values[base], values[var])
        }
    for base, var in options.pairs:
        for record in table.compare_records(base, var, values[base], values[var]):
            fl = floor.get((record["group"], record["metric"]))
            if options.noise and fl is not None:
                record["floor"] = fl
                record["counts"] = bool(
                    (record["ci"][0] > 0 or record["ci"][1] < 0) and abs(record["diff"]) > fl
                )
            records.append(record)

    header: dict[str, Any] = {
        "format": QA_SCORE,
        "created": created,
        "scorer": {
            "name": f"qa-bench {_package_version()} score",
            "md5": code_md5(),
            "mathnorm": "v2+words" if options.number_words else "v2",
        },
        "set": {"name": qset.header["set"], "md5": qset.md5},
        "runs": [
            {
                "run_id": run_id,
                "file": run.name,
                "md5": run.file.md5,
                "label": (run.file.header["arm"].get("variant") or run.file.header["arm"]["name"])[
                    :LABEL_CHARS
                ],
            }
            for run_id, run in zip(run_ids, runs, strict=True)
        ],
        "bootstrap": {
            "resamples": bootstrap.RESAMPLES,
            "seed": bootstrap.SEED,
            "level": bootstrap.LEVEL,
            "cluster": "id",
            "engine": engine.name,
        },
    }
    if options.noise:
        header["noise_floor"] = {"base_run": options.noise[0], "var_run": options.noise[1]}
    header["notes"] = _notes(engine, options, warnings)
    return Scored(build(QA_SCORE, header, records), tuple(warnings))
