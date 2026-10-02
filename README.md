# CarAssist

A RAG assistant for a dealer parts-and-service desk. A customer or service advisor describes a car problem or asks a how-to question, and CarAssist explains the likely cause, the relevant procedure, and the part that fits that exact make, model, and year, with citations.

> All makes, models, specs, and part numbers in this repo are **fictional**. This is a learning project, not real automotive guidance.

**Live demo:** [link, add after deploy]

## Use case

- **Users:** service advisors and customers at a dealer service desk.
- **Questions it handles:** procedures (changing a tire, jump-starting), warning lights, symptoms ("grinding when I brake"), maintenance, and "which part do I need?"
- **Knowledge sources:** manual sections, troubleshooting articles, service bulletins, past service tickets, advisor-customer conversations, FAQs.

## Goal

Give accurate, cited answers for a specific make, model, and year. When the answer isn't in the data, say so or ask a clarifying question instead of guessing.

## Success criteria

| Area | Target |
|---|---|
| Retrieval | The correct source chunk is in the top 3 for [X]% of test questions |
| Grounding | Answers use only retrieved content, with a correct citation |
| Fitment | The part number returned fits the stated make, model, and year |
| No invention | Never produces a part number that isn't in the data |
| Honesty | Says "not found" on unanswerable questions; asks which model when none is given |
| Safety | Brake and steering symptoms always carry a safety warning |

## Scope

**In scope (this build):** document RAG with citations, symptom-to-likely-cause reasoning grounded in the documents, part identification from the catalog.

**Out of scope for now:** pricing, stock, and placing orders; real manufacturer data.

## How it works

1. **Ingest:** generated text documents with metadata (make, model, year, doc type).
2. **Chunk:** by document structure, keeping metadata on every chunk.
3. **Embed and store:** [embedding model] into [Chroma].
4. **Retrieve:** top-k with metadata filtering by make, model, and year.
5. **Generate:** Claude answers from retrieved context only, with citations.

The parts catalog stays a structured table, because part numbers and fitment need exact lookups, not fuzzy search.

## Data

- `data/` holds the source-of-truth tables (CSV): models, parts catalog, symptoms.
- `docs/` holds text documents generated from those tables and spot-checked against them.
- [N] make-model-year combinations, [N] document types.
- Evaluation questions and expected answers come from the tables.

## Repo structure

```
carassist/
  data/        # CSV tables (ground truth)
  docs/        # generated text documents
  src/         # pipeline code
  evals/       # test questions and results
  app/         # Streamlit app
  README.md
```

## Evaluation

Test set of [N] questions, including direct lookups, wrong-model traps, fitment traps, vague symptoms, unanswerable questions, and safety cases. Results: [add after first run].

## Known limitations

[Fill in as you find them: retrieval misses, near-duplicate confusion, etc.]

## Roadmap

- [ ] Plain-Python RAG pipeline and eval harness
- [ ] Hybrid search, reranking, query rewriting
- [ ] Embedding model comparison
- [ ] Rebuild with LangChain and compare
- [ ] LangGraph agent combining RAG with parts-catalog lookup
- [ ] Expose the knowledge base through an MCP server

## Setup

[Add after the code exists: Python version, install steps, how to run locally. Never commit API keys; use environment variables or Streamlit secrets.]