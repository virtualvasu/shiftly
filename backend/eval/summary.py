"""Compare evaluation runs by category.

    python -m eval.summary                       # all runs in eval/history, oldest first
    python -m eval.summary a.jsonl b.jsonl       # specific result files

Prints a Markdown table: one row per category, one column per run.
"""

import json
import sys
from pathlib import Path

HISTORY = Path(__file__).with_name("history")


def load_run(path: Path) -> dict[str, tuple[int, int]]:
    """category -> (passed, total)"""
    out: dict[str, list[int]] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        cat = r["notes"].split("]")[0].lstrip("[")
        passed, total = out.setdefault(cat, [0, 0])
        out[cat] = [passed + bool(r["pass"]), total + 1]
    return {k: (v[0], v[1]) for k, v in out.items()}


def render_table(runs: list[tuple[str, dict[str, tuple[int, int]]]]) -> str:
    cats = sorted({c for _, run in runs for c in run})
    lines = ["| Category | " + " | ".join(name for name, _ in runs) + " |",
             "|---|" + "---|" * len(runs)]
    for c in cats:
        cells = [f"{run[c][0]}/{run[c][1]}" if c in run else "–" for _, run in runs]
        lines.append(f"| {c} | " + " | ".join(cells) + " |")
    totals = [f"**{sum(p for p, _ in run.values())}/{sum(t for _, t in run.values())}**" for _, run in runs]
    lines.append("| **Total** | " + " | ".join(totals) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    files = [Path(a) for a in args] or sorted(HISTORY.glob("*.jsonl"))
    print(render_table([(f.stem, load_run(f)) for f in files]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
