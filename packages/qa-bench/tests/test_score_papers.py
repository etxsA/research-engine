"""Multi-paper metrics: gold papers among the contexts and candidates, and keys per paper."""

import pytest
from scoredata import DOC_A, DOC_B, answer, make_qset, make_run, num_key, question, text_key

from qa_bench.score import NamedRun, Options, code_md5, score
from qa_bench.score.papers import (
    evidence_papers,
    gold_papers,
    keys_per_paper,
    paper_key,
    paper_scores,
)

DOC_C = "c3" * 32
DOC_D = "d4" * 32
CREATED = "2026-01-05T09:00:00+00:00"
NO_SCORES = {"paper_recall10": None, "paper_recall50": None, "all_gold_present": None}


def ctx(doc_id, *, reference=None):
    """A context or candidate; ``reference`` None leaves ``is_reference`` out."""
    chunk = {"doc_id": doc_id}
    if reference is not None:
        chunk["is_reference"] = reference
    return chunk


def multi(*doc_ids):
    """A multi-paper question whose gold papers are ``doc_ids``."""
    papers = [{"doc_id": d, "passages": [{"md_text": "made up"}]} for d in doc_ids]
    return question("M-1", None, form="multi_compare", gold_papers=papers)


def scores(question_, contexts, candidates=None, arm="harness"):
    item = {"contexts": contexts}
    if candidates is not None:
        item["candidates"] = candidates
    return paper_scores(question_, item, arm=arm)


def retrieval(got):
    """The three retrieval metrics of ``paper_scores``, without ``contexts_used``."""
    return {name: got[name] for name in NO_SCORES}


def test_every_gold_paper_in_the_contexts_scores_one():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A), ctx(DOC_B)])
    assert got["paper_recall10"] == 1.0
    assert got["all_gold_present"] == 1.0


def test_a_gold_paper_missing_from_the_contexts_scores_half_and_zero():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A), ctx(DOC_C)])
    assert got["paper_recall10"] == 0.5
    assert got["all_gold_present"] == 0.0


def test_a_question_without_gold_papers_scores_none():
    unanswerable = {"id": "U-1", "form": "unanswerable", "gold_papers": []}
    assert retrieval(scores(unanswerable, [ctx(DOC_A)], [ctx(DOC_A)])) == NO_SCORES
    assert retrieval(scores(unanswerable, [], arm="bare")) == NO_SCORES


def test_a_bare_answer_scores_none_even_if_it_carried_contexts_and_candidates():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], [ctx(DOC_A)], arm="bare")
    assert retrieval(got) == NO_SCORES
    assert retrieval(scores(multi(DOC_A, DOC_B), [], arm="bare")) == NO_SCORES


@pytest.mark.parametrize("arm", ["harness", "oracle", "agentv1", None])
def test_an_arm_that_retrieves_and_gave_no_contexts_scores_zero(arm):
    # a retrieval failure: the answer counts as 0.0, it is not left out
    got = scores(multi(DOC_A, DOC_B), [], arm=arm)
    assert (got["paper_recall10"], got["all_gold_present"]) == (0.0, 0.0)
    assert got["paper_recall50"] is None  # no candidates kept
    assert got["contexts_used"] == 0
    with_candidates = scores(multi(DOC_A, DOC_B), [], [ctx(DOC_A)], arm=arm)
    assert with_candidates["paper_recall50"] == 0.5
    assert paper_scores(multi(DOC_A, DOC_B), {}, arm=arm)["all_gold_present"] == 0.0


def test_contexts_used_counts_the_contexts_whatever_they_are():
    contexts = [ctx(DOC_A), ctx(DOC_A), ctx(DOC_B, reference=True)]
    assert scores(multi(DOC_A, DOC_B), contexts)["contexts_used"] == 3
    assert scores(multi(DOC_A, DOC_B), contexts, arm="bare")["contexts_used"] == 3
    assert scores({"gold_papers": []}, contexts)["contexts_used"] == 3


def test_contexts_without_the_gold_paper_score_zero_not_none():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_C), ctx(DOC_D)])
    assert got["contexts_used"] == 2
    assert (got["paper_recall10"], got["all_gold_present"]) == (0.0, 0.0)


def test_k_larger_than_the_contexts_uses_the_ones_there_are():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A), ctx(DOC_B), ctx(DOC_C)])
    assert (got["paper_recall10"], got["all_gold_present"]) == (1.0, 1.0)


