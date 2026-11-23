---
name: google-colab
description: >-
  Run, inspect, and autonomously debug Google Colab notebooks and Python scripts
  headlessly using the official `colab` CLI (`google-colab-cli`), with optional
  Google Drive (`gdrive`) sync for zero permanent local files, smart automatic
  timeouts, and strict GPU cleanup. Use when asked to run or debug Colab
  notebooks, fix failing cells without manual browser refreshes, or manage
  Colab CPU/GPU/TPU runtimes.
---

# Google Colab Autonomous Runner & Debugger

Operate Google Colab headlessly end-to-end via the official `colab` CLI
(`google-colab-cli`, macOS & Linux) — plus optional `gdrive` integration — so
notebooks and scripts can be inspected, executed, debugged, patched, verified,
and cleaned up reliably without manual browser refreshes.

## Prerequisites Check (First Run)

Before running any Colab command, verify `colab` is available:
```bash
command -v colab
```
- If missing, instruct the user (or install with permission):
  ```bash
  uv tool install google-colab-cli
  # or: pip install google-colab-cli
  ```
- Note: `google-colab-cli` supports **macOS and Linux** (not native Windows).
- If the user refers to a Google Drive notebook by name/URL, check whether the
  `gdrive` CLI/plugin is installed (`command -v gdrive`).
  - **If `gdrive` is available**: pull the notebook into a private temporary
    directory (`mktemp -d`), debug it there, push fixes back to the **same Drive
    file ID** (`gdrive mutate update`), and delete the temp directory when done
    (zero permanent local files).
  - **If `gdrive` is not installed**: operate directly on a local `.ipynb`/`.py`
    file in the workspace, or let the user know they can install the official
    Google Drive plugin from the Antigravity Marketplace for zero-local-file
    Drive sync.

---

## Step 1: Pre-Flight Inspection & Single-Turn User Alignment (UX Rule)

**Always inspect the notebook before allocating a billable GPU VM.**

1. If the notebook is on Google Drive (and `gdrive` is available), locate and
   pull it into a private temporary directory (`mktemp -d`):
   ```bash
   gdrive readonly search --name-contains "<notebook name>"
   WORKDIR=$(mktemp -d -t colab_work.XXXXXX)
   gdrive readonly download <FILE_ID> --out "$WORKDIR/<name>.ipynb"
   ```
2. Run the bundled static scanner ([nb_cells.py](./scripts/nb_cells.py)) before
   starting a VM:
   ```bash
   python3 <path/to/this/skill>/scripts/nb_cells.py scan "$WORKDIR/<name>.ipynb"
   ```
   This detects:
   - **Colab-UI blockers** (`output.eval_js` missing `timeout_sec`, `drive.mount`,
     `files.upload`, `userdata.get`, `input()`).
   - **Long-running cells** (training/fine-tuning loops, large model/dataset
     downloads, compilation, or benchmarks).
3. **Ask once before provisioning** (skip if the user already specified both):
   - **Runtime**: If the user did not specify hardware, ask which runtime to use
     (`CPU`, `T4`, `L4`, `G4`, `A100`, `H100`, `TPU v5e1`/`v6e1`, and whether
     `--high-mem` is needed). Note: Colab `A100`/`H100` VMs already include high
     host RAM (~83 GiB) by default — recommend `--gpu A100` **without**
     `--high-mem` first even if notebook metadata contains `"machine_shape": "hm"`.
   - **Timeout budget for long tasks**: If `nb_cells.py scan` flags
     `needs_timeout_confirmation: true` (training loops, fine-tuning,
     benchmarks) or the user's task is expected to run quietly for several
     minutes, **explicitly ask the user beforehand** what per-cell timeout
     budget they want (e.g., `10m`, `30m`, `60m`, or custom). Never silently
     guess on long jobs!
   - Keep this to **one concise message** combining runtime + timeout so the
     user isn't interrupted repeatedly.

---

## Step 2: Automatic Timeout Policy & Hang Recovery

`colab exec` defaults to a 30-second silence timeout (`--timeout 30`). Use
cell-appropriate automatic timeouts to catch deadlocks fast without killing
legitimate work:

