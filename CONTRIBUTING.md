# Contributing to Raising Rooves

Quick guide for team members getting set up and working on the repo.

---

## Prerequisites

- Python 3.11 or later
- Git
- A terminal (VS Code integrated terminal works well)
- Access to the shared `.env` file (ask Ryan for the keys you need)

---

## First-Time Setup

```bash
# 1. Clone the repo
git clone https://github.com/<org>/raising-rooves-model.git
cd raising-rooves-model

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
# Windows (PowerShell):
Copy-Item .env.example .env
# macOS / Linux:
cp .env.example .env
# Then open .env and fill in the keys you need for your task
```

> **Never commit `.env` or paste API keys into chat, commits, or screenshots.**

### Your first output (no keys needed)

Before chasing API keys, prove your setup works using the tracked sample:

```bash
# Windows (PowerShell):
Copy-Item data/samples/stage1_carlton.parquet data/output/
# macOS / Linux:
cp data/samples/stage1_carlton.parquet data/output/

python -m stage2_irradiance.run_stage2 --suburb Carlton
python -m stage3_thermal.run_stage3 --suburb Carlton
python -m tools.visualise_results --suburb Carlton
# Open data/output/stage3_carlton_report.html in a browser
```

If that works, your environment is good — you only need API keys for the
specific stage you're working on (see the table below).

---

## Environment Variables

| Variable | What it's for | Who needs it |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Satellite tile download (Stage 1) | Stage 1 |
| `GEMINI_API_KEY` | Gemini roof-assessment experiment (HSV validation) | Experiment only; free tier at https://aistudio.google.com/app/apikey |
| `OPENTOPO_API_KEY` | COP30 DSM fallback for pitch extraction | Pitch tool |
| `GOOGLE_SHEET_ID` | QA ticket tracker | QA / test monitor |
| `GWS_CREDS_FILE` | Google Sheets OAuth credential path (Windows: use a full path, `~` is not expanded) | QA / test monitor |

Stage 2 needs no key at all — irradiance comes from NASA POWER automatically.

NCI THREDDS access for BARRA2 does not use a key — register at https://my.nci.org.au and request project `ob53` access.

---

## Running the Pipeline

Each stage runs independently:

```bash
# Stage 1 — Roof segmentation
python -m stage1_segmentation.run_stage1 --suburb "Carlton"

# Stage 2 — Irradiance + cool roof delta
python -m stage2_irradiance.run_stage2 --suburb "Carlton"

# Stage 3 — Thermal electricity savings
python -m stage3_thermal.run_stage3 --suburb "Carlton"

# Tools
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
python -m tools.visualise_results --suburb Carlton
python -m tools.compare_suburbs
```

Add `--debug` to any command for verbose logging.

---

## Branch and PR Workflow

1. **Never commit directly to `main`.**
2. Create a branch named `yourname/what-youre-doing`:
   ```bash
   git checkout -b seamus/cba-script
   ```
3. Make small, focused commits. Commit message should say *why*, not just *what*:
   ```bash
   git add stage3_thermal/cba.py
   git commit -m "Add CBA standalone script reading Stage 3 CSV output"
   ```
4. Open a pull request into `main` when your work is ready for review.
5. Keep PRs to one stage or one tool — easier to review and revert if needed.

---

## Code Conventions

- Files: `snake_case.py`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE` (defined in `config/settings.py`)
- Type-hint every function signature
- Use `logging` not `print`
- Coordinates are always EPSG:4326 (lat/lon). CRS bugs are treated as serious.
- Area in square metres (`m2`). Unit bugs are treated as serious.
- Irradiance: W/m² for instantaneous values; kWh/m²/year for annual aggregates

---

## Running Tests

```bash
python -m pytest tests/
```

Before committing any physics or data-join changes, also run:

```bash
python -m tools.test_monitor --dry-run
```

---

## Output Files

All pipeline outputs go to `data/output/`. Raw data and downloads go to `data/raw/`. Do not commit large data files — they are gitignored.

---

## Questions?

Reach out on the team group chat or check `CLAUDE.md` for the full project guide.