def test_the_recall_looks_at_ten_contexts_and_all_gold_present_at_all_of_them():
    contexts = [ctx(DOC_B)] + [ctx(DOC_C)] * 9 + [ctx(DOC_A)]  # gold A is the 11th
    got = scores(multi(DOC_A, DOC_B), contexts)
    assert got["paper_recall10"] == 0.5
    assert got["all_gold_present"] == 1.0


@pytest.mark.parametrize("count", [2, 5, 10])
def test_with_ten_contexts_or_fewer_all_gold_present_is_paper_recall10_equal_to_one(count):
    for held in ([DOC_A, DOC_B], [DOC_A], [DOC_C]):
        contexts = [ctx(d) for d in held] + [ctx(DOC_D)] * (count - len(held))
        got = scores(multi(DOC_A, DOC_B), contexts[:count])
        assert got["all_gold_present"] == float(got["paper_recall10"] == 1.0)


def test_several_chunks_of_one_paper_count_once():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A)] * 4)
    assert got["paper_recall10"] == 0.5
    assert got["all_gold_present"] == 0.0


def test_a_reference_chunk_is_not_evidence():
    only_reference = scores(multi(DOC_A, DOC_B), [ctx(DOC_A), ctx(DOC_B, reference=True)])
    assert only_reference["paper_recall10"] == 0.5
    assert only_reference["all_gold_present"] == 0.0
    both = scores(multi(DOC_A, DOC_B), [ctx(DOC_A, reference=False), ctx(DOC_B)])
    assert both["all_gold_present"] == 1.0  # False and a missing is_reference both count


@pytest.mark.parametrize(
    ("gold", "held"),
    [(DOC_A, DOC_A[:16]), (DOC_A[:16], DOC_A), (DOC_A[:16], DOC_A[:16] + "f" * 48)],
)
def test_papers_are_compared_by_their_first_sixteen_hex(gold, held):
    got = scores(multi(gold, DOC_B), [ctx(held), ctx(DOC_B)])
    assert got["all_gold_present"] == 1.0
    assert paper_key(gold) == paper_key(held) == DOC_A[:16]


def test_gold_papers_are_read_once_each_in_set_order():
    assert gold_papers(multi(DOC_B, DOC_A, DOC_B[:16])) == [DOC_B[:16], DOC_A[:16]]
    assert evidence_papers([ctx(DOC_A), ctx(DOC_B, reference=True)]) == {DOC_A[:16]}


def test_paper_recall50_reads_the_first_fifty_candidates():
    pool = [ctx(DOC_D)] * 29 + [ctx(DOC_B)] + [ctx(DOC_D)] * 20 + [ctx(DOC_B)]
    assert len(pool) == 51
    inside = scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], pool[:50])
    assert inside["paper_recall50"] == 0.5  # A is not among the candidates, B is the 30th
    assert scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], [ctx(DOC_A), *pool])["paper_recall50"] == 1.0
    beyond = [ctx(DOC_D)] * 50 + [ctx(DOC_A), ctx(DOC_B)]
    assert scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], beyond)["paper_recall50"] == 0.0


def test_paper_recall50_is_none_without_candidates_and_skips_reference_candidates():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A), ctx(DOC_B)])
    assert got["paper_recall50"] is None
    assert got["paper_recall10"] == 1.0
    candidates = [ctx(DOC_A), ctx(DOC_B, reference=True)]
    assert scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], candidates)["paper_recall50"] == 0.5


def test_an_empty_list_of_candidates_counts_as_no_candidates():
    got = scores(multi(DOC_A, DOC_B), [ctx(DOC_A)], [])
    assert got["paper_recall50"] is None
    assert got["paper_recall10"] == 0.5


def test_an_error_line_without_contexts_scores_zero_on_an_arm_that_retrieves():
    failed = {"id": "M-1", "answer": None, "contexts": [], "error": {"kind": "timeout"}}
    got = paper_scores(multi(DOC_A, DOC_B), failed, arm="harness")
    assert (got["paper_recall10"], got["all_gold_present"]) == (0.0, 0.0)
    assert got["paper_recall50"] is None
    assert retrieval(paper_scores(multi(DOC_A, DOC_B), failed, arm="bare")) == NO_SCORES


