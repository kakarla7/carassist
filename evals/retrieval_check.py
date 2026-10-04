"""
Score RETRIEVAL ONLY (no Claude calls): for each golden question, did the right
source document show up in the top k, and how high?

    python evals/retrieval_check.py                       # dev questions, with car filter
    python evals/retrieval_check.py --no-filter           # same, filter off (compare!)
    python evals/retrieval_check.py --tag baseline        # also saves evals/results/retrieval_baseline.csv
    python evals/retrieval_check.py --include-holdout     # only for a final check, not while tuning

Metrics:
  hit@k : the share of questions where ANY expected source is in the top k
  MRR   : mean of 1/rank of the first expected source (1.0 = always first, 0 = never found)
Questions with no expected source (unanswerable, out of scope) are skipped here;
they are judged on the answer, not on retrieval.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import retrieve  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--k", type=int, default=5)
ap.add_argument("--no-filter", action="store_true")
ap.add_argument("--include-holdout", action="store_true")
ap.add_argument("--tag", default="")
a = ap.parse_args()

golden = pd.read_csv(ROOT / "evals" / "golden_v1.csv", dtype=str).fillna("")
if not a.include_holdout:
    golden = golden[golden.split == "dev"]

col = retrieve.get_collection()
rows = []
for r in golden.itertuples():
    expected = [s for s in r.expected_sources.split(";") if s]
    if not expected:
        continue
    car, hits = retrieve.retrieve(r.question, k=a.k, use_filter=not a.no_filter, collection=col)
    got = [h["source"] for h in hits]
    rank = next((i + 1 for i, s in enumerate(got) if s in expected), None)
    rows.append({"id": r.id, "type": r.type, "question": r.question, "rank": rank,
                 "car": f"{car['make']} {car['model']} {car['year']}",
                 "expected": ";".join(expected), "got": ";".join(got),
                 "dists": ";".join(f"{h['distance']:.2f}" for h in hits)})

df = pd.DataFrame(rows)
df["hit@1"] = df["rank"].le(1)
df["hit@3"] = df["rank"].le(3)
df[f"hit@{a.k}"] = df["rank"].le(a.k)
df["mrr"] = df["rank"].map(lambda x: 0 if pd.isna(x) else 1 / x)

metrics = ["hit@1", "hit@3", f"hit@{a.k}", "mrr"]
by_type = df.groupby("type")[metrics].mean()
by_type.loc["ALL"] = df[metrics].mean()
by_type["n"] = df.groupby("type").size().reindex(by_type.index).fillna(len(df)).astype(int)
print(f"\nfilter={'off' if a.no_filter else 'on'}  k={a.k}  questions={len(df)}\n")
print(by_type.round(2).to_string())

misses = df[~df["hit@3"]]
print(f"\nMISSES (not in top 3): {len(misses)}")
for m in misses.itertuples():
    print(f"\n{m.id} [{m.type}] {m.question}\n  car detected: {m.car}\n  expected: {m.expected}")
    for src, dist in list(zip(m.got.split(";"), m.dists.split(";")))[:3]:
        print(f"  got: {src}  (dist {dist})")

if a.tag:
    out = ROOT / "evals" / "results"
    out.mkdir(exist_ok=True)
    df.to_csv(out / f"retrieval_{a.tag}.csv", index=False)
    print(f"\nSaved evals/results/retrieval_{a.tag}.csv")
