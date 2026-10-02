"""
Generate CarAssist text documents from the CSV tables in data/.

Two kinds of documents:
  1. TEMPLATED (default, no API needed): manual sections, symptom articles,
     service bulletins, FAQs. Facts come straight from the tables, so they are
     always consistent with the answer key.
  2. LLM-WRITTEN (--llm): customer/advisor conversations and service tickets.
     Facts are injected into the prompt, and every output is checked: it must
     contain the correct part number and no other/invented part numbers.

Usage (from the repo root):
    python src/generate_docs.py                 # templated docs
    python src/generate_docs.py --llm --dry-run # preview one LLM prompt, no API call
    python src/generate_docs.py --llm           # needs ANTHROPIC_API_KEY
"""
import argparse
import os
import re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # load environment variables from .env
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA, DOCS = ROOT / "data", ROOT / "docs"

models = pd.read_csv(DATA / "models.csv").fillna("")
parts = pd.read_csv(DATA / "parts_catalog.csv")
fitment = pd.read_csv(DATA / "fitment.csv")
symptoms = pd.read_csv(DATA / "symptoms.csv").fillna("")
fit = fitment.merge(parts, on="part_number")  # one row per (model_id, part)

KNOWN_PARTS = set(parts.part_number)
MODEL_IDS = set(models.model_id)


# ---------- helpers ----------
def write_doc(doc_type, doc_id, meta, title, body):
    folder = DOCS / doc_type
    folder.mkdir(parents=True, exist_ok=True)
    head = "\n".join(["---", f"doc_id: {doc_id}", f"doc_type: {doc_type}"]
                     + [f"{k}: {v}" for k, v in meta.items()] + ["---"])
    (folder / f"{doc_id}.md").write_text(f"{head}\n\n# {title}\n\n{body.strip()}\n")


def label(r):
    return f"{r.make} {r.model} {r.year}"


def meta_for(r):
    return {"make": r.make, "model": r.model, "year": r.year}


def parts_of(model_id):
    return fit[fit.model_id == model_id]


def part_number(model_id, name_prefix):
    hit = parts_of(model_id)
    hit = hit[hit.part_name.str.startswith(name_prefix)]
    return ", ".join(hit.part_number) if len(hit) else "not listed"


def quirk_if(r, *keywords):
    q = r.quirk
    return f"\nModel note: {q}.\n" if q and any(k in q.lower() for k in keywords) else ""


# ---------- templated: 7 manual sections per model-year ----------
def tire_change(r):
    if r.spare_location.startswith("None"):
        spare = (f"This model has no spare tire ({r.spare_location}). For a puncture, use the inflator kit; "
                 "to swap a wheel, use the steps below with a replacement wheel.")
    else:
        spare = f"The spare tire is stored here: {r.spare_location}."
    return f"""Tire size: {r.tire_size}. Recommended pressure: {r.tire_pressure_psi} psi.
{spare}
{quirk_if(r, "lug", "spare")}
Steps:
1. Park on level ground, set the parking brake, and turn on the hazard lights.
2. Loosen the lug nuts about a half turn before raising the car.
3. Place the jack at the marked jack points: {r.jack_points}.
4. Raise the car, remove the lug nuts, and swap the wheel.
5. Tighten the lug nuts by hand in a star pattern, then lower the car.
6. Torque the lug nuts to {r.lug_torque_ftlb} ft-lb in a star pattern.
7. Check tire pressure and re-check the torque after about 50 miles."""


def fluids(r):
    return f"""Engine oil: {r.oil_type}, capacity {r.oil_capacity_l} liters.
Coolant: {r.coolant_type}. Do not mix coolant types.
Oil change interval: every {r.oil_change_interval_miles:,} miles.
Oil filter part number: {part_number(r.model_id, "Oil filter")}."""


def battery(r):
    return f"""Battery type: {r.battery_type}. Location: {r.battery_location}.
Battery part number: {part_number(r.model_id, "Battery,")}.
{quirk_if(r, "battery", "agm")}
Jump-starting:
1. Park the donor car close, with both engines off.
2. Connect red to the positive terminal of the dead battery, then to the donor's positive terminal.
3. Connect black to the donor's negative terminal, then to bare unpainted metal on the dead car's engine (not the dead battery's negative terminal).
4. Start the donor car, wait two minutes, then start the dead car.
5. Remove the clamps in reverse order. Drive at least 20 minutes to recharge."""


