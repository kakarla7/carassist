"""Check golden_v1.csv against the tables and the generated docs. Run from the repo root:
    python evals/validate_golden.py
"""
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
g = pd.read_csv(ROOT / "evals/golden_v1.csv", dtype=str).fillna("")
parts = pd.read_csv(ROOT / "data/parts_catalog.csv")
fit = pd.read_csv(ROOT / "data/fitment.csv")
models = pd.read_csv(ROOT / "data/models.csv")

errors = []
if g.id.duplicated().any():
    errors.append("duplicate ids")

for r in g.itertuples():
    # 1. every expected source file exists
    for src in filter(None, r.expected_sources.split(";")):
        if not (ROOT / src).exists():
            errors.append(f"{r.id}: missing source {src}")
    # 2. expected part exists, and fits the stated car when the question names one
    pn = r.expected_part_number
    if pn:
        if pn not in set(parts.part_number):
            errors.append(f"{r.id}: part {pn} not in catalog")
        elif r.stated_make and r.stated_model and r.stated_year:
            mid = f"{r.stated_make[:3].upper()}-{r.stated_model.upper()}-{r.stated_year}"
            if not ((fit.part_number == pn) & (fit.model_id == mid)).any():
                errors.append(f"{r.id}: {pn} does not fit {mid}")
    # 3. stated car exists, unless the question is meant to be unanswerable
    if r.stated_make and r.type != "unanswerable":
        mid = f"{r.stated_make[:3].upper()}-{r.stated_model.upper()}-{r.stated_year}"
        if mid not in set(models.model_id):
            errors.append(f"{r.id}: unknown car {mid}")
    if r.expected_behavior not in {"answer", "clarify", "abstain"}:
        errors.append(f"{r.id}: bad expected_behavior")

print(g.groupby(["type", "split"]).size().unstack(fill_value=0))
print("\nERRORS:" if errors else "\nAll checks passed.")
for e in errors:
    print(" -", e)
sys.exit(1 if errors else 0)
