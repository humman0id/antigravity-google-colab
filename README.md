# Google Colab Plugin for Antigravity

Run, inspect, and autonomously debug Google Colab notebooks headlessly from the **Antigravity Desktop App** or **`agy` CLI** using the official [`google-colab-cli`](https://github.com/googlecolab/google-colab-cli) — with optional **Google Drive (`gdrive`)** sync for zero permanent local files, smart automatic timeouts, and strict GPU cleanup.

## Highlights

- **Zero browser babysitting**: Works on any macOS or Linux setup (including Safari-only Macs where browser-proxy MCP tools are blocked by Mixed Content).
- **Pre-flight static scanning (`nb_cells.py`)**: Detects Colab-UI-only blockers (`output.eval_js` without `timeout_sec`, `drive.mount`, `files.upload`, `userdata.get`) and long-running training/benchmark cells *before* spinning up a billable GPU.
- **Smart automatic timeouts + upfront alignment**: Confirms your hardware runtime (CPU, T4, L4, A100, H100, TPU) and long-job timeout budget once up front, uses `60s`/`180s` defaults for normal/setup cells, and recovers wedged kernels via `colab restart-kernel` without losing `/content` downloads.
- **Zero permanent local files (with `gdrive`)**: Pulls Drive notebooks into `/tmp`, patches and verifies them on a persistent Colab kernel, pushes fixes straight back to the same Drive file ID, stops the VM, and cleans `/tmp`. Works cleanly on local `.ipynb`/`.py` files too when `gdrive` isn't installed.

---

## Installation

Works identically in both the **Antigravity Desktop App** and **`agy` CLI**.

### Option 1: Antigravity Desktop App (no `agy` CLI needed)
Clone directly into your global plugins directory:
```bash
git clone https://github.com/humman0id/antigravity-google-colab ~/.gemini/config/plugins/google-colab
```
Antigravity discovers the plugin automatically on the next turn and lists it under **Customizations**.

### Option 2: Antigravity CLI (`agy`)
```bash
agy plugin install https://github.com/humman0id/antigravity-google-colab
```

---

## Prerequisites

1. **Install `google-colab-cli`** (macOS or Linux):
   ```bash
   uv tool install google-colab-cli
   # or: pip install google-colab-cli
   ```
2. **One-time Google OAuth login** (cached in your OS keyring):
   ```bash
   colab new -s test && colab stop -s test
   ```
3. *(Optional)* **Google Drive plugin (`gdrive`)**: Install from the Antigravity Marketplace if you want the agent to pull and update Drive notebooks directly with zero permanent local files.

---

## Example Prompts

- `/google-colab Run my Drive notebook "training_lab" on an A100 GPU and fix any bugs`
- `/google-colab Debug ./experiment.ipynb on a T4 GPU until all cells pass`
- `/google-colab Check my active Colab sessions and stop any idle GPUs`

---

## Updating

```bash
# Update the plugin (if installed via git clone)
git -C ~/.gemini/config/plugins/google-colab pull

# Update the underlying Google Colab CLI
uv tool upgrade google-colab-cli
```

## License

[MIT](./LICENSE)