def fuse_box(r):
    return f"""Fuse box locations: {r.fuse_box_locations}.
A fuse diagram is printed on each fuse box cover. Replace a blown fuse only with one of the same amperage.
For a headlight that is out, check the headlight bulb and its fuse first.
Headlight bulb part number: {part_number(r.model_id, "Headlight bulb")}."""


def maintenance(r):
    return f"""Service schedule for the {label(r)}:
- Engine oil and oil filter: every {r.oil_change_interval_miles:,} miles. Oil filter: {part_number(r.model_id, "Oil filter")}.
- Cabin air filter: every {r.cabin_filter_interval_miles:,} miles. Cabin air filter: {part_number(r.model_id, "Cabin air filter")}.
{quirk_if(r, "cabin filter")}
- Engine air filter: inspect at each oil change. Engine air filter: {part_number(r.model_id, "Engine air filter")}.
- Wiper blades: replace when they streak. Front wiper blade set: {part_number(r.model_id, "Front wiper")}."""


def parts_list(r):
    lines = ["| Part | Position | Part number |", "|---|---|---|"]
    for p in parts_of(r.model_id).sort_values(["category", "part_name"]).itertuples():
        lines.append(f"| {p.part_name} | {p.position} | {p.part_number} |")
    return ("Parts that fit this exact make, model, and year. Part numbers can differ between "
            "similar models and between model years, so confirm the fit before ordering.\n\n" + "\n".join(lines))


def warning_lights(r):
    return f"""Warning lights on the {label(r)}:
- Check engine light, steady: the engine management system found a fault. Tighten the gas cap and have the codes read soon.
- Check engine light, flashing: a serious misfire. Reduce speed and stop driving as soon as it is safe.
- Brake warning light: check the parking brake is released. If it stays on, have the brakes inspected before driving.
- Battery light: the charging system may be failing. Drive straight to a service center.
- Oil pressure light: stop the engine immediately and check the oil level."""


SECTIONS = {
    "tire_change": ("Changing a tire", tire_change),
    "fluids": ("Fluids and capacities", fluids),
    "battery": ("Battery and jump-starting", battery),
    "fuse_box": ("Fuse box", fuse_box),
    "maintenance": ("Maintenance schedule", maintenance),
    "parts_list": ("Parts list", parts_list),
    "warning_lights": ("Warning lights", warning_lights),
}


def build_manual():
    n = 0
    for r in models.itertuples():
        for doc_type, (title, fn) in SECTIONS.items():
            write_doc(doc_type, f"{doc_type}_{r.model_id}", meta_for(r),
                      f"{title}: {label(r)}", fn(r))
            n += 1
    return n


# ---------- templated: symptom articles ----------
def build_symptoms():
    for s in symptoms.itertuples():
        causes = [c.strip() for c in s.likely_causes_ranked.split(";")]
        cause_lines = "\n".join(f"{i}. {c}" for i, c in enumerate(causes, 1))
        safety = {"high": "SAFETY: this affects braking or steering. Do not keep driving normally; have it inspected promptly.",
                  "medium": "Safety: have it checked soon. It may leave you stranded or affect driving.",
                  "low": "Safety: low urgency, but fix it at the next service."}[s.safety_flag]
        if s.model_specific == "Yes":
            ids = [x.strip() for x in s.applies_to.split(";")]
            r = models[models.model_id == ids[0]].iloc[0]
            meta = {"make": r.make, "model": r.model, "year": "all"}
            parts_line = f"Parts involved: {s.parts_involved}. Part number for these models: {part_number(ids[0], 'Battery hold-down')}."
        else:
            meta = {"make": "all", "model": "all", "year": "all"}
            parts_line = (f"Parts involved: {s.parts_involved}. Part numbers differ by make, model, "
                          "and year, so confirm the exact fit before ordering.")
        body = (f"Symptom: {s.symptom_plain}.\n\nLikely causes, most likely first:\n{cause_lines}\n\n"
                f"Quick checks: {s.quick_checks}.\n\n{parts_line}\n\n{safety}\n\n"
                "A technician should confirm the diagnosis before parts are replaced.")
        write_doc("symptom", s.symptom_id, {**meta, "safety_flag": s.safety_flag}, s.symptom_plain, body)
    return len(symptoms)


