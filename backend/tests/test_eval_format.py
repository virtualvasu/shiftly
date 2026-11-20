import json

from eval.run import CASES, run_all, write_results

FIELDS = {"id", "input", "expected", "actual", "pass", "notes"}


def test_cases_file_is_well_formed():
    cases = [json.loads(line) for line in CASES.read_text().splitlines()]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 30
    categories = {c["category"] for c in cases}
    assert {"isolation", "cutoff", "approval", "tool_selection", "failure", "clarify", "capacity", "safety"} <= categories


async def test_results_lines_have_exact_fields(tmp_path):
    only = {"cutoff-acme-reject", "iso-body-identity", "appr-restart-approve"}
    results = await run_all("fake", only, 0)
    out = tmp_path / "results.jsonl"
    write_results(results, out)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert {r["id"] for r in lines} == only
    for r in lines:
        assert set(r) == FIELDS
        assert r["pass"] is True, r["notes"]
    restart = next(r for r in lines if r["id"] == "appr-restart-approve")
    assert "<restart service>" in restart["input"]


def test_percentile_nearest_rank():
    from eval.latency import percentile

    values = [float(i) for i in range(1, 21)]  # 1..20
    assert percentile(values, 95) == 19.0
    assert percentile(values, 50) == 10.0
    assert percentile([3.0], 95) == 3.0
