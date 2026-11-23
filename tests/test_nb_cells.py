"""Unit tests for skills/google-colab/scripts/nb_cells.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "skills" / "google-colab" / "scripts" / "nb_cells.py"

_spec = importlib.util.spec_from_file_location("nb_cells", SCRIPT_PATH)
assert _spec and _spec.loader
nb_cells = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nb_cells)


def _sample_notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": [
            {
                "id": "md-0",
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Sample Lab\n", "Overview text."],
            },
            {
                "id": "code-1",
                "cell_type": "code",
                "metadata": {"custom": "keep-me"},
                "execution_count": None,
                "outputs": [],
                "source": [
                    "#@title 1.1 Download Model\n",
                    "from huggingface_hub import hf_hub_download\n",
                    "path = hf_hub_download('org/repo', 'weights.gguf')\n",
                ],
            },
            {
                "id": "code-2",
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "source": [
                    "#@title 2.1 Start Server & Proxy\n",
                    "from google.colab import output\n",
                    "url = output.eval_js('google.colab.kernel.proxyPort(8091)')\n",
                    "# comment with drive.mount() should be ignored\n",
                ],
            },
            {
                "id": "code-3",
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "source": [
                    "#@title 3.1 Safe eval_js + Training\n",
                    "url = output.eval_js('google.colab.kernel.proxyPort(8091)', timeout_sec=3)\n",
                    "trainer.train()\n",
                ],
            },
        ],
    }


class TestNbCells(unittest.TestCase):
    def test_scan_detects_blockers_and_long_tasks(self) -> None:
        report = nb_cells.scan_notebook(_sample_notebook())
        self.assertEqual(report["total_cells"], 4)
        self.assertEqual(report["code_cells"], 3)

        # Only cell 2 has eval_js without timeout_sec; cell 3 has timeout_sec=3; comment is ignored.
        blocker_kinds = [(b["cell"], b["kind"]) for b in report["ui_blockers"]]
        self.assertEqual(blocker_kinds, [(2, "eval_js_no_timeout")])

        long_kinds = [(t["cell"], t["kind"]) for t in report["long_tasks"]]
        self.assertIn((1, "large_download"), long_kinds)
        self.assertIn((3, "training_loop"), long_kinds)
        self.assertTrue(report["needs_timeout_confirmation"])
        self.assertEqual(report["recommended_default_timeout"], 180)

    def test_scan_simple_notebook_defaults_to_60s(self) -> None:
        simple = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "id": "c0",
                    "cell_type": "code",
                    "metadata": {},
                    "outputs": [],
                    "source": ["x = 1 + 1\n", "print(x)\n"],
                }
            ],
        }
        report = nb_cells.scan_notebook(simple)
        self.assertEqual(report["ui_blockers"], [])
        self.assertEqual(report["long_tasks"], [])
        self.assertFalse(report["needs_timeout_confirmation"])
        self.assertEqual(report["recommended_default_timeout"], 60)

    def test_extract_and_patch_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            nb_path = tmp_path / "demo.ipynb"
            nb_path.write_text(json.dumps(_sample_notebook(), indent=1), encoding="utf-8")

            cells_dir = tmp_path / "cells"
            res = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "extract", str(nb_path), "--out", str(cells_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Extracted 3 code cell(s)", res.stdout)
            cell2_file = cells_dir / "cell_02.py"
            self.assertTrue(cell2_file.exists())

            # Patch cell 2 to add timeout_sec=3
            updated = cell2_file.read_text(encoding="utf-8").replace(
                "proxyPort(8091)')",
                "proxyPort(8091)', timeout_sec=3)",
            )
            cell2_file.write_text(updated, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "patch",
                    str(nb_path),
                    "--cell",
                    "2",
                    "--from",
                    str(cell2_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            # Verify JSON integrity, metadata preservation, and zero UI blockers remaining
            patched_nb = json.loads(nb_path.read_text(encoding="utf-8"))
            self.assertEqual(patched_nb["cells"][1]["metadata"], {"custom": "keep-me"})
            self.assertEqual(patched_nb["cells"][2]["id"], "code-2")
            report_after = nb_cells.scan_notebook(patched_nb)
            self.assertEqual(report_after["ui_blockers"], [])

    def test_inspect_and_scan_json_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nb_path = Path(tmp) / "demo.ipynb"
            nb_path.write_text(json.dumps(_sample_notebook()), encoding="utf-8")

            ins = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "inspect", str(nb_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("[01] code", ins.stdout)
            self.assertIn("1.1 Download Model", ins.stdout)

            scn = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "scan", "--json", str(nb_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            parsed = json.loads(scn.stdout)
            self.assertTrue(parsed["needs_timeout_confirmation"])


if __name__ == "__main__":
    unittest.main()