# ---------- templated: service bulletins (hand-written, planted traps) ----------
BULLETINS = [
    ("SB-2025-01", "Arden", "Cora", "2025", "Lug nut torque change",
     "For the 2025 Arden Cora, lug nut torque is 90 ft-lb. The 2024 Cora remains 80 ft-lb. "
     "Do not apply the 2025 value to a 2024 vehicle or the reverse."),
    ("SB-2025-02", "Arden", "Brio", "2025", "No spare tire on 2025 Brio",
     "The 2025 Arden Brio does not have a spare tire. It ships with a tire inflator kit and sealant in the trunk side pocket. "
     "The 2024 Brio still has a spare under the trunk floor mat."),
    ("SB-2024-03-TERN", "Norvik", "Tern", "all", "Tern and Vale front brake pads are not interchangeable",
     "The Norvik Tern and Norvik Vale look alike but use different front brake pads. Tern: BP-4417. Vale: BP-4418. "
     "Check make, model, and year before ordering. Left mirror glass also differs: Tern MR-8801, Vale MR-8802."),
    ("SB-2024-03-VALE", "Norvik", "Vale", "all", "Tern and Vale front brake pads are not interchangeable",
     "The Norvik Tern and Norvik Vale look alike but use different front brake pads. Tern: BP-4417. Vale: BP-4418. "
     "Check make, model, and year before ordering. Left mirror glass also differs: Tern MR-8801, Vale MR-8802."),
    ("SB-2025-04", "Arden", "Aria", "2025", "2025 Aria requires an AGM battery",
     "The 2025 Arden Aria uses a Group 48 AGM battery, part BAT-48A. Do not fit the flooded Group 35 battery (BAT-35) "
     "used on the 2024 Aria. Using the wrong battery type can cause charging problems."),
]


def build_bulletins():
    for sb_id, make, model, year, title, body in BULLETINS:
        write_doc("bulletin", sb_id, {"make": make, "model": model, "year": year}, f"{sb_id}: {title}", body)
    return len(BULLETINS)


# ---------- templated: FAQs ----------
def build_faqs():
    n = 0
    for (make, model), g in models.groupby(["make", "model"]):
        r = g.iloc[0]
        body = (f"Q: How often should I change the cabin air filter on the {make} {model}?\n"
                f"A: Every {r.cabin_filter_interval_miles:,} miles.\n\n"
                f"Q: What oil does the {make} {model} use, and how much?\n"
                f"A: {r.oil_type}, {r.oil_capacity_l} liters, changed every {r.oil_change_interval_miles:,} miles.\n\n"
                f"Q: What tire pressure should I use on the {make} {model}?\n"
                f"A: {r.tire_pressure_psi} psi for size {r.tire_size}.")
        write_doc("faq", f"faq_{make}_{model}".lower(), {"make": make, "model": model, "year": "all"},
                  f"FAQ: {make} {model}", body)
        n += 1
    write_doc("faq", "faq_general", {"make": "all", "model": "all", "year": "all"}, "FAQ: ordering the right part",
              "Q: What do I need to find the right part?\nA: The make, model, and model year. Part numbers can differ "
              "between similar models and between years.\n\nQ: Can a technician confirm my diagnosis?\n"
              "A: Yes. Symptom guides list likely causes, but a technician should confirm before parts are replaced.")
    return n + 1


# ---------- LLM-written: conversations and tickets ----------
# (model_id, symptom_id, part_number, doc_type)
SEEDS = [
    ("ARD-CORA-2025", "SYM-01", "BP-3305", "conversation"),
    ("NOR-TERN-2024", "SYM-01", "BP-4417", "conversation"),
    ("NOR-VALE-2024", "SYM-01", "BP-4418", "conversation"),
    ("ARD-ARIA-2025", "SYM-02", "BAT-48A", "conversation"),
    ("ARD-ARIA-2024", "SYM-02", "BAT-35", "conversation"),
    ("ARD-CORA-2024", "SYM-10", "BAT-HD-47", "conversation"),
    ("NOR-SABLE-2025", "SYM-06", "CF-2306", "conversation"),
    ("ARD-BRIO-2024", "SYM-07", "WP-2400", "conversation"),
    ("NOR-TERN-2025", "SYM-08", "BLB-H7", "conversation"),
    ("ARD-CORA-2025", "SYM-09", "AF-6411", "conversation"),
    ("ARD-CORA-2024", "SYM-09", "AF-6410", "conversation"),
    ("NOR-VALE-2025", "SYM-03", "BR-7710", "conversation"),
    ("ARD-ARIA-2024", "SYM-01", "BP-3301", "ticket"),
    ("ARD-BRIO-2025", "SYM-04", "BP-3301", "ticket"),
    ("NOR-SABLE-2024", "SYM-01", "BP-4421", "ticket"),
    ("NOR-TERN-2025", "SYM-06", "CF-2305", "ticket"),
    ("ARD-ARIA-2025", "SYM-08", "BLB-H11", "ticket"),
    ("NOR-VALE-2024", "SYM-07", "WP-2600", "ticket"),
    ("ARD-CORA-2025", "SYM-06", "CF-1202", "ticket"),
    ("NOR-SABLE-2025", "SYM-09", "AF-6430", "ticket"),
]

