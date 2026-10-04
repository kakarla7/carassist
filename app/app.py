"""
CarAssist: Streamlit front end.

Run locally from the repo root:   streamlit run app/app.py
Local settings go in .env; hosted settings go in Streamlit secrets:
    ANTHROPIC_API_KEY   required
    APP_PASSWORD        required (the app stays locked without it)
    MAX_QUESTIONS       optional, questions per visitor session (default 10)
    GLOBAL_LIMIT        optional, questions per server run across all visitors (default 300)
    REPO_URL            optional, shows a "view the code" link
"""
import hmac
import os
import re
import sys
import traceback
from pathlib import Path

try:  # Streamlit Cloud ships an old sqlite that Chroma rejects; this swaps in a newer one if installed
    import pysqlite3  # noqa: F401
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

st.set_page_config(page_title="CarAssist", page_icon="🚗", layout="wide")


def secret(name, default=None):
    """Read from Streamlit secrets (hosted) or the environment / .env (local)."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


if secret("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = str(secret("ANTHROPIC_API_KEY"))

import answer as answer_mod  # noqa: E402  (after the key is set)
import ingest  # noqa: E402
import retrieve  # noqa: E402

MAX_QUESTIONS = int(secret("MAX_QUESTIONS", 10))
GLOBAL_LIMIT = int(secret("GLOBAL_LIMIT", 300))
MAX_CHARS = 300

EXAMPLES = [
    "What torque do I use on the lug nuts for my 2024 Norvik Vale?",
    "My brakes are grinding on my 2025 Arden Aria. What's wrong and what part do I need?",
    "Where is the battery on a 2024 Arden Cora?",
    "Does my 2024 Arden Brio have a spare tire?",
    "Which part do I need for the left mirror glass on a 2025 Norvik Tern?",
    "My car is making a noise",
]


# ---------- access control ----------
def require_password():
    expected = secret("APP_PASSWORD")
    if not expected:
        st.error("The app is locked because no APP_PASSWORD is set. Add it to your .env file (local) "
                 "or to Streamlit secrets (hosted).")
        st.stop()
    if st.session_state.get("authed"):
        return
    st.title("CarAssist")
    st.caption("A demo for a dealer parts-and-service desk. Enter the password you were given.")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter")
    if submitted:
        if hmac.compare_digest(pw.encode(), str(expected).encode()):
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


@st.cache_resource
def global_counter():
    return {"n": 0}


# ---------- data ----------
@st.cache_resource(show_spinner="Setting up the knowledge base (first load only)...")
def get_collection():
    """Build the index in memory when the app starts (a few seconds for about 125 short documents).
    This avoids reading an index folder written by a different Chroma version, and hosted disks
    start empty anyway."""
    col, _ = ingest.build_index(persist=False)
    return col


@st.cache_data
def load_cars():
    m = pd.read_csv(ROOT / "data" / "models.csv", dtype=str)
    cars = m.groupby(["make", "model"]).agg(
        years=("year", lambda s: ", ".join(sorted(s))), body=("body_type", "first"),
        eng=("engine", "first")).reset_index()  # not "engine": agg() treats that name as a setting
    cars.columns = ["Make", "Model", "Years", "Body", "Engine"]
    return cars


def car_status(text):
    """Tell the visitor whether the car they typed is covered. Returns (level, message)."""
    cars = load_cars()
    car = retrieve.extract_car(text)
    make, model = car["make"], car["model"]
    typed_years = re.findall(r"\b(?:19|20)\d{2}\b", text)
    if model:
        row = cars[cars.Model == model].iloc[0]
        have = row.Years.split(", ")
        if typed_years and not all(y in have for y in typed_years):
            return "warning", (f"We cover the {row.Make} {model} for {row.Years} only. "
                               f"Not covered: {', '.join(y for y in typed_years if y not in have)}.")
        if typed_years:
            return "success", f"Covered: {typed_years[0]} {row.Make} {model}."
        return "info", f"We cover the {row.Make} {model} for {row.Years}. Add the year for exact answers."
    if make:
        models = ", ".join(cars[cars.Make == make].Model)
        return "info", f"We cover {make}. Models covered: {models}. Add the model and year."
    return "warning", "That doesn't match a car in the data. See the list of covered cars on the left."


# ---------- chat helpers ----------
def build_query(messages, new):
    """If the assistant just asked a question and the reply is short, treat the reply as an
    answer to it by joining it to the earlier question. Otherwise the new text stands alone."""
    if messages and messages[-1]["role"] == "assistant" and "?" in messages[-1]["content"] \
            and len(new.split()) <= 12:
        return f"{messages[-1]['meta']['query']} {new}"
    return new


def label(source):
    return Path(source).stem.replace("_", " ")


def show_meta(meta):
    with st.expander("How I got this answer"):
        car = {k: v for k, v in meta["car"].items() if v}
        st.write("**Car I detected in your question:**", ", ".join(f"{k} {v}" for k, v in car.items()) or "none")
        if meta["cited"]:
            st.write("**Documents cited:**", ", ".join(label(s) for s in meta["cited"]))
        st.write("**Documents retrieved (closest first):**")
        st.dataframe(pd.DataFrame(meta["hits"]), hide_index=True, width="stretch")
        if meta["query"] != meta["question"]:
            st.caption(f"Searched using: {meta['query']}")


def ask(question, col):
    messages = st.session_state.messages
    query = build_query(messages, question)
    with st.spinner("Looking through the service documents..."):
        r = answer_mod.answer(query, k=5, collection=col)
    meta = {"car": r["car"], "cited": r["cited_sources"], "query": query, "question": question,
            "hits": [{"Rank": h["rank"], "Document": label(h["source"]), "Distance": round(h["distance"], 3)}
                     for h in r["hits"]]}
    messages.append({"role": "user", "content": question})
    messages.append({"role": "assistant", "content": r["answer"], "meta": meta})
    st.session_state.count += 1
    global_counter()["n"] += 1


# ---------- page ----------
require_password()
st.session_state.setdefault("messages", [])
st.session_state.setdefault("count", 0)

left_in_session = MAX_QUESTIONS - st.session_state.count
limit_reached = left_in_session <= 0 or global_counter()["n"] >= GLOBAL_LIMIT

with st.sidebar:
    st.header("CarAssist")
    st.metric("Questions left this session", max(left_in_session, 0))
    if st.button("Start a new chat"):
        st.session_state.messages = []
        st.rerun()
    if secret("REPO_URL"):
        st.link_button("View the code", str(secret("REPO_URL")))
    st.caption("All makes, models, specs, and part numbers here are fictional. "
               "Answers come only from the service documents, and a technician should confirm any diagnosis.")

st.title("CarAssist")
st.write("A support assistant for a dealer parts-and-service desk. Describe a car problem or ask a how-to "
         "question, and it explains what's likely wrong, how to do the job, and which part fits your exact "
         "car, with the documents it used.")

col_left, col_right = st.columns([3, 2], gap="large")
with col_left:
    st.subheader("Cars covered")
    st.dataframe(load_cars(), hide_index=True, width="stretch")
    st.caption("Model years 2024 and 2025. Part numbers can differ between similar models and between years, "
               "so always include the make, model, and year.")
with col_right:
    st.subheader("Is your car covered?")
    typed = st.text_input("Type it like 2024 Norvik Vale", key="car_check")
    if typed.strip():
        level, msg = car_status(typed)
        getattr(st, level)(msg)
    st.subheader("What it can help with")
    st.markdown("- How-to steps (tire change, jump-start, fuses, fluids)\n"
                "- What a symptom likely means\n"
                "- Which part fits your car\n\n"
                "It can't give prices, check stock, or place orders.")

st.subheader("Try a question")
cols = st.columns(3)
for i, ex in enumerate(EXAMPLES):
    if cols[i % 3].button(ex, key=f"ex{i}", width="stretch", disabled=limit_reached):
        st.session_state.pending = ex

st.divider()
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            show_meta(m["meta"])

if limit_reached:
    st.info("This demo has reached its question limit. Thanks for trying it. "
            "The code and results are in the project's GitHub repo.")

typed_question = st.chat_input("Ask about a car problem, a procedure, or a part...", disabled=limit_reached)
question = st.session_state.pop("pending", None) or typed_question
if question and not limit_reached:
    question = question.strip()
    if len(question) > MAX_CHARS:
        st.warning(f"Please keep questions under {MAX_CHARS} characters.")
    else:
        try:
            ask(question, get_collection())
            st.rerun()
        except Exception as e:  # show something friendly, keep the details in the server log
            traceback.print_exc()
            print("CarAssist error:", repr(e))
            st.error("Something went wrong while answering. Please try again in a moment.")
