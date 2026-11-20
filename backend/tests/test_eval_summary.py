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