SYSTEM = ("You write realistic fictional documents for a car dealer's parts-and-service desk. "
          "Use ONLY the facts you are given. Never mention any part number, ID code, price, or "
          "specification that is not in the facts. Output only the document text.")


def build_prompt(seed):
    model_id, sym_id, pn, kind = seed
    r = models[models.model_id == model_id].iloc[0]
    s = symptoms[symptoms.symptom_id == sym_id].iloc[0]
    p = parts[parts.part_number == pn].iloc[0]
    facts = (f"Vehicle: {r.make} {r.model} {r.year} ({r.engine}).\n"
             f"Symptom (customer's issue): {s.symptom_plain}.\n"
             f"Diagnosed cause: {s.likely_causes_ranked.split(';')[0].strip()}.\n"
             f"Quick check: {s.quick_checks}.\n"
             f"Correct part: {p.part_name}, part number {pn}.\n"
             f"Safety level: {s.safety_flag}.")
    if kind == "conversation":
        task = ("Write a chat between a Customer and a dealer service Advisor. The customer describes the problem in "
                "casual, imperfect language and may not know technical terms. The advisor asks one clarifying question, "
                "explains the likely cause, and gives the correct part number once. If safety level is high, the advisor "
                "gives a safety warning. 120-200 words. Format as 'Customer:' and 'Advisor:' lines.")
    else:
        task = ("Write a short service ticket in plain text with these fields: Customer complaint (in the customer's own "
                "words), Technician findings, Part replaced (name and part number), Notes. Do not include a ticket ID. "
                "80-140 words.")
    return f"{task}\n\nFacts:\n{facts}"


PART_LIKE = re.compile(r"\b[A-Z]{2,3}-[A-Z0-9]{1,4}(?:-[A-Z0-9]{1,4})?\b")


def check_text(text, expected):
    """Return a list of problems; empty means the document passed."""
    for mid in MODEL_IDS:
        text = text.replace(mid, "")
    found = set(PART_LIKE.findall(text))
    problems = []
    if expected not in found:
        problems.append(f"missing expected part {expected}")
    invented = found - KNOWN_PARTS
    if invented:
        problems.append(f"invented part-like codes: {sorted(invented)}")
    wrong = (found & KNOWN_PARTS) - {expected}
    if wrong:
        problems.append(f"mentions other real parts: {sorted(wrong)}")
    return problems


def build_llm(dry_run, model_name):
    if dry_run:
        print(SYSTEM, "\n---\n", build_prompt(SEEDS[0]))
        return 0
    import anthropic  # imported here so templated mode needs no API package
    client = anthropic.Anthropic()
    ok = 0
    for i, seed in enumerate(SEEDS, 1):
        model_id, sym_id, pn, kind = seed
        r = models[models.model_id == model_id].iloc[0]
        text, problems = "", ["not generated"]
        for _ in range(3):  # retry if the check fails
            msg = client.messages.create(model=model_name, max_tokens=600, system=SYSTEM,
                                         messages=[{"role": "user", "content": build_prompt(seed)}])
            text = msg.content[0].text.strip()
            problems = check_text(text, pn)
            if not problems:
                break
        doc_id = f"{kind}_{i:02d}_{model_id}_{sym_id}"
        meta = {**meta_for(r), "symptom_id": sym_id, "part_number": pn, "generated_by": "llm"}
        if problems:
            meta["REJECTED"] = "; ".join(problems)
            write_doc("_rejected", doc_id, meta, f"{kind.title()}: {label(r)}", text)
            print(f"REJECTED {doc_id}: {problems}")
        else:
            write_doc(kind, doc_id, meta, f"{kind.title()}: {label(r)}", text)
            ok += 1
    return ok


# ---------- main ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also generate conversations and tickets")
    ap.add_argument("--dry-run", action="store_true", help="with --llm: print one prompt, no API call")
    ap.add_argument("--llm-model", default=os.environ.get("CARASSIST_LLM", "claude-haiku-4-5-20251001"))
    a = ap.parse_args()

    if a.llm:
        print("LLM docs passed checks:", build_llm(a.dry_run, a.llm_model))
    else:
        print("manual sections:", build_manual())
        print("symptom articles:", build_symptoms())
        print("bulletins:", build_bulletins())
        print("faqs:", build_faqs())
