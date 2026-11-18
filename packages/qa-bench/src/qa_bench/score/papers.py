"""Metrics of multi-paper questions: which gold papers the run retrieved, and how many of each
paper's keys the answer holds.

Paper identity is the first 16 hex characters of a doc id, so a 64-hex id and its 16-hex prefix are
the same paper. A context or candidate with ``is_reference`` true is a reference list, never
evidence; a missing ``is_reference`` counts as not a reference.

``paper_recall10`` is the share of the gold papers with a chunk among the first 10 contexts, in the
order the run gave them, and ``paper_recall50`` the same over the first 50 ``candidates``.
``all_gold_present`` is 1.0 when every gold paper has a chunk among ALL the contexts the run gave
and 0.0 when one has none; with 10 contexts or fewer it equals ``paper_recall10 == 1``, with more
(for example 15 or 20) it does not. ``contexts_used`` is the number of contexts of the answer.

The three retrieval metrics are None for a question without gold papers and for a ``bare`` run,
which retrieves nothing. An arm that does retrieve and gave an empty list of contexts scores 0.0 (a
retrieval failure), and so does an answer whose contexts hold no gold paper. An answer line with an
``error`` (a timeout, say) usually has no contexts, so on an arm that retrieves it also scores 0.0
and lowers the recall means; read the number of errors next to them. The ``oracle`` arm scores 1.0
when its contexts carry the gold doc ids, there are at most 10 gold passages and the line is not an
error line. ``paper_recall50`` is None when the answer has no ``candidates``, and an empty list of
``candidates`` counts as none.

``keys_per_paper_mean`` and ``keys_per_paper_min`` take, for each gold paper that has at least one
key assigned to it (``key_facts[].paper``), the share of those keys the answer holds exactly
(every key type, as :func:`qa_bench.score.keys.key_scores` marks them, so they follow
``number_words`` like ``key_share`` and have no ``_words`` variant); then the mean over those
papers and the weakest one. A key whose paper is not a gold paper of the question is ignored, and
both are None when no key has a gold paper.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PAPER_ID_HEX = 16
RECALL_CONTEXTS = 10
RECALL_CANDIDATES = 50
BARE_ARM = "bare"

NO_KEY_SCORES: dict[str, float | None] = {"keys_per_paper_mean": None, "keys_per_paper_min": None}


def paper_key(doc_id: str) -> str:
    """The identity of a paper: the first 16 hex characters of its doc id."""
    return doc_id[:PAPER_ID_HEX]


def gold_papers(question: Mapping[str, Any]) -> list[str]:
    """The paper keys of the gold papers of a question, each once, in the order of the set."""
    out: list[str] = []
    for paper in question.get("gold_papers") or []:
        key = paper_key(paper["doc_id"])
        if key not in out:
            out.append(key)
    return out


def evidence_papers(chunks: Sequence[Mapping[str, Any]]) -> set[str]:
    """The papers with at least one chunk that is not a reference list."""
    return {paper_key(c["doc_id"]) for c in chunks if not c.get("is_reference")}


def paper_scores(
    question: Mapping[str, Any], item: Mapping[str, Any], *, arm: str | None = None
) -> dict[str, float | None]:
    """``contexts_used`` and the three retrieval metrics of one answer.

    ``arm`` is the arm of the run (``bare``, ``harness``, ``oracle``, ...); only ``bare`` makes the
    retrieval metrics None for lack of contexts, any other arm (or None) retrieves.
    """
    contexts = item.get("contexts") or []
    out: dict[str, float | None] = {
        "contexts_used": len(contexts),
        "paper_recall10": None,
        "paper_recall50": None,
        "all_gold_present": None,
    }
    gold = gold_papers(question)
    if not gold or arm == BARE_ARM:
        return out
    candidates = item.get("candidates") or []
    top = evidence_papers(contexts[:RECALL_CONTEXTS])
    every = evidence_papers(contexts)
    pool = evidence_papers(candidates[:RECALL_CANDIDATES])
    out["paper_recall10"] = sum(g in top for g in gold) / len(gold)
    out["all_gold_present"] = float(all(g in every for g in gold))
    if candidates:
        out["paper_recall50"] = sum(g in pool for g in gold) / len(gold)
    return out


def keys_per_paper(
    question: Mapping[str, Any], key_rows: Sequence[Mapping[str, Any]]
) -> dict[str, float | None]:
    """The two keys-per-paper metrics, from the key records that ``key_scores`` gives."""
    gold = set(gold_papers(question))
    found: dict[str, list[bool]] = {}
    for row in key_rows:
        paper = row.get("paper")
        if paper and paper_key(paper) in gold:
            found.setdefault(paper_key(paper), []).append(bool(row["exact"]))
    if not found:
        return dict(NO_KEY_SCORES)
    shares = [sum(hits) / len(hits) for hits in found.values()]
    return {"keys_per_paper_mean": sum(shares) / len(shares), "keys_per_paper_min": min(shares)}
