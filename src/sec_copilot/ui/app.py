"""The chat page. Run it with: streamlit run src/sec_copilot/ui/app.py"""
import streamlit as st

from sec_copilot.ui import api_client

EXAMPLES = [
    "Which company had the highest operating margin in its most recent quarter?",
    "What does NVIDIA say about export controls?",
    "How fast did Micron's revenue grow in its latest quarter, and what does Micron say about demand?",
    "Forecast Apple's revenue for its next quarter.",
    "Compare Microsoft's and Apple's gross margin over the last 8 quarters.",
]

st.set_page_config(page_title="SEC Copilot", page_icon="📊", layout="wide")


def safe(text: str) -> str:
    """Streamlit treats $...$ as a maths formula, which would garble 'revenue was $114.5 billion and $110 billion'."""
    return text.replace("$", "\\$")


def render_source(source: dict) -> None:
    tag = source["tag"]
    if source["type"] == "sql":
        with st.expander(f"[{tag}] SQL result ({source['total_rows']} rows)"):
            st.code(source["sql"], language="sql")
            st.dataframe([dict(zip(source["columns"], row)) for row in source["rows"]])
            if source["total_rows"] > len(source["rows"]):
                st.caption(f"Showing the first {len(source['rows'])} of {source['total_rows']} rows.")
    elif source["type"] == "forecast":
        with st.expander(f"[{tag}] Forecast for {source['ticker']} ({source['model']} model)"):
            st.markdown(safe(source["summary"]))
            st.caption(source.get("caveat", ""))
    else:
        with st.expander(f"[{tag}] {source['ticker']} FY{source['fiscal_year']} {source['section']} (filing excerpt)"):
            st.markdown(safe(source["text"]))


def render_answer(payload: dict) -> None:
    st.markdown(safe(payload["answer"]))
    for warning in payload.get("warnings", []):
        st.warning(warning)
    for source in payload.get("sources", []):
        render_source(source)
    if payload.get("tool_calls"):
        with st.expander("What the assistant did"):
            for call in payload["tool_calls"]:
                st.markdown(f"- `{call['name']}` → {call['outcome']}")
    st.caption(f"{payload['model_calls']} model calls · {payload['seconds']} s")


def render_body(message: dict) -> None:
    """Draw a message's content inside the chat bubble that is currently open."""
    if message["role"] == "user":
        st.markdown(safe(message["content"]))
    elif "problem" in message:
        (st.info if message["level"] == "info" else st.error)(message["problem"])
    else:
        render_answer(message["payload"])


def render_message(message: dict) -> None:
    with st.chat_message(message["role"]):
        render_body(message)


# ---------- sidebar ----------
info = api_client.health()
with st.sidebar:
    st.header("About")
    st.write("Answers come from SEC filings of 15 US tech companies: quarterly financials (SQL), 10-K risk factors and "
             "MD&A (search), and a backtested revenue forecast.")
    st.caption("Citations: **[Q]** SQL result · **[C]** filing excerpt · **[F]** forecast. Not financial advice.")
    if info:
        st.caption(f"Data as of {info['data_as_of']} · model {info['model']} · {info['questions_left_today']} questions left today")
    else:
        st.caption("The API is not reachable right now.")
    st.header("Try one")
    for example in EXAMPLES:
        if st.button(example, key=example):
            st.session_state["pending"] = example

# ---------- chat ----------
st.title("SEC Copilot")
st.caption("Ask about revenue, margins, risks and forecasts for AAPL, MSFT, NVDA, INTC, AMD, CSCO, CRM, ADBE, IBM, TXN, MU, "
           "AVGO, NOW, INTU and AMAT.")
st.session_state.setdefault("messages", [])
for message in st.session_state["messages"]:
    render_message(message)

question = st.chat_input("Ask a question about the companies...") or st.session_state.pop("pending", None)
if question:
    user_message = {"role": "user", "content": question}
    st.session_state["messages"].append(user_message)
    render_message(user_message)
    with st.chat_message("assistant"):
        with st.spinner("Searching the data and the filings. This takes 10 to 30 seconds."):
            try:
                reply = {"role": "assistant", "payload": api_client.ask(question)}
            except api_client.ApiProblem as problem:
                reply = {"role": "assistant", "problem": problem.message, "level": problem.level}
        render_body(reply)                       # same bubble as the spinner, so no empty bubble is left behind
    st.session_state["messages"].append(reply)