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
  - **If `gdrive` is available**: pull the notebook into `/tmp/colab_work/`,
    debug it there, push fixes back to the **same Drive file ID**
    (`gdrive mutate update`), and delete `/tmp/colab_work/` when done (zero
    permanent local files).
  - **If `gdrive` is not installed**: operate directly on a local `.ipynb`/`.py`
    file in the workspace, or let the user know they can install the official
    Google Drive plugin from the Antigravity Marketplace for zero-local-file
    Drive sync.

---

## Step 1: Pre-Flight Inspection & Single-Turn User Alignment (UX Rule)

**Always inspect the notebook before allocating a billable GPU VM.**

1. If the notebook is on Google Drive (and `gdrive` is available), locate and
   pull it to `/tmp/colab_work/`:
   ```bash
   gdrive readonly search --name-contains "<notebook name>"
   mkdir -p /tmp/colab_work
   gdrive readonly download <FILE_ID> --out /tmp/colab_work/<name>.ipynb
   ```
2. Run the bundled static scanner ([nb_cells.py](./scripts/nb_cells.py)) before
   starting a VM:
   ```bash
   python3 <path/to/this/skill>/scripts/nb_cells.py scan /tmp/colab_work/<name>.ipynb
   ```
   This detects:
   - **Colab-UI blockers** (`output.eval_js` missing `timeout_sec`, `drive.mount`,
     `files.upload`, `userdata.get`, `input()`).
   - **Long-running cells** (training/fine-tuning loops, large model/dataset
     downloads, compilation, or benchmarks).
3. **Ask once before provisioning** (skip if the user already specified both):
   - **Runtime**: If the user did not specify hardware, ask which runtime to use
     (`CPU`, `T4`, `L4`, `G4`, `A100`, `H100`, `TPU v5e1`/`v6e1`, and whether
     `--high-mem` is needed).
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
   - If a requested GPU returns `400` (tier/quota limit), report it clearly and
     ask before falling back to another tier.

3. **Run step-by-step (recommended for multi-section notebooks) or full notebook:**
   - **Multi-section / GPU notebooks (preferred)**: Extract code cells with
     `nb_cells.py extract` and run them sequentially against the persistent
     kernel. If cell 8 fails, you patch only cell 8 and re-run without
     re-downloading models from cell 3:
     ```bash
     python3 <path/to/this/skill>/scripts/nb_cells.py extract /tmp/colab_work/<name>.ipynb --out /tmp/colab_work/cells
     colab exec -s debug --timeout 60 -f /tmp/colab_work/cells/cell_02.py
     ```
   - **Small / self-contained notebooks**: Run the entire `.ipynb` directly
     (writes `<name>_output.ipynb` next to the input):
     ```bash
     colab exec -s debug --timeout 60 -f /tmp/colab_work/<name>.ipynb
     ```
   - For plot/image outputs, pass `--output-image /tmp/colab_work/plot.png`.

4. **Patch & verify:**
   - Edit the extracted cell file (or notebook directly), then write it cleanly
     back into the `.ipynb` JSON with `nb_cells.py patch`:
     ```bash
     python3 <path/to/this/skill>/scripts/nb_cells.py patch /tmp/colab_work/<name>.ipynb --cell 8 --from /tmp/colab_work/cells/cell_08.py
     ```
   - Re-run until every cell succeeds cleanly.

5. **Sync back & mandatory cleanup (CRITICAL — idle GPUs burn compute units!):**
   - If pulled from Google Drive, push the fixed notebook back to the **exact
     same Drive file ID** (preserving the user's link and revision history):
     ```bash
     gdrive mutate update <FILE_ID> /tmp/colab_work/<name>.ipynb
     ```
   - Always stop the VM when done (unless the user explicitly asked to keep it
     alive):
     ```bash
     colab stop -s debug
     ```
   - Remove temporary files (`rm -rf /tmp/colab_work`) and report a concise
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
- **Non-interactive shell on the VM**:
  `colab ssh -s debug -- 'nvidia-smi | head -5'` (or fall back to
  `echo '!nvidia-smi' | colab exec -s debug`).
- **Never run TTY-interactive commands from an agent**: bare `colab repl`, bare
  `colab ssh` (without `-- CMD`), `colab auth`, and `colab drivemount` expect a
  human terminal TTY and will hang.
- **Session recovery**: If `colab exec` returns 404/401 (backend pruned an idle
  VM), run `colab sessions` and re-create with `colab new -s debug`.
