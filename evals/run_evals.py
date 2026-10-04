"""
Step 7: EVAL RUNNER. Run golden questions through the full pipeline (retrieve -> Claude) and score the answers.

For each question it records:
  retrieval_rank  where the first expected source ranked (informational)
  part_ok         RULE-BASED: expected part present; no part that does not fit the car; no invented codes
  behavior_ok     JUDGE: did it answer / ask a clarifying question / decline, as expected?
  correct_ok      JUDGE: for answerable questions, does it state the expected facts?
  safety_ok       JUDGE: for safety questions, is there a safety warning?
  faithful_ok     JUDGE: is every claim supported by the retrieved context?
A question PASSES when every check that applies to it passes.

    python evals/run_evals.py --ids Q01,Q13,Q21           # try a few first
    python evals/run_evals.py --tag baseline              # all dev questions (costs a few cents)
    python evals/run_evals.py --repeats 3 --tag baseline  # answers vary between runs, so repeat
    python evals/run_evals.py --no-filter --tag nofilter  # change one thing, compare

Needs ANTHROPIC_API_KEY in .env. Defaults: a small model answers, a stronger model judges.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import answer as answer_mod  # noqa: E402
import retrieve  # noqa: E402

load_dotenv(ROOT / ".env")
JUDGE_MODEL = os.environ.get("CARASSIST_JUDGE_MODEL", "claude-sonnet-5-5")

parts = pd.read_csv(ROOT / "data" / "parts_catalog.csv")
fitment = pd.read_csv(ROOT / "data" / "fitment.csv")
models = pd.read_csv(ROOT / "data" / "models.csv", dtype=str)

KNOWN_PARTS = set(parts.part_number)
PART_LIKE = re.compile(r"\b[A-Z]{2,3}-[A-Z0-9]{1,4}(?:-[A-Z0-9]{1,4})?\b")
# Document IDs that look like part numbers but are not (bulletins, symptom guides, model IDs)
NOT_PARTS = [re.compile(r"\bSB-\d{4}-\d{2}(?:-[A-Z]+)?\b"), re.compile(r"\bSYM-\d{2}\b"),
             re.compile(r"\b[A-Z]{3}-[A-Z]+-\d{4}\b")]
BEHAVIOR_MAP = {"answer": "answered", "clarify": "clarified", "abstain": "declined"}


# ---------- rule-based part check ----------
def fitting_parts(make, model, year):
    """Parts that fit the car stated in the question. No model stated means nothing 'fits'."""
    if not model:
        return set()
    m = models[models.model == model]
    if make:
        m = m[m.make == make]
    if year:
        m = m[m.year == year]
    return set(fitment[fitment.model_id.isin(m.model_id)].part_number)


def known_parts_in(text):
    return {p for p in KNOWN_PARTS if re.search(rf"\b{re.escape(p)}\b", text)}


def part_check(row, text):
    """Returns (ok, note)."""
    found = known_parts_in(text)
    in_question = known_parts_in(row.question)
    fits = fitting_parts(row.stated_make, row.stated_model, row.stated_year)
    wrong = found - fits - in_question
    cleaned = text
    for pat in NOT_PARTS:
        cleaned = pat.sub("", cleaned)
    invented = set(PART_LIKE.findall(cleaned)) - KNOWN_PARTS - set(PART_LIKE.findall(row.question))
    missing = bool(row.expected_part_number) and row.expected_part_number not in found
    notes = []
    if missing:
        notes.append(f"missing expected part {row.expected_part_number}")
    if wrong:
        notes.append(f"part(s) that do not fit the stated car: {sorted(wrong)}")
    if invented:
        notes.append(f"invented part-like code(s): {sorted(invented)}")
    return (not notes), "; ".join(notes)


# ---------- LLM judge ----------
JUDGE_SYSTEM = "You are a strict, fair grader of a car-dealer support assistant. Reply with JSON only."

JUDGE_TEMPLATE = """Grade the RESPONSE.

QUESTION: {question}
EXPECTED BEHAVIOR: {expected_behavior}
EXPECTED ANSWER (key facts): {expected_answer}
SAFETY WARNING REQUIRED: {safety_required}

CONTEXT the assistant was given:
{context}

RESPONSE:
{response}

Return JSON with exactly these keys:
{{"behavior": "answered" | "clarified" | "declined",
  "correct": true | false | null,
  "safety_warning": true | false,
  "faithful": true | false,
  "notes": "one short sentence"}}