def test_a_worked_example_by_hand():
    # Gold papers A, B, C. Contexts, in order: A, D, B (a reference list), A, then six chunks of D,
    # then C as the 11th. Evidence among the first 10: A and D, so paper_recall10 = 1/3 (only A is
    # gold). Among all 11: A, D and C, so B is still missing and all_gold_present = 0.
    # Candidates: D x 59, with A first, C 20th, B as a reference list 30th and B as a body chunk
    # 51st. The first 50 hold A and C (B at 30 is a reference), so paper_recall50 = 2/3.
    contexts = [ctx(DOC_A), ctx(DOC_D), ctx(DOC_B, reference=True), ctx(DOC_A)]
    contexts += [ctx(DOC_D)] * 6 + [ctx(DOC_C)]
    candidates = [ctx(DOC_D)] * 59
    candidates[0], candidates[19] = ctx(DOC_A), ctx(DOC_C)
    candidates[29] = ctx(DOC_B, reference=True)
    candidates[50] = ctx(DOC_B)
    got = scores(multi(DOC_A, DOC_B, DOC_C), contexts, candidates)
    assert got["paper_recall10"] == pytest.approx(1 / 3)
    assert got["all_gold_present"] == 0.0
    assert got["paper_recall50"] == pytest.approx(2 / 3)


def cand(doc_id):
    """A candidate with the fields a ``qa-run/1`` file requires."""
    return {"doc_id": doc_id, "start": 0, "score": 0.5}


def row(i, exact, paper=None):
    out = {"i": i, "exact": exact, "lenient": exact}
    if paper:
        out["paper"] = paper
    return out


def test_keys_per_paper_gives_the_mean_and_the_weakest_paper():
    # A has 2 keys and 1 hit (0.5); B has 1 key and 1 hit (1.0): mean 0.75, min 0.5
    rows = [row(0, True, DOC_A), row(1, False, DOC_A), row(2, True, DOC_B)]
    assert keys_per_paper(multi(DOC_A, DOC_B), rows) == {
        "keys_per_paper_mean": 0.75,
        "keys_per_paper_min": 0.5,
    }


def test_keys_without_a_paper_or_of_a_paper_that_is_not_gold_are_ignored():
    rows = [row(0, True, DOC_A), row(1, False), row(2, False, DOC_C), row(3, False, DOC_D)]
    got = keys_per_paper(multi(DOC_A, DOC_B), rows)
    assert got == {"keys_per_paper_mean": 1.0, "keys_per_paper_min": 1.0}


def test_a_gold_paper_without_keys_is_left_out_of_the_mean_and_the_minimum():
    got = keys_per_paper(multi(DOC_A, DOC_B), [row(0, False, DOC_A)])
    assert got == {"keys_per_paper_mean": 0.0, "keys_per_paper_min": 0.0}


@pytest.mark.parametrize(
    "rows",
    [[], [row(0, True), row(1, True)], [row(0, True, DOC_C)]],
    ids=["none", "no-paper", "not-gold"],
)
def test_no_key_with_a_gold_paper_gives_none(rows):
    assert keys_per_paper(multi(DOC_A, DOC_B), rows) == {
        "keys_per_paper_mean": None,
        "keys_per_paper_min": None,
    }


def test_a_key_paper_may_be_a_sixteen_hex_prefix():
    got = keys_per_paper(multi(DOC_A, DOC_B), [row(0, True, DOC_A[:16]), row(1, True, DOC_B)])
    assert got["keys_per_paper_min"] == 1.0


def scored(qset, runs):
    named = [NamedRun(f"{r.header['run_id']}.qa-run.jsonl", r) for r in runs]
    return score(qset, named, created=CREATED, options=Options())


def multi_set():
    papers = [{"doc_id": d, "passages": [{"md_text": "made up"}]} for d in (DOC_A, DOC_B)]
    multi_keys = [
        text_key("alpha", paper=DOC_A),
        text_key("beta", paper=DOC_B),
        text_key("gamma", paper=DOC_B),
    ]
    synthesis = {"kind": "order", "statement": "A comes before B.", "rubric": "right: both."}
    return make_qset(
        [
            question("S-1", [text_key("alpha")]),
            question(
                "M-1",
                multi_keys,
                form="multi_compare",
                gold_papers=papers,
                synthesis=synthesis,
                necessity={DOC_A: [0], DOC_B: [1, 2]},
            ),
        ]
    )


