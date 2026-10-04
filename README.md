# CarAssist

A RAG assistant for a dealer parts-and-service desk. A customer or service advisor describes a car problem or asks a how-to question, and CarAssist explains the likely cause, the relevant procedure, and the part that fits that exact make, model, and year, with citations.

> All makes, models, specs, and part numbers in this repo are **fictional**. This is a learning project, not real automotive guidance.

**Status:** the full pipeline is built (data, documents, ingestion, retrieval, grounded generation) along with the golden test set and the evaluation runner. Baseline scores, the hosted demo, and the retrieval improvements are next (see [Roadmap](#roadmap)).

**Live demo:** _[link, add after deploy]_

## Contents

- [Use case](#use-case) · [Goal](#goal) · [Success criteria](#success-criteria) · [Scope](#scope)
- [Architecture](#architecture)
- [Files and what they do](#files-and-what-they-do)
- [Data](#data) · [Evaluation](#evaluation)
- [Setup](#setup) · [Known limitations](#known-limitations) · [Roadmap](#roadmap)

## Use case

- **Users:** service advisors and customers at a dealer service desk.
- **Questions it handles:** procedures (changing a tire, jump-starting), warning lights, symptoms ("grinding when I brake"), maintenance, and "which part do I need?"
- **Knowledge sources:** manual sections, troubleshooting articles, service bulletins, service tickets, advisor-customer conversations, and FAQs.

## Goal

Give accurate, cited answers for a specific make, model, and year. When the answer isn't in the data, say so or ask a clarifying question instead of guessing.

## Success criteria

| Area | Target |
|---|---|
| Retrieval | The correct source document is in the top 3 for _[X]%_ of golden questions |
| Grounding | Answers use only retrieved content, with a correct citation |
| Fitment | The part number returned fits the stated make, model, and year |
| No invention | Never produces a part number that isn't in the data |
| Honesty | Says "not found" on unanswerable questions; asks which car when none is given |
| Safety | Brake and steering symptoms always carry a safety warning |

## Scope

**In scope:** document RAG with citations, symptom-to-likely-cause reasoning grounded in the documents, part identification.

**Out of scope for now:** pricing, stock, placing orders, and real manufacturer data. The parts catalog is a structured table reserved for a later lookup tool.

## Architecture

```mermaid
flowchart TB
  subgraph BUILD["Build time (done)"]
    CSV["data/*.csv<br/>models, parts, fitment, symptoms<br/>(source of truth)"]
    GEN["src/generate_docs.py"]
    LLM["Claude API<br/>conversations and tickets"]
    DOCS["docs/*.md<br/>about 125 documents with metadata"]
    ING["src/ingest.py<br/>parse, chunk, embed"]
    IDX[("index/chroma<br/>vectors, text, metadata")]
    CSV --> GEN
    GEN -->|templates| DOCS
    GEN -->|facts in prompt| LLM
    LLM -->|checked for part numbers| DOCS
    DOCS --> ING
    ING --> IDX
  end

  subgraph RUN["Answering (built)"]
    Q["User question"] --> RET["Retrieval<br/>top-k and make/model/year filter"]
    IDX --> RET
    RET --> ANSW["src/answer.py + prompts.py<br/>Claude: grounded answer<br/>citations, abstain, safety warning"]
    ANSW --> OUT["Cited answer"]
  end

  subgraph EVAL["Evaluation"]
    GOLD["evals/golden_v1.csv"] --> VAL["evals/validate_golden.py"]
    CSV -.-> VAL
    DOCS -.-> VAL
    GOLD --> RCHK["evals/retrieval_check.py"]
    RCHK --> RET
    GOLD --> RUNNER["evals/run_evals.py<br/>rules + LLM judge"]
    RUNNER --> ANSW
  end
```

**Build time** turns the tables into documents and the documents into a searchable index. It runs once, and again whenever the data changes. **Answering** runs on every question. **Evaluation** checks that the answer key is consistent and (once the runner exists) scores the system against it.

Part numbers and fitment need exact answers, so the parts catalog stays a structured table instead of being searched by similarity.

## Files and what they do

### `data/*.csv` and `data/README.md`
- **What:** four tables that are the single source of truth: `models.csv`, `parts_catalog.csv`, `fitment.csv`, `symptoms.csv`. `data/README.md` is the data dictionary and lists every planted trap.
- **Why it matters:** documents are generated from these tables and every eval answer traces to a cell, so the answer key never depends on a model's judgment.
- **Edit when:** adding a model, part, symptom, or changing a fact. Then regenerate documents and re-run the validator.

### `src/generate_docs.py`
- **What:** reads the four tables and writes the text documents into `docs/`.
- **Two modes:**
  - Default (no API): 107 templated documents. 84 manual sections (7 types for each of the 12 model-years), 11 symptom articles, 5 service bulletins, and 7 FAQs. Facts come straight from the tables.
  - `--llm`: 20 customer-advisor conversations and service tickets written by Claude. The prompt contains only the facts for that case, and a checker rejects any output missing the correct part number, mentioning a different real part, or inventing a part-like code. Up to 3 retries. Failures are written to `docs/_rejected/`.
  - `--llm --dry-run` prints one prompt without calling the API.
- **Run:** `python src/generate_docs.py` then optionally `python src/generate_docs.py --llm` (needs `ANTHROPIC_API_KEY` in `.env`).
- **Key decisions:** templates for facts that must be exact; an LLM only for natural-sounding text, with automatic checking. LLM output is non-deterministic, so the committed `docs/` folder is the frozen corpus.
- **Limits:** the checker validates part numbers only. Other facts in LLM text are spot-checked by hand.

### `src/ingest.py`
- **What:** turns `docs/` into a searchable index. It runs once up front, not per question.
- **Steps:**
  1. **Parse:** separate each document's front matter (make, model, year, doc type) from its text. Folders starting with `_` are skipped.
  2. **Chunk:** documents under 1,500 characters stay as one chunk. Longer ones split on blank lines, never mid-table. Every chunk starts with its title so it identifies the car on its own.
  3. **Metadata:** each chunk keeps make, model, year, doc type, doc ID, and file path. Part numbers on LLM-written documents are deliberately left out, since they are the expected answers.
  4. **Embed and store:** Chroma's default model (all-MiniLM-L6-v2, local and free) turns each chunk into a vector and saves it with the text and metadata in `index/chroma`. The index is deleted and rebuilt each run, so re-running never duplicates.
- **Run:** `python src/ingest.py`. It prints chunk counts by document type.
- **Known weakness:** near-duplicate documents (Tern vs. Vale) embed almost identically, and exact codes like part numbers carry little meaning for an embedding model. This motivates metadata filtering and, later, hybrid search.

### `evals/golden_v1.csv`
- **What:** 29 test questions in plain customer language. Each has an expected answer, expected source documents, expected part number, and expected behavior (`answer`, `clarify`, or `abstain`).
- **Types:** direct, near-duplicate, year trap, cross-document, ambiguous, unanswerable, out-of-scope, safety. Seven are marked `holdout` and are not used while tuning.
- **Columns:** `stated_make/model/year` record what the question itself says (blank when not stated), which shows whether a filter could be applied.
- **Versioning:** scores from different versions are not comparable. Add new versions as `golden_v2.csv` and re-score earlier configurations on them.

### `evals/validate_golden.py`
- **What:** a sanity check of the answer key. It does not run RAG or call any model.
- **Checks:** IDs are unique; every expected source file exists; every expected part is in the catalog and fits the stated car; the stated car exists (except for unanswerable questions); `expected_behavior` is valid.
- **Run:** `python evals/validate_golden.py` from the repo root. Prints counts by type and split, then `All checks passed.` or a list of errors.
- **Re-run when:** tables, documents, or golden questions change. Chunking and embedding changes are scored by the eval runner instead.

### `src/retrieve.py`
- **What:** given a question, finds the most relevant chunks in the Chroma index. It runs on every question.
- **Steps:**
  1. **Detect the car:** find the make, model, and year in the question by matching against `models.csv` ("my Vale" implies Norvik). Rule-based on purpose: free, simple, and easy to debug.
  2. **Build a filter:** for each detected field, match the stated value **or** `"all"`, so general chunks (symptom guides, FAQs, bulletins that cover every year) are not filtered out.
  3. **Embed and search:** the question is embedded with the same model as the chunks, and Chroma returns the k nearest chunks (default 5) among those that passed the filter.
  4. **Return hits:** rank, source file, cosine distance (smaller is closer), text, and metadata.
- **Run:** `python src/retrieve.py "lug nut torque on my 2024 Norvik Vale?"`. Add `--no-filter` to search every chunk and see what the filter changes.

```mermaid
flowchart TB
  Q["Question"] --> D["Detect the car<br/>make, model, year"]
  D --> F["Build filter<br/>car value or 'all'"]
  F --> S["Embed question and search<br/>nearest chunks inside filter"]
  IDX[("Chroma index")] --> S
  S --> H["Top-k chunks<br/>source, distance, text"]
```

- **Why the filter matters:** Tern and Vale documents differ by one number, so they embed almost identically. Without the filter, wrong-car chunks can land in the top results. With it, they are never searched.
- **What the filter cannot fix:** the right car's own documents still compete with each other (for example a symptom guide outranking a torque document). That is what hybrid search and reranking address later.
- **Limits:** a year that is not in the table (such as "2023 Cora") or a misspelled model name is not detected, so no filter is applied. Production systems often use an LLM for this step.

### `evals/retrieval_check.py`
- **What:** scores retrieval only, with no Claude calls. For each golden question it asks: did the right source document show up in the top k, and how high?
- **Steps:**
  1. Load the golden questions, keep the dev split (holdout only with `--include-holdout`), and skip questions with no expected source (unanswerable, out of scope), since those are judged on the answer.
  2. Run `retrieve()` for each question.
  3. Find the rank of the first expected source in the results.
  4. Report scores by question type, and list every miss (right source not in the top 3) with what came back instead.
- **Metrics:**
  - `hit@k`: share of questions where any expected source is in the top k.
  - `MRR`: mean of 1/rank of the first expected source (1.0 means always first, 0 means never found).
- **Run:**
  - `python evals/retrieval_check.py --tag baseline`
  - `python evals/retrieval_check.py --no-filter --tag nofilter` to compare with the filter off
  - `--k 10` changes how many results are retrieved. `--tag name` saves `evals/results/retrieval_name.csv`.

```mermaid
flowchart TB
  G["golden_v1.csv"] --> P["Keep dev questions<br/>skip those with no expected source"]
  P --> R["retrieve() for each question"]
  R --> C["Find rank of first expected source"]
  C --> M["hit@1, hit@3, hit@k, MRR<br/>by question type"]
  C --> X["Miss report<br/>right source not in top 3"]
```

Worked example of the scores (three questions, right source at rank 1, rank 3, and not found):

| Question | hit@1 | hit@3 | 1/rank |
|---|---|---|---|
| A (rank 1) | yes | yes | 1.00 |
| B (rank 3) | no | yes | 0.33 |
| C (not found) | no | no | 0.00 |
| **Average** | **0.33** | **0.67** | **0.44 (MRR)** |

- **Hit means any expected source:** a cross-document question counts as a hit if either of its sources shows up. A stricter "all sources found" metric can be added later.
- **Small sets are noisy:** with about 20 scored questions, one flip moves a score by around 5 points. Read the misses, not just the averages.

### `src/answer.py`
- **What:** the generation step. It takes a question, retrieves chunks, gives them to Claude with strict rules, and returns an answer with citations. It runs on every question.
- **Steps (`answer()`):**
  1. **Retrieve:** call `retrieve()` for the top 5 chunks, filtered by the car in the question.
  2. **Build the prompt:** number each chunk `[1]`, `[2]`, and so on, with its source file, so Claude can cite by number. The numbered context and the question form the user message.
  3. **Ask Claude:** the rules go in as the system prompt, and the user message is the context plus the question.
  4. **Read the citations:** keep only the text blocks of the response (some models return a "thinking" block first), pull the `[n]` numbers out of the answer, and map them back to real source files. A number that does not exist is ignored.
- **Run:** `python src/answer.py "lug nut torque on my 2024 Norvik Vale?"`. Add `--show-context` to see exactly what Claude was given, `--no-filter` to turn the car filter off, and `--model claude-sonnet-5-5` to compare models.
- **Key decisions:**
  - Rules live in a separate file (`prompts.py`) so they can be read and changed without touching code.
  - Chunks are numbered so citations are checkable, not free-form.
  - The default model is a small, cheap one (Haiku). Stronger models may follow the rules better, which is a comparison worth running.
- **Gotchas found while building:**
  - Newer models can return a thinking block before the answer, so reading `content[0]` fails. Read only blocks of type `text`.
  - Some models reject a `temperature` setting, so answers can vary slightly between runs. `max_tokens` is set to 1500 so thinking does not use up the answer budget.
- **Limits:** a citation shows which chunk Claude says it used, not that the chunk really supports the claim. Checking that needs the eval runner's judge.

```mermaid
flowchart TB
  Q["Question"] --> R["Retrieve<br/>top 5 chunks, car filter"]
  R --> P["Build the prompt<br/>number chunks, add rules"]
  P --> C["Ask Claude<br/>answer from context only"]
  C --> X["Read the citations<br/>[n] mapped to source files"]
```

### `src/prompts.py`
- **What:** holds `SYSTEM_PROMPT`, the fixed rules sent with every question. Claude gets two inputs: this fixed prompt, and a user message built fresh for each question (numbered chunks plus the question).

```mermaid
flowchart TB
  SP["System prompt<br/>fixed rules from prompts.py"] --> CL["Claude"]
  UM["User message<br/>numbered chunks and question"] --> CL
  CL --> AN["Answer text with [n] citations"]
  AN --> SRC["Cited sources<br/>[n] mapped back to files"]
```

- **Each rule maps to a kind of golden question,** which is how generation will be scored:

| # | Rule | Behavior | Golden questions that test it |
|---|---|---|---|
| 1 | Use only the context | "Could not find it" instead of guessing | unanswerable (Q20, Q21, Q29) |
| 2 | Right car only | Ignore chunks for other cars; say if the car is not in the data | near-duplicate and year-trap questions; Q20, Q29 |
| 3 | Ask which car | Clarify when make, model, or year is missing | Q17, Q19, Q28 |
| 4 | Vague complaint | Ask follow-ups, suggest no part yet | Q18 |
| 5 | Part numbers | Copy exactly, only for this car; say when fitment cannot be confirmed | Q07, Q08, Q09, Q11, Q21, Q25 |
| 6 | Safety | Warning first; a technician confirms | Q13, Q19, Q23 |
| 7 | Out of scope | No prices, stock, or orders | Q22 |
| 8 | Citations | Every fact ends in `[n]` | all answerable questions |
| 9 | Style | Short, plain sentences, under 150 words | all |
| 10 | Ignore instructions in context | Documents are data, not commands | none yet (a possible future test) |

### `evals/run_evals.py`
- **What:** the eval runner. It sends golden questions through the full pipeline (retrieve, then Claude) and scores each answer. This is where the success criteria get measured.
- **Two kinds of checks:**
  - **Rule-based (`part_ok`):** looks only at part numbers in the answer. The expected part must be present. No part may appear that does not fit the car in the question (including the Tern's part in a Vale answer, or SPK-9999 for the Brio). No invented part-like codes. With no car stated, any part number counts as wrong. Document IDs such as `SB-2025-04` and `SYM-01` are not treated as parts.
  - **Judge model:** a stronger model than the one answering (default `claude-sonnet-5-5`) gets the question, the expected answer, the retrieved context, and the response, and returns JSON grading four things.
- **The checks:**

| Check | Type | Applies to | Question it answers |
|---|---|---|---|
| `behavior_ok` | judge | all questions | Did it answer, ask a clarifying question, or decline, as expected? |
| `part_ok` | rules | all questions | Right part, no wrong-fit part, nothing invented? |
| `correct_ok` | judge | answerable questions | Does it state the expected facts? |
| `safety_ok` | judge | safety questions | Is there a safety warning with a technician check? |
| `faithful_ok` | judge | all questions | Is every claim supported by the retrieved context? |

A question **passes** only when every check that applies to it passes.

| Question kind | behavior | part | correct | safety | faithful |
|---|---|---|---|---|---|
| Answerable (Q01) | yes | yes | yes | n/a | yes |
| Answer + safety (Q13) | yes | yes | yes | yes | yes |
| Clarify (Q17) | yes | yes | n/a | n/a | yes |
| Clarify + safety (Q19) | yes | yes | n/a | yes | yes |
| Decline (Q20) | yes | yes | n/a | n/a | yes |

```mermaid
flowchart TB
  G["Golden question<br/>dev set, N repeats"] --> P["Run the pipeline<br/>retrieve, then Claude"]
  P --> R["Part check<br/>rules on part numbers"]
  P --> J["Judge model<br/>behavior, facts, safety, support"]
  R --> V["Pass or fail<br/>table, failures, CSV"]
  J --> V
```

- **Output:** pass rates per check and per question type, then a failure list (answer, notes, retrieval rank), and optionally a CSV in `evals/results/`.
- **Run:**
  - `python evals/run_evals.py --ids Q01,Q13,Q21` to try a few first
  - `python evals/run_evals.py --repeats 3 --tag baseline` for the dev set (answers vary between runs, so repeat)
  - `python evals/run_evals.py --no-filter --repeats 3 --tag nofilter` to see what the car filter is worth
  - Other flags: `--k`, `--answer-model`, `--judge-model`, `--include-holdout` (final check only)
- **Reading a failure:** there are three possible causes. Retrieval missed (check `retrieval_rank`), the prompt needs a better rule, or the judge is wrong. Work out which before changing anything.
- **Limits:**
  - The judge can be wrong, so spot-check its verdicts.
  - The part rule is strict: "not the Tern's BP-4417" in a Vale answer counts as a failure.
  - About 20 scored questions means one flip moves a rate by about 5 points, so repeat runs and read the failures, not just the averages.
  - The default answer model and the judge are different models on purpose, so the judge is not grading its own style.

### `requirements.txt`, `.gitignore`, `.env`
- `requirements.txt`: Python dependencies (pandas, anthropic, python-dotenv, chromadb).
- `.gitignore`: keeps secrets (`.env`), virtual environments, caches, `index/`, and `docs/_rejected/` out of the repo.
- `.env`: holds `ANTHROPIC_API_KEY`. Never commit it.

### Coming next
- `app/`: Streamlit chat page with password protection and cost caps.

## Data

| Table | Rows | Contents |
|---|---|---|
| `models.csv` | 12 | 2 makes, 3 models each, 2 model years |
| `parts_catalog.csv` | 31 | part number, name, category, position, price, stock |
| `fitment.csv` | 109 | which part fits which make-model-year |
| `symptoms.csv` | 11 | plain-language symptoms, ranked causes, checks, safety flag |

**Deliberate traps** to stress retrieval:
- Near-duplicate models (Norvik Tern vs. Vale: same specs except lug torque, part numbers one digit apart).
- Year differences on the same model (Aria battery type, Brio spare tire, Cora lug torque and air filter).
- A part that fits only some years, a catalog part that fits no model, and vague or unanswerable questions.

## Evaluation

**Results (v1 metric):** 22 dev questions, 3 runs each. Answers by `claude-haiku-4-5-20251001`, judged by `claude-sonnet-5-5`. The 7 holdout questions have not been run.

Retrieval scores come from `python evals/retrieval_check.py --tag <name>`.

| Change | hit@3 | MRR | Notes |
|---|---|---|---|
| baseline (car filter on, k=5) | 0.85 | 0.74 | hit@1 0.60, hit@5 0.95. Cross-document questions are weakest (hit@3 0.50) |
| car filter off | 0.80 | 0.59 | hit@1 0.40, hit@5 0.85. Near-duplicate MRR drops from 0.88 to 0.58 |

Change one thing at a time and log it here.

Answer scores come from `python evals/run_evals.py --repeats 3 --tag <name>` (pass rate per check; "passed" needs every applicable check).

| Change | passed | behavior | part | correct | safety | faithful | Notes |
|---|---|---|---|---|---|---|---|
| baseline (filter on, k=5) | 0.88 | 1.00 | 0.95 | 1.00 | 1.00 | 0.92 | 8 of 66 rows fail, all from Q24, Q25, Q27 |
| car filter off | 0.79 | 0.97 | 0.91 | 0.92 | 1.00 | 0.91 | 14 of 66 rows fail. Safety questions pass 0 of 3 |

**Findings**
- **The car filter helps.** Pass rate rises from 0.79 to 0.88, hit@1 from 0.40 to 0.60, and near-duplicate MRR from 0.58 to 0.88. Without it, wrong-car chunks pushed the model to ask an unnecessary clarifying question on a simple tire-pressure question (Q24).
- **Retrieval is not the bottleneck for answers.** The right source was in the top 5 for 19 of 20 scored questions, and every behavior, correctness, and safety check passed with the filter on.
- **Two of the three failing questions are the same generation problem:** correct answers padded with advice that the retrieved text does not support (Q24: re-check pressure after 50 miles, check when cold; Q27: an electrical-connection cause and an offer to replace the bulb). That is a prompt fix.
- **The third (Q25) is a metric problem, not a system problem.** The answer was right (MR-8802) but also named the Tern's MR-8801 as a contrast, which the strict part rule counts as a failure. All 3 baseline and all 6 filter-off `part_ok` failures were this kind of mention, with nothing missing and nothing invented.
- **Caveat:** 22 questions times 3 runs is small, so each question moves the pass rate by about 4.5 points. The direction is consistent across question types, but treat exact numbers as approximate.

## Repo structure

```
carassist/
  data/        # CSV tables (ground truth) + data dictionary
  docs/        # generated text documents (the corpus)
  src/         # generate_docs.py, ingest.py, retrieve.py, answer.py, prompts.py
  evals/       # golden_v1.csv, validate_golden.py, retrieval_check.py, run_evals.py
  app/         # Streamlit app (to come)
  README.md
  requirements.txt
```

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# only needed for LLM-written docs and generation; never commit this file
echo 'ANTHROPIC_API_KEY=your-key' > .env

python src/ingest.py              # builds index/chroma (first run downloads the embedding model)
python evals/validate_golden.py   # checks the answer key
python evals/retrieval_check.py --tag baseline   # scores retrieval on the dev questions
python evals/run_evals.py --repeats 3 --tag baseline   # scores full answers (rules + LLM judge)
python src/retrieve.py "lug nut torque on my 2024 Norvik Vale?"   # retrieval only
python src/answer.py "lug nut torque on my 2024 Norvik Vale?"     # full answer with citations
```

## Known limitations

- All data is fictional and generated, so real-world messiness (scanned PDFs, tables, inconsistent naming) is not represented. Parsing real PDFs is a planned extension.
- Templated documents are repetitive by design, which makes near-duplicate retrieval harder. Real manuals read more naturally.
- The LLM-document checker validates part numbers only. Other facts in those documents are spot-checked by hand.
- Every document is currently one chunk, so chunking strategy is not yet a meaningful variable.
- Car detection is rule-based: unknown years or misspelled model names are not detected, so no filter is applied.
- The car filter removes wrong-car chunks but does not help when the right car's own documents compete with each other.
- Retrieval scores count a hit if any expected source is found, which is lenient for cross-document questions.
- A citation shows which chunk Claude says it used, not that the chunk supports the claim. Answers can also vary slightly between runs because temperature is not set.
- Behavior differs by model (for example thinking blocks, rule-following), so scores are only comparable within one model.
- The LLM judge can be wrong, and the part-number rule is deliberately strict (it also flags a wrong-fit part mentioned only as a contrast). With about 20 scored questions, results are noisy, so runs are repeated and failures are read by hand.
- _[Add retrieval and generation failures here as you find them]_

## Roadmap

- [x] Data tables and data dictionary
- [x] Document generation (templated plus LLM with checks)
- [x] Golden test set and validator
- [x] Ingestion (chunk, embed, Chroma)
- [x] Retrieval with metadata filtering, and a retrieval scoring script
- [x] Grounded generation with citations, abstention, and safety warnings
- [x] Eval runner (rule checks and LLM judge)
- [x] Baseline results recorded in the Evaluation tables (retrieval and answers, filter on and off)
- [ ] Fix the eval and the prompt, then re-run as `baseline_v2`: let the judge decide whether a wrong-fit part is recommended (not just mentioned), exempt rule-required safety warnings from the faithfulness check, and tell the model not to add advice beyond the context
- [ ] Streamlit app with password protection and cost caps, deployed
- [ ] Hybrid search, reranking, and query rewriting
- [ ] Embedding model comparison
- [ ] Rebuild with LangChain and compare
- [ ] LangGraph agent combining RAG with parts-catalog lookup
- [ ] Expose the knowledge base through an MCP server
- [ ] Add a PDF-parsing step with realistic PDFs
