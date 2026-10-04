"""
Step 5: RETRIEVAL. Given a question, find the most relevant chunks in the Chroma index.

Two ideas:
  1. Similarity search: embed the question, return the k nearest chunks.
  2. Metadata filtering: if the question names a car (make, model, year), only
     search chunks about that car (plus general chunks tagged "all"). This is the
     main defense against near-duplicates like Norvik Tern vs. Vale.

Try it from the repo root:
    python src/retrieve.py "lug nut torque on my 2024 Norvik Vale?"
    python src/retrieve.py "lug nut torque on my 2024 Norvik Vale?" --no-filter
"""
import argparse
import re
from pathlib import Path

import chromadb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "index" / "chroma"
COLLECTION = "carassist"

# Known cars come from the table, so the extractor never needs updating by hand.
_models = pd.read_csv(ROOT / "data" / "models.csv", dtype=str)
MAKES = sorted(_models.make.unique())
MODEL_TO_MAKE = dict(zip(_models.model, _models.make))
YEARS = sorted(_models.year.unique())


def get_collection(embedding_function=None):
    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    if embedding_function:
        return client.get_collection(COLLECTION, embedding_function=embedding_function)
    return client.get_collection(COLLECTION)


def extract_car(question):
    """Find make, model, and year mentioned in the question. Rule-based on purpose:
    simple, free, and easy to debug. Anything not mentioned comes back as None."""
    q = question.lower()
    find = lambda words: next((w for w in words if re.search(rf"\b{w.lower()}\b", q)), None)
    make, model, year = find(MAKES), find(MODEL_TO_MAKE), find(YEARS)
    if model and not make:
        make = MODEL_TO_MAKE[model]          # "my Vale" implies Norvik
    return {"make": make, "model": model, "year": year}


def build_where(car):
    """Chroma filter. Each field matches the stated value OR "all", so general chunks
    (symptom guides, FAQs, bulletins that cover every year) are not filtered out."""
    conditions = [{"$or": [{field: value}, {field: "all"}]}
                  for field, value in car.items() if value]
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def retrieve(question, k=5, use_filter=True, collection=None):
    """Return (detected car, list of hits). Each hit has rank, source, distance, text, meta.
    Distance is cosine distance: smaller means closer in meaning."""
    col = collection or get_collection()
    car = extract_car(question) if use_filter else {"make": None, "model": None, "year": None}
    res = col.query(query_texts=[question], n_results=k, where=build_where(car))
    hits = [
        {"rank": i + 1, "source": m["source"], "doc_id": m["doc_id"], "distance": d, "text": t, "meta": m}
        for i, (m, d, t) in enumerate(zip(res["metadatas"][0], res["distances"][0], res["documents"][0]))
    ]
    return car, hits


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-filter", action="store_true", help="search all chunks, ignore the car")
    a = ap.parse_args()
    car, hits = retrieve(a.question, k=a.k, use_filter=not a.no_filter)
    print("Detected car:", car)
    for h in hits:
        snippet = h["text"].replace("\n", " ")[:90]
        print(f"{h['rank']}. dist={h['distance']:.3f}  {h['source']}\n     {snippet}...")