| Cell Category | Automatic `--timeout` | Notes |
| :--- | :--- | :--- |
| Standard code / unit / smoke-test cells | `--timeout 60` | Catches deadlocks quickly without false positives on imports. |
| Package installs, model/dataset downloads, server startup | `--timeout 180` | Prevents false timeouts during quiet download/load phases. |
| Training loops, fine-tuning, heavy benchmarks, builds | **User-confirmed** | Always ask the user beforehand (e.g., `600`–`3600`s). |

### Deadlock Prevention & Recovery
- **`output.eval_js(...)` deadlock**: Calling `google.colab.output.eval_js(...)`
  without `timeout_sec` defaults to `timeout_sec=None` and **blocks the IPython
  kernel forever** when no Colab browser frontend is attached — causing every
  subsequent `colab exec` to queue and hang indefinitely. Always patch it to
  `output.eval_js(..., timeout_sec=3)` (inside `try/except`) before running.
- **`huggingface_hub>=1.x` 10s secret-vault stall**: Passing `token=None` to
  `hf_hub_download` / `snapshot_download` triggers Colab's headless
  `userdata.get("HF_TOKEN")` lookup (~10s timeout + warning per run, even when
  files are cached). On public repos with no explicit token or env var, pass
  `token=False` instead of `None`.
- **If a cell hits its timeout**:
  1. Check session state and recent events:
     ```bash
     colab status -s <name>
     colab log -s <name> -n 20
     ```
  2. Probe responsiveness with a quick `echo 'print("OK")' | colab exec -s <name> --timeout 10`.
  3. If the probe times out, the kernel is wedged — immediately recover with:
     ```bash
     colab restart-kernel -s <name>
     ```
     This resets Python state in seconds **without dropping the VM or losing
     downloaded files in `/content`**. Fix the blocking cell before re-running.

---

## Step 3: Consistent Colab Session Lifecycle

### Mental Model
- **A session = a persistent remote Jupyter kernel on a rented VM.** Imports,
  variables, installed packages, and `/content` files survive across separate
  `colab exec` calls until `colab restart-kernel` or `colab stop`.
- **Default working directory** on the VM is `/content`.

### Execution Loop

1. **Check auth & existing sessions first (never create duplicate GPUs):**
   ```bash
   colab sessions
   ```
   - If auth fails (401/403), run `colab whoami` to inspect identity/scopes.
   - Default auth is `--auth=oauth2` (one-time browser OAuth cached in OS
     keyring); for headless ADC auth, pass `--auth=adc` before the subcommand.
   - **Never** run `colab auth` to fix CLI 401/403 errors — `colab auth` only
     injects VM-side GCP credentials for in-notebook BigQuery/GCS calls.

2. **Provision (or reuse) a named session:**
   ```bash
   colab new -s debug              # CPU
   colab new -s debug --gpu A100   # GPU: T4, L4, G4, A100, H100 (optional --high-mem)
   colab new -s debug --tpu v6e1   # TPU: v5e1, v6e1
   ```
   - Always pass `-s <name>` so every command targets the session unambiguously.
   - Do not pass `--high-mem` (`shape=hm`) on `A100`/`H100` unless specifically
     needed — Colab `A100`/`H100` VMs already include high host RAM (~83 GiB)
     by default, and `--high-mem` frequently triggers `503 Service Unavailable`.
   - If a requested GPU returns `400` (tier/quota limit) or `503 Service Unavailable`
     (capacity exhaustion or unsupported shape — note `colab new` already retries
     internally for ~3 min), inspect `~/.config/colab-cli/colab.log`, report the
     exact status immediately, and ask before dropping `--high-mem` or falling
     back to another tier (e.g., `L4`, `G4`, `H100`) rather than running another
     slow retry cycle.
   - **Multi-GPU notebook portability:** When authoring or optimizing GPU Colab
     notebooks, auto-tune VRAM-sensitive defaults (quantization, context length,
     batch/ubatch, KV cache dtype, vision/expert residency) from live `nvidia-smi`
     VRAM and `compute_cap` (`sm_XX`) rather than hardcoding A100-only settings.
     Note that Colab L4 has **22.5 GiB** (`23034 MiB`) usable VRAM, not 24 GiB.
   - **Large GitHub Release downloads:** For multi-GB engine tarballs on Colab,
     prefer an 8-way parallel HTTP `Range` downloader (~150–200 MB/s) over single-stream
     `curl` (~14 MB/s), with automatic `curl` fallback if `Accept-Ranges` is absent.

