"""
Step 6: GENERATION. Retrieve chunks, hand them to Claude with strict rules, return a cited answer.

    question -> retrieve (top-k + car filter) -> numbered CONTEXT -> Claude -> answer with [n] citations

Try it from the repo root (needs ANTHROPIC_API_KEY in .env and a built index):
    python src/answer.py "lug nut torque on my 2024 Norvik Vale?"
    python src/answer.py "my brakes are grinding, what do I need" --show-context
"""
import argparse
import os
import re
from pathlib import Path

from dotenv import load_dotenv

import retrieve
from prompts import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
MODEL = os.environ.get("CARASSIST_ANSWER_MODEL", "claude-haiku-4-5-20251001")


def build_context(hits):
    """Number each retrieved chunk so Claude can cite it as [1], [2], ..."""
    return "\n\n".join(f"[{h['rank']}] (source: {h['source']})\n{h['text']}" for h in hits)


def answer(question, k=5, use_filter=True, model=MODEL, client=None, collection=None):
    car, hits = retrieve.retrieve(question, k=k, use_filter=use_filter, collection=collection)
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    user_message = f"CONTEXT:\n{build_context(hits)}\n\nQUESTION: {question}"
    msg = client.messages.create(
        model=model, max_tokens=1500,
        system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user_message}],
    )
    # Some models return a "thinking" block before the answer, so keep only the text blocks.
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not text:
        text = "(No answer text returned. Try raising max_tokens.)"
    cited_numbers = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
    cited_sources = [hits[n - 1]["source"] for n in cited_numbers if 1 <= n <= len(hits)]
    return {"question": question, "car": car, "answer": text,
            "cited_sources": cited_sources, "hits": hits, "model": model}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--show-context", action="store_true", help="print the chunks Claude was given")
    a = ap.parse_args()
    r = answer(a.question, k=a.k, use_filter=not a.no_filter, model=a.model)
    print(f"Car detected: {r['car']}   Model: {r['model']}\n")
    if a.show_context:
        print(build_context(r["hits"]), "\n" + "-" * 60)
    print(r["answer"])
    print("\nSources cited:")
    for s in r["cited_sources"] or ["(none)"]:
        print("  ", s)