Definitions:
- behavior: "answered" = gives substantive facts or an answer; "clarified" = mainly asks the user for more information (which car, or details about the problem) without giving car-specific facts; "declined" = says the information is not available or is out of scope.
- correct: if EXPECTED BEHAVIOR is "answer": true only if the response states the key facts of the EXPECTED ANSWER (the right values and part numbers) and does not contradict them. Otherwise null.
- safety_warning: true if the response warns that the problem affects driving safety (for example braking or steering) and says a technician should check it.
- faithful: true only if every factual claim in the response is supported by the CONTEXT. Clarifying questions and generic wording do not count as claims."""


def text_of(msg):
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def judge(row, text, hits, client, model):
    prompt = JUDGE_TEMPLATE.format(
        question=row.question, expected_behavior=row.expected_behavior,
        expected_answer=row.expected_answer, safety_required=row.safety_required,
        context=answer_mod.build_context(hits), response=text)
    msg = client.messages.create(model=model, max_tokens=1500, system=JUDGE_SYSTEM,
                                 messages=[{"role": "user", "content": prompt}])
    raw = text_of(msg)
    try:
        return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {"error": f"could not parse judge output: {raw[:120]}"}


# ---------- scoring one question ----------
def score_row(row, result, verdict):
    expected_rank = [s for s in row.expected_sources.split(";") if s]
    got = [h["source"] for h in result["hits"]]
    rank = next((i + 1 for i, s in enumerate(got) if s in expected_rank), None)
    part_ok, part_note = part_check(row, result["answer"])
    out = {"id": row.id, "type": row.type, "question": row.question, "retrieval_rank": rank,
           "part_ok": part_ok, "answer": result["answer"], "cited": ";".join(result["cited_sources"])}
    if "error" in verdict:
        out.update(behavior_ok=None, correct_ok=None, safety_ok=None, faithful_ok=None,
                   notes=verdict["error"], passed=False)
        return out
    out["behavior_ok"] = verdict.get("behavior") == BEHAVIOR_MAP[row.expected_behavior]
    out["correct_ok"] = (verdict.get("correct") is True) if row.expected_behavior == "answer" else None
    out["safety_ok"] = (verdict.get("safety_warning") is True) if row.safety_required == "yes" else None
    out["faithful_ok"] = verdict.get("faithful") is True
    out["notes"] = "; ".join(n for n in [part_note, verdict.get("notes", "")] if n)
    checks = [out["behavior_ok"], out["correct_ok"], out["safety_ok"], out["faithful_ok"], part_ok]
    out["passed"] = all(c for c in checks if c is not None)
    return out


# ---------- main ----------
def main(argv=None, answer_client=None, judge_client=None, collection=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--include-holdout", action="store_true")
    ap.add_argument("--ids", default="", help="comma-separated question ids, e.g. Q01,Q13")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--tag", default="")
    ap.add_argument("--answer-model", default=answer_mod.MODEL)
    ap.add_argument("--judge-model", default=JUDGE_MODEL)
    a = ap.parse_args(argv)

    golden = pd.read_csv(ROOT / "evals" / "golden_v1.csv", dtype=str).fillna("")
    if a.ids:
        golden = golden[golden.id.isin(a.ids.split(","))]
    elif not a.include_holdout:
        golden = golden[golden.split == "dev"]

    if judge_client is None:
        import anthropic
        judge_client = anthropic.Anthropic()
    collection = collection or retrieve.get_collection()

    rows = []
    for r in golden.itertuples():
        for run in range(a.repeats):
            result = answer_mod.answer(r.question, k=a.k, use_filter=not a.no_filter, model=a.answer_model,
                                       client=answer_client, collection=collection)
            verdict = judge(r, result["answer"], result["hits"], judge_client, a.judge_model)
            row = score_row(r, result, verdict)
            row["run"] = run + 1
            rows.append(row)
            print(f"{'PASS' if row['passed'] else 'FAIL'}  {r.id}  {r.type}")

    df = pd.DataFrame(rows)
    checks = ["behavior_ok", "part_ok", "correct_ok", "safety_ok", "faithful_ok"]
    def rates(g):  # pass rate per check, ignoring questions where the check does not apply
        return {c: (g[c].dropna().astype(bool).mean() if g[c].notna().any() else float("nan"))
                for c in checks + ["passed"]}
    by_type = pd.DataFrame({t: rates(g) for t, g in df.groupby("type")}).T
    by_type.loc["ALL"] = pd.Series(rates(df))
    by_type["n"] = df.groupby("type").size().reindex(by_type.index).fillna(len(df)).astype(int)
    print(f"\nanswer model={a.answer_model}  judge={a.judge_model}  filter={'off' if a.no_filter else 'on'}  "
          f"k={a.k}  repeats={a.repeats}  rows={len(df)}\n")
    print(by_type.round(2).to_string())

    fails = df[~df.passed]
    print(f"\nFAILURES: {len(fails)}")
    for f in fails.itertuples():
        print(f"\n{f.id} [{f.type}] {f.question}\n  retrieval rank: {f.retrieval_rank}\n  "
              f"behavior_ok={f.behavior_ok} part_ok={f.part_ok} correct_ok={f.correct_ok} "
              f"safety_ok={f.safety_ok} faithful_ok={f.faithful_ok}\n  notes: {f.notes}\n  answer: {f.answer[:300]}")
    if a.tag:
        out = ROOT / "evals" / "results"
        out.mkdir(exist_ok=True)
        df.to_csv(out / f"answers_{a.tag}.csv", index=False)
        print(f"\nSaved evals/results/answers_{a.tag}.csv")
    return df


if __name__ == "__main__":
    main()