3. **Run step-by-step (recommended for multi-section notebooks) or full notebook:**
   - **Multi-section / GPU notebooks (preferred)**: Extract code cells with
     `nb_cells.py extract` and run them sequentially against the persistent
     kernel. If cell 8 fails, you patch only cell 8 and re-run without
     re-downloading models from cell 3:
     ```bash
     python3 <path/to/this/skill>/scripts/nb_cells.py extract "$WORKDIR/<name>.ipynb" --out "$WORKDIR/cells"
     colab exec -s debug --timeout 60 -f "$WORKDIR/cells/cell_02.py"
     ```
   - **Small / self-contained notebooks**: Run the entire `.ipynb` directly
     (writes `<name>_output.ipynb` next to the input):
     ```bash
     colab exec -s debug --timeout 60 -f "$WORKDIR/<name>.ipynb"
     ```
   - For plot/image outputs, pass `--output-image "$WORKDIR/plot.png"`.
   - For multi-line diagnostic snippets, write a temp script and run
     `colab exec -s debug -f "$WORKDIR/check.py"` instead of piping
     `echo '...' | colab exec` (zsh expands escape sequences like `\n` inside
     `echo`).

4. **Patch & verify:**
   - Edit the extracted cell file (or notebook directly), then write it cleanly
     back into the `.ipynb` JSON with `nb_cells.py patch`:
     ```bash
     python3 <path/to/this/skill>/scripts/nb_cells.py patch "$WORKDIR/<name>.ipynb" --cell 8 --from "$WORKDIR/cells/cell_08.py"
     ```
   - Re-run from a fresh kernel (`colab restart-kernel -s debug`) until every
     cell succeeds cleanly.
   - **Verify interactive apps/servers live**: Cell exit code 0 only proves a
     server launched, not that handlers work. For Gradio, `llama-server`, or
     FastAPI cells, run a live test script against the local and public URLs
     (multi-turn requests, Stop/cancel slot cleanup, Clear/reset, error paths,
     and HTTP 200 on share links).

5. **Sync back & mandatory cleanup (CRITICAL — idle GPUs burn compute units!):**
   - If pulled from Google Drive, push the fixed notebook back to the **exact
     same Drive file ID** with the Colab MIME type (preserving the user's link,
     revision history, and Colab rendering). Push the clean `.ipynb` (not
     `<name>_output.ipynb` with expired tunnel URLs):
     ```bash
     gdrive mutate update <FILE_ID> "$WORKDIR/<name>.ipynb" --mime-type "application/vnd.google.colaboratory"
     ```
   - Always stop the VM when done (unless the user explicitly asked to keep it
     alive):
     ```bash
     colab stop -s debug
     ```
   - Remove the temporary directory (`rm -rf "$WORKDIR"`) and report a concise
     summary of every fix made and confirmation that the VM is stopped.

---

## One-Shot Ephemeral Runs (`colab run`)

For standalone `.py` scripts that do not need a persistent debugging kernel:
```bash
colab run --gpu T4 script.py arg1 arg2
```
Provisions a fresh VM, runs `script.py` with exit-code propagation, and
automatically tears down the VM even if the script errors.

## Guardrails
- **Prefer `-f` script files over `colab ssh` or stdin heredocs**:
  - `colab ssh` exits with code `255` if the host has no SSH keypair in `~/.ssh/`.
    Default to writing a small Python script to `"$WORKDIR/step.py"` and running
    `colab exec -s debug --timeout 120 -f "$WORKDIR/step.py"`.
  - Avoid piping large stdin heredocs into `colab exec`; if the websocket drops
    (`RuntimeError: Connection was lost.`), `colab exec` can hang past `--timeout`
    even while the remote VM stays alive. Always check `colab sessions` before
    assuming the VM died.
- **Never run TTY-interactive commands from an agent**: bare `colab repl`, bare
  `colab ssh` (without `-- CMD`), `colab auth`, and `colab drivemount` expect a
  human terminal TTY and will hang.
- **Session recovery**: If `colab exec` returns 404/401 (backend pruned an idle
  VM), run `colab sessions` and re-create with `colab new -s debug`.