def records_of(result, kind, **match):
    return [
        r
        for r in result.file.records
        if r["record"] == kind and all(r.get(k) == v for k, v in match.items())
    ]


def test_the_scorer_writes_the_multi_paper_metrics_and_groups_them():
    qset = multi_set()
    first = [
        answer("S-1", "alpha", contexts=[ctx(DOC_A)]),
        # A has a body chunk, B only a reference list; alpha is A's key, beta and gamma are B's
        answer(
            "M-1",
            "only alpha is here",
            contexts=[ctx(DOC_A), ctx(DOC_B, reference=True)],
            candidates=[
                {"doc_id": DOC_A, "start": 0, "score": 0.9},
                {"doc_id": DOC_B, "start": 0, "score": 0.5, "is_reference": False},
            ],
        ),
    ]
    result = scored(qset, [make_run("r1", qset, first)])
    single = records_of(result, "item", id="S-1")[0]["metrics"]
    assert single["all_gold_present"] == 1.0
    assert single["paper_recall10"] == 1.0
    assert single["paper_recall50"] is None  # no candidates kept
    assert single["keys_per_paper_mean"] is None  # its key has no paper
    both = records_of(result, "item", id="M-1")[0]["metrics"]
    assert both["all_gold_present"] == 0.0
    assert both["paper_recall10"] == 0.5
    assert both["paper_recall50"] == 1.0
    assert both["keys_per_paper_mean"] == 0.5  # A: 1 of 1, B: 0 of 2
    assert both["keys_per_paper_min"] == 0.0
    everyone = records_of(result, "group", group="all", metric="all_gold_present")[0]
    assert (everyone["n"], everyone["mean"]) == (2, 0.5)
    only_multi = records_of(result, "group", group="all", metric="keys_per_paper_min")[0]
    assert (only_multi["n"], only_multi["mean"]) == (1, 0.0)
    assert result.file.header["scorer"]["md5"] == code_md5()
    for name in ("paper_recall10", "paper_recall50", "all_gold_present", "keys_per_paper_mean"):
        assert name in result.file.header["notes"]


def test_keys_per_paper_follow_the_number_words_option():
    papers = [{"doc_id": d, "passages": [{"md_text": "made up"}]} for d in (DOC_A, DOC_B)]
    keys = [{**num_key(8), "paper": DOC_A}, text_key("beta", paper=DOC_B)]
    synthesis = {"kind": "order", "statement": "A comes before B.", "rubric": "right: both."}
    qset = make_qset(
        [
            question(
                "M-1",
                keys,
                form="multi_compare",
                gold_papers=papers,
                synthesis=synthesis,
                necessity={DOC_A: [0], DOC_B: [1]},
            )
        ]
    )
    run = make_run("r1", qset, [answer("M-1", "eight gates and beta", contexts=[ctx(DOC_A)])])
    named = [NamedRun("r1.qa-run.jsonl", run)]
    for words, expected in ((False, (0.5, 0.0)), (True, (1.0, 1.0))):
        result = score(qset, named, created=CREATED, options=Options(number_words=words))
        metrics = records_of(result, "item", id="M-1")[0]["metrics"]
        assert (metrics["keys_per_paper_mean"], metrics["keys_per_paper_min"]) == expected


def test_a_bare_run_has_no_multi_paper_metrics_and_stays_out_of_the_groups():
    qset = multi_set()
    bare = make_run("b1", qset, [answer("S-1", "alpha"), answer("M-1", "alpha")], arm="bare")
    result = scored(qset, [bare])
    for item in records_of(result, "item"):
        assert item["metrics"]["all_gold_present"] is None
        assert item["metrics"]["paper_recall10"] is None
        assert item["metrics"]["paper_recall50"] is None
        assert item["metrics"]["contexts_used"] == 0
    assert not records_of(result, "group", metric="all_gold_present")
    assert not records_of(result, "group", metric="paper_recall10")
    assert records_of(result, "group", group="all", metric="key_share")  # the keys still score
    assert records_of(result, "group", group="all", metric="contexts_used")[0]["mean"] == 0.0


