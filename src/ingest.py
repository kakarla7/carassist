"""
Step 4: INGEST. Load docs/, split into chunks, embed, and store in Chroma.

Pipeline:  docs/*.md  ->  parse  ->  chunk  ->  embed  ->  Chroma (index/chroma)

Run from the repo root:   python src/ingest.py
Re-running rebuilds the index from scratch, so it is safe to run again.
"""
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX_DIR = ROOT / "index" / "chroma"
COLLECTION = "carassist"

MAX_CHARS = 1500  # docs shorter than this stay as ONE chunk

# Metadata we store with each chunk. Deliberately leaves out part_number and
# symptom_id from LLM-written docs: those are the expected answers, and the
# retriever must find them by meaning, not read them from a label.
KEEP_META = ["doc_id", "doc_type", "make", "model", "year", "safety_flag"]


# ---------- 1. parse ----------
def parse_doc(path):
    """Split a doc into (metadata dict, title, body text)."""
    text = path.read_text()
    _, front, rest = text.split("---", 2)           # front matter sits between the first two ---
    meta = {}
    for line in front.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    rest = rest.strip()
    title, _, body = rest.partition("\n")            # first line is "# Title"
    return meta, title.lstrip("# ").strip(), body.strip()


def load_docs():
    for path in sorted(DOCS.glob("*/*.md")):
        if path.parent.name.startswith("_"):         # skip docs/_rejected
            continue
        meta, title, body = parse_doc(path)
        yield path, meta, title, body


# ---------- 2. chunk ----------
def chunk_body(title, body):
    """Short docs stay whole. Long docs split on blank lines, never mid-paragraph
    (so a parts table is never cut in half). Every chunk starts with the title,
    so it still says which car it is about when read on its own."""
    if len(body) <= MAX_CHARS:
        return [f"{title}\n\n{body}"]
    chunks, current = [], ""
    for block in body.split("\n\n"):
        if current and len(current) + len(block) > MAX_CHARS:
            chunks.append(f"{title}\n\n{current.strip()}")
            current = ""
        current += block + "\n\n"
    if current.strip():
        chunks.append(f"{title}\n\n{current.strip()}")
    return chunks


# ---------- 3. embed + store ----------
def build_index(embedding_function=None):
    """embedding_function=None uses Chroma's default: all-MiniLM-L6-v2 (small, local, free).
    To compare embedding models later, pass a different one here."""
    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)         # rebuild from scratch
    kwargs = {"embedding_function": embedding_function} if embedding_function else {}
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"}, **kwargs)

    ids, texts, metas = [], [], []
    for path, meta, title, body in load_docs():
        for i, chunk in enumerate(chunk_body(title, body)):
            ids.append(f"{meta['doc_id']}::{i}")
            texts.append(chunk)
            m = {k: meta[k] for k in KEEP_META if k in meta}
            m.update(source=str(path.relative_to(ROOT)), chunk_index=i)
            metas.append(m)

    col.add(ids=ids, documents=texts, metadatas=metas)   # Chroma embeds the texts here
    return col, metas


if __name__ == "__main__":
    col, metas = build_index()
    print(f"Indexed {col.count()} chunks from {len({m['doc_id'] for m in metas})} documents")
    by_type = {}
    for m in metas:
        by_type[m["doc_type"]] = by_type.get(m["doc_type"], 0) + 1
    for t, n in sorted(by_type.items()):
        print(f"  {t}: {n}")
