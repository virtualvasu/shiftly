import json

from eval.summary import load_run, render_table


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_summary_table_by_category(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    _write(a, [{"id": "x", "pass": False, "notes": "[cutoff] d"}, {"id": "y", "pass": True, "notes": "[isolation] d"}])
    _write(b, [{"id": "x", "pass": True, "notes": "[cutoff] d"}, {"id": "y", "pass": True, "notes": "[isolation] d"}])
    table = render_table([("Run 1", load_run(a)), ("Run 2", load_run(b))])
    assert table.splitlines() == [
        "| Category | Run 1 | Run 2 |",
        "|---|---|---|",
        "| cutoff | 0/1 | 1/1 |",
        "| isolation | 1/1 | 1/1 |",
        "| **Total** | **1/2** | **2/2** |",
    ]


def test_latency_summary_separates_unavailable_replies():
    from eval.latency import summarize

    answered = [(0.5 + i * 0.05, 2, False) for i in range(19)] + [(3.0, 2, False)]
    unavailable = [(0.2, 1, True)] * 3
    s = summarize(answered + unavailable)
    assert s["answered"] == 20 and s["model_unavailable"] == 3
    assert s["p95_s"] == 1.4 and s["max_s"] == 3.0
    assert s["max_llm_calls"] == 2
