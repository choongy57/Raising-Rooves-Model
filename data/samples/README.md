# Sample data

This folder is the only part of `data/` tracked in git. It exists so a fresh
clone can run Stages 2 and 3 immediately — no API keys, no tile downloads.

## Files

| File | What it is |
| --- | --- |
| `stage1_carlton.parquet` | Real Stage 1 output for Carlton: 6,177 buildings with footprint area, roof material/colour, absorptance estimates. |

## Quickstart from a fresh clone

```bash
# 1. Copy the sample into the output folder Stage 2 reads from
#    (PowerShell)
Copy-Item data/samples/stage1_carlton.parquet data/output/
#    (macOS/Linux)
cp data/samples/stage1_carlton.parquet data/output/

# 2. Run Stage 2 (irradiance comes from NASA POWER — free, no key)
python -m stage2_irradiance.run_stage2 --suburb Carlton

# 3. Run Stage 3 (thermal → electricity savings)
python -m stage3_thermal.run_stage3 --suburb Carlton

# 4. Visualise
python -m tools.visualise_results --suburb Carlton
```

To regenerate this fixture from scratch you need a `GOOGLE_MAPS_API_KEY`:
`python -m stage1_segmentation.run_stage1 --suburb Carlton`, then copy the
resulting `data/output/stage1_carlton.parquet` here.

Do not add large files to this folder — keep samples under ~1 MB so the
repo stays fast to clone.
