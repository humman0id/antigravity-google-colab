#!/usr/bin/env python3
"""Zero-dependency helper for inspecting, scanning, extracting, and patching .ipynb cells.

Designed for headless Google Colab autonomous debugging workflows with `google-colab-cli`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Patterns that expect an interactive Colab browser frontend and can hang/fail headlessly.
UI_BLOCKER_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "eval_js_no_timeout",
        re.compile(r"\beval_js\s*\((?!.*\btimeout_sec\s*=)"),
        "output.eval_js(...) without timeout_sec blocks forever when headless; add timeout_sec=3.",
    ),
    (
        "drive_mount",
        re.compile(r"\bdrive\.mount\s*\("),
        "google.colab.drive.mount() requires interactive browser OAuth; upload inputs via `colab upload` or skip if Drive cache is disabled.",
    ),
    (
        "files_upload",
        re.compile(r"\bfiles\.upload\s*\("),
        "google.colab.files.upload() blocks waiting for browser file picker.",
    ),
    (
        "userdata_get",
        re.compile(r"\buserdata\.get\s*\("),
        "google.colab.userdata.get() only works in the Colab web UI; pass secrets via `--env KEY=VALUE` or environment variables.",
    ),
    (
        "builtin_input",
        re.compile(r"(?<![.\w])input\s*\("),
        "input() blocks waiting for interactive stdin.",
    ),
]

# Patterns indicating cells that may run longer than standard 60s silence timeouts.
LONG_TASK_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "training_loop",
        re.compile(
            r"\b(trainer\.train\s*\(|\.fit\s*\(|for\s+\w*epoch\w*\s+in\b|Accelerator\s*\(|SFTTrainer\s*\()",
            re.IGNORECASE,
        ),
        "Training/fine-tuning loop detected — confirm timeout budget with the user beforehand.",
    ),
    (
        "large_download",
        re.compile(
            r"\b(snapshot_download|hf_hub_download|from_pretrained|wget\s+|curl\s+.*-o)\b"
        ),
        "Model or dataset download detected — use at least --timeout 180 (or ask user for very large downloads).",
    ),
    (
        "compile_or_build",
        re.compile(r"\b(torch\.compile|cmake\b|ninja\b|make\s+-j|cargo\s+build)\b"),
        "Compilation/build step detected — may stay silent >60s; use --timeout 180+ or confirm with user.",
    ),
    (
        "benchmark_or_eval",
        re.compile(r"\b(benchmark|evaluate\s*\(|lm_eval|eval_harness)\b", re.IGNORECASE),
        "Benchmark/evaluation workload detected — confirm timeout budget with the user beforehand.",
    ),
]

TITLE_RE = re.compile(r"^\s*#\s*@title\s+(.*)")


def _cell_source(cell: dict[str, Any]) -> str:
    src = cell.get("source", "")
    if isinstance(src, list):
        return "".join(src)
    return str(src)


def _cell_title(source: str, cell_type: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = TITLE_RE.match(line)
        if m:
            return m.group(1).strip()
        if cell_type == "markdown":
            return stripped.lstrip("#").strip()[:70]
        return stripped[:70]
    return "(empty)"


def _load_nb(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "cells" not in data:
        raise ValueError(f"{path} is not a valid Jupyter notebook JSON file.")
    return data


def cmd_inspect(args: argparse.Namespace) -> int:
    nb = _load_nb(Path(args.notebook))
    cells = nb["cells"]
    print(f"Notebook: {args.notebook} ({len(cells)} cells)")
    for idx, cell in enumerate(cells):
        ctype = cell.get("cell_type", "unknown")
        src = _cell_source(cell)
        lines = len(src.splitlines())
        title = _cell_title(src, ctype)
        print(f"  [{idx:02d}] {ctype:<8} ({lines:>3} lines)  {title}")
    return 0


def scan_notebook(nb: dict[str, Any]) -> dict[str, Any]:
    ui_blockers: list[dict[str, Any]] = []
    long_tasks: list[dict[str, Any]] = []
    code_cell_count = 0

    for idx, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        code_cell_count += 1
        src = _cell_source(cell)
        title = _cell_title(src, "code")

        for line_no, line in enumerate(src.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for kind, regex, advice in UI_BLOCKER_PATTERNS:
                if regex.search(line):
                    ui_blockers.append(
                        {
                            "cell": idx,
                            "line": line_no,
                            "title": title,
                            "kind": kind,
                            "snippet": stripped[:100],
                            "advice": advice,
                        }
                    )

        matched_long: set[str] = set()
        for kind, regex, advice in LONG_TASK_PATTERNS:
            if kind in matched_long:
                continue
            if regex.search(src):
                matched_long.add(kind)
                long_tasks.append(
                    {
                        "cell": idx,
                        "title": title,
                        "kind": kind,
                        "advice": advice,
                    }
                )

    needs_timeout_confirmation = any(
        t["kind"] in {"training_loop", "benchmark_or_eval"} for t in long_tasks
    )
    recommended_default_timeout = 180 if long_tasks else 60

    return {
        "total_cells": len(nb["cells"]),
        "code_cells": code_cell_count,
        "ui_blockers": ui_blockers,
        "long_tasks": long_tasks,
        "needs_timeout_confirmation": needs_timeout_confirmation,
        "recommended_default_timeout": recommended_default_timeout,
    }


def cmd_scan(args: argparse.Namespace) -> int:
    nb = _load_nb(Path(args.notebook))
    report = scan_notebook(nb)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(
        f"Scanned {args.notebook}: {report['total_cells']} cells "
        f"({report['code_cells']} code)"
    )
    print(
        f"Recommended default --timeout: {report['recommended_default_timeout']}s | "
        f"Ask user about long-job timeout: {'YES' if report['needs_timeout_confirmation'] else 'no'}"
    )
    if report["ui_blockers"]:
        print("\nUI Blockers (may hang or fail headlessly):")
        for b in report["ui_blockers"]:
            print(
                f"  - Cell {b['cell']:02d} L{b['line']} [{b['kind']}]: {b['snippet']}\n"
                f"    -> {b['advice']}"
            )
    else:
        print("\nUI Blockers: none detected.")

    if report["long_tasks"]:
        print("\nLong-Running Workloads Detected:")
        for t in report["long_tasks"]:
            print(
                f"  - Cell {t['cell']:02d} [{t['kind']}] ({t['title']}): {t['advice']}"
            )
    else:
        print("Long-Running Workloads: none detected.")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    nb = _load_nb(Path(args.notebook))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for idx, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") != "code":
            continue
        src = _cell_source(cell)
        out_path = out_dir / f"cell_{idx:02d}.py"
        out_path.write_text(src, encoding="utf-8")
        title = _cell_title(src, "code")
        print(f"Wrote {out_path} ({title})")
        extracted += 1
    print(f"Extracted {extracted} code cell(s) to {out_dir}")
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    nb_path = Path(args.notebook)
    nb = _load_nb(nb_path)
    idx = args.cell
    if idx < 0 or idx >= len(nb["cells"]):
        raise IndexError(f"Cell index {idx} out of range (0..{len(nb['cells']) - 1}).")
    if nb["cells"][idx].get("cell_type") != "code":
        raise ValueError(f"Cell {idx} is '{nb['cells'][idx].get('cell_type')}', not a code cell.")

    new_src = Path(args.source_file).read_text(encoding="utf-8")
    nb["cells"][idx]["source"] = new_src.splitlines(keepends=True)

    # Atomic write via temp file in same directory to avoid corrupting the notebook on interruption.
    tmp_path = nb_path.with_suffix(nb_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(nb, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, nb_path)
    print(f"Patched cell {idx:02d} in {nb_path} from {args.source_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, scan, extract, and patch Jupyter notebook (.ipynb) cells."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="List all cells with index, type, and title.")
    p_inspect.add_argument("notebook", help="Path to .ipynb file")
    p_inspect.set_defaults(func=cmd_inspect)

    p_scan = sub.add_parser(
        "scan",
        help="Scan notebook for Colab-UI blockers and long-running cells before execution.",
    )
    p_scan.add_argument("notebook", help="Path to .ipynb file")
    p_scan.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_scan.set_defaults(func=cmd_scan)

    p_extract = sub.add_parser("extract", help="Extract code cells to cell_XX.py files.")
    p_extract.add_argument("notebook", help="Path to .ipynb file")
    p_extract.add_argument("--out", required=True, help="Output directory for extracted .py files")
    p_extract.set_defaults(func=cmd_extract)

    p_patch = sub.add_parser("patch", help="Patch a single code cell in-place from a .py file.")
    p_patch.add_argument("notebook", help="Path to .ipynb file")
    p_patch.add_argument("--cell", type=int, required=True, help="0-based cell index")
    p_patch.add_argument("--from", dest="source_file", required=True, help="Path to replacement .py file")
    p_patch.set_defaults(func=cmd_patch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
