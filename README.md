# CarAssist data

All makes, models, specs, and part numbers are **fictional**. These tables are the ground truth: text documents are generated from them, and eval answers come from them.

## Files

| File | Rows | What it is |
|---|---|---|
| `models.csv` | 12 | One row per make, model, and year. IDs look like `NOR-TERN-2024`. |
| `parts_catalog.csv` | 31 | One row per part: number, name, category, position, price, stock. |
| `fitment.csv` | 109 | Which part fits which `model_id`, one row per fit. |
| `symptoms.csv` | 11 | Plain-language symptoms with ranked likely causes, quick checks, parts involved, safety flag. |

## Lineup

- **Arden:** Aria, Brio, Cora
- **Norvik:** Sable, Tern, Vale
- Years: 2024 and 2025

## Column notes

- `models.csv`: units are psi, ft-lb, liters, and miles. `quirk` is blank when a model has no special note.
- `parts_catalog.csv`: `price_usd` and `stock` are not used in this build (reserved for a later structured lookup tool).
- `fitment.csv`: every `part_number` and `model_id` exists in the other tables (checked).
- `symptoms.csv`: `safety_flag` is high, medium, or low. `applies_to` is `All` or a list of `model_id`s.

## Planted traps (the answer key for evals)

**Near-duplicate models**
- Norvik Tern and Vale are identical except lug nut torque (Tern 85 ft-lb, Vale 88 ft-lb).
- Their front brake pads differ by one digit: Tern `BP-4417`, Vale `BP-4418`.
- Their left mirror glass differs by one digit: Tern `MR-8801`, Vale `MR-8802`.

**Year differences on the same model**
- Arden Aria: 2024 uses a Group 35 flooded battery (`BAT-35`); 2025 uses a Group 48 AGM (`BAT-48A`).
- Arden Brio: 2024 has a spare tire; 2025 has no spare, only an inflator kit.
- Arden Cora: lug torque is 80 ft-lb in 2024 and 90 ft-lb in 2025. Engine air filter is `AF-6410` for 2024 and `AF-6411` for 2025.

**Parts that fit only some years**
- `BM-1500` (front bumper cover) fits the 2024 Aria only.
- `AF-6410` fits the 2024 Cora only; `AF-6411` fits the 2025 Cora only.

**Parts that fit many models**
- `BP-5502` (rear pads) fits all six Norvik model-years; `BP-3301` (front pads) fits Aria and Brio, both years.

**Model-specific quirks**
- Arden Cora: battery is under the rear seat; rattling there points to a loose hold-down clamp (`BAT-HD-47`).
- Norvik Sable: cabin filter is behind the glovebox and needs a trim removal tool; its service interval is 12,000 miles (others 15,000).

**Not-found cases**
- `SPK-9999` (spark plug set) is in the catalog but fits no model in `fitment.csv`.
- No model outside the 12 rows exists, so any other make or model is unanswerable.

**Safety and ambiguity**
- `SYM-01` (grinding when braking) and `SYM-03` (steering wheel shakes) are high safety: answers must carry a warning.
- `SYM-11` ("car is making a noise") is deliberately vague: the right behavior is to ask follow-up questions.
- A question with no make, model, or year should trigger a clarifying question, not a guess.

### Golden set and validation

`evals/golden_v1.csv` holds 29 test questions with expected answers, sources, and part numbers, all traced to the tables in `data/`. Types: direct, near-duplicate, year trap, cross-document, ambiguous, unanswerable, out-of-scope, safety. Five are held out and not used while tuning.

`python evals/validate_golden.py` checks the answer key is consistent: source files exist, expected parts exist and fit the stated car, IDs are unique. Re-run it whenever the tables, documents, or golden questions change. Chunking and embedding changes are scored by the eval runner instead (coming in a later step).