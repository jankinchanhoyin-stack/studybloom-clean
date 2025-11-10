# llm.py
import os
import json
from openai import OpenAI

def _get_api_key():
    # Prefer Streamlit secrets in the cloud
    try:
        import streamlit as st
        k = st.secrets.get("OPENAI_API_KEY")
        if k:
            return k
    except Exception:
        pass
    # Fallback to .env locally
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return os.getenv("OPENAI_API_KEY")

def get_client():
    key = _get_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found in Streamlit Secrets or environment.")
    return OpenAI(api_key=key)

def ask_gpt(prompt: str) -> str:
    client = get_client()
    r = client.responses.create(model="gpt-4o-mini", input=prompt)
    return r.output_text

def summarize_text(text: str, audience: str = "university") -> dict:
    client = get_client()
    sys = (
        "You are a precise study summarizer. "
        "Return concise, factual summaries with short bullet points. "
        "Output valid JSON only."
    )
    user = (
        f"Summarize the following content for a {audience} student.\n\n"
        "Return JSON with keys exactly: title, tl_dr, sections (array of {heading, bullets}).\n"
        "Keep bullets short and exam-oriented.\n\n"
        f"CONTENT:\n{text[:15000]}"
    )
    r = client.responses.create(
        model="gpt-4o-mini",
        input=[{"role":"system","content":sys},{"role":"user","content":user}],
        response_format={"type":"json_object"},
    )
    return json.loads(r.output_text)