def test_a_harness_run_with_an_empty_list_of_contexts_scores_zero():
    qset = multi_set()
    empty = make_run("h1", qset, [answer("S-1", "alpha"), answer("M-1", "alpha")])
    result = scored(qset, [empty])
    for item in records_of(result, "item"):
        assert item["metrics"]["all_gold_present"] == 0.0
        assert item["metrics"]["paper_recall10"] == 0.0
        assert item["metrics"]["paper_recall50"] is None  # no candidates kept
        assert item["metrics"]["contexts_used"] == 0
    group = records_of(result, "group", group="all", metric="all_gold_present")[0]
    assert (group["n"], group["mean"]) == (2, 0.0)


def test_the_oracle_arm_scores_one_because_its_contexts_are_the_gold_passages():
    qset = multi_set()
    gold = [
        answer("S-1", "alpha", contexts=[{"doc_id": DOC_A, "text": "Passage of S-1."}]),
        answer(
            "M-1",
            "alpha",
            contexts=[{"doc_id": DOC_A, "text": "made up"}, {"doc_id": DOC_B, "text": "made up"}],
        ),
    ]
    result = scored(qset, [make_run("o1", qset, gold, arm="oracle")])
    for item in records_of(result, "item"):
        assert item["metrics"]["all_gold_present"] == 1.0
        assert item["metrics"]["paper_recall10"] == 1.0
    assert records_of(result, "item", id="M-1")[0]["metrics"]["contexts_used"] == 2


def test_a_paired_comparison_uses_only_the_ids_with_a_value_in_both_runs():
    qset = multi_set()
    bare = make_run("b1", qset, [answer("S-1", "alpha"), answer("M-1", "alpha")], arm="bare")
    first = [
        answer("S-1", "alpha", contexts=[ctx(DOC_A)], candidates=[cand(DOC_A)]),
        answer("M-1", "alpha", contexts=[ctx(DOC_A)], candidates=[cand(DOC_A), cand(DOC_B)]),
    ]
    second = [
        first[0],
        answer("M-1", "alpha", contexts=[ctx(DOC_A), ctx(DOC_B)]),  # no candidates kept
    ]
    harness = make_run("h1", qset, first)
    other = make_run("h2", qset, second)
    # bare has no value anywhere, so there is nothing to compare with it
    against_bare = score(
        qset,
        [NamedRun("b1.qa-run.jsonl", bare), NamedRun("h1.qa-run.jsonl", harness)],
        created=CREATED,
        options=Options(pairs=(("b1", "h1"),)),
    )
    assert not records_of(against_bare, "compare", metric="all_gold_present")
    # the group mean of the harness run keeps its own n
    assert records_of(against_bare, "group", run_id="h1", metric="all_gold_present")[0]["n"] == 2
    paired = score(
        qset,
        [NamedRun("h1.qa-run.jsonl", harness), NamedRun("h2.qa-run.jsonl", other)],
        created=CREATED,
        options=Options(pairs=(("h1", "h2"),)),
    )
    both_have = records_of(paired, "compare", group="all", metric="all_gold_present")[0]
    assert (both_have["n"], both_have["base"], both_have["var"]) == (2, 0.5, 1.0)
    assert (both_have["diff"], both_have["up"], both_have["down"]) == (0.5, 1, 0)
    # M-1 has paper_recall50 in the first run only, so only S-1 is compared (n = 1)
    one_has = records_of(paired, "compare", group="all", metric="paper_recall50")[0]
    assert (one_has["n"], one_has["base"], one_has["var"], one_has["diff"]) == (1, 1.0, 1.0, 0.0)
    # each run's group mean keeps the ids it has: n = 2 for the first run, n = 1 for the second
    assert records_of(paired, "group", run_id="h1", metric="paper_recall50")[0]["n"] == 2
    assert records_of(paired, "group", run_id="h2", metric="paper_recall50")[0]["n"] == 1


def test_repeats_are_averaged_before_the_multi_paper_groups():
    qset = multi_set()
    answers = [
        answer("S-1", "alpha", contexts=[ctx(DOC_A)]),
        answer("M-1", "alpha", contexts=[ctx(DOC_A), ctx(DOC_B)]),
        answer("M-1", "alpha", repeat=2, contexts=[ctx(DOC_A), ctx(DOC_C)]),
    ]
    result = scored(qset, [make_run("r1", qset, answers)])
    group = records_of(result, "group", group="all", metric="all_gold_present")[0]
    assert (group["n"], group["mean"]) == (2, 0.75)  # S-1 is 1.0; M-1 is the mean of 1.0 and 0.0
