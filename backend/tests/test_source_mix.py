"""Source mix computation (Phase 1d). Unit: citations."""

from app.modules.research.source_mix import compute_report_source_mix, compute_run_source_mix


def _c(cid: str, source: str, **extra) -> dict:
    return {"id": cid, "source": source, "simulated": False, **extra}


def test_counts_only_citations_marked_in_the_answer():
    citations = [
        _c("c1", "asset"),
        _c("c2", "asset"),
        _c("c3", "web"),
        _c("c4", "asset"),  # claim-only evidence, never cited inline
        {"id": "k1", "kind": "claim"},
        _c("c5", "web", simulated=True),
    ]
    answer = "Point one [c1][c2]. Point two [c3] and [c5]."
    assert compute_run_source_mix(answer, citations, "grounded") == {
        "kb": 2,
        "web": 1,
        "general": 0,
        "unit": "citations",
    }


def test_answer_without_markers_counts_every_stored_citation():
    mix = compute_run_source_mix("Extractive findings.", [_c("c1", "asset"), _c("c2", "web")], "grounded")
    assert (mix["kb"], mix["web"]) == (1, 1)


def test_general_knowledge_answer():
    assert compute_run_source_mix("From general knowledge.", [], "unsourced") == {
        "kb": 0,
        "web": 0,
        "general": 1,
        "unit": "citations",
    }


def test_report_counts_section_citations_and_distinct_links():
    sections = [
        {"content": "See https://pytorch.org and https://pytorch.org.", "citations": ["a1", "a2"]},
        {"content": "Also https://huggingface.co/docs)", "citations": ["a1"]},
        {"content": "Not covered by your documents.", "citations": []},
    ]
    assert compute_report_source_mix(sections) == {
        "kb": 3,
        "web": 2,
        "general": 0,
        "unit": "citations",
    }
