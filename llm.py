# llm.py
import os
import json
import re
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

def _parse_json_loose(text: str) -> dict:
    """
    Try to parse JSON from the model output even if it includes extra text.
    """
    # Fast path
    try:
        return json.loads(text)
    except Exception:
        pass
    # Extract the first {...} block
    m = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # Last resort: wrap as a minimal object
    return {"title": "Summary", "tl_dr": text.strip(), "sections": []}

def ask_gpt(prompt: str) -> str:
    client = get_client()
    r = client.responses.create(model="gpt-4o-mini", input=prompt)
    return r.output_text

def summarize_text(text: str, audience: str = "university") -> dict:
    client = get_client()
    sys = (
        "You are a precise study summarizer. "
        "Return concise, factual summaries with short bullet points. "
        "CRITICAL: Return ONLY JSON with keys exactly: "
        "title, tl_dr, sections (array of {heading, bullets}). No prose before/after."
    )
    user = (
        f"Summarize the following content for a {audience} student.\n\n"
        "Keys exactly: title (string), tl_dr (string), sections (array of objects with "
        "heading (string) and bullets (array of strings)). Keep bullets short and exam-oriented.\n\n"
        f"CONTENT:\n{text[:15000]}"
    )
    # No response_format here (not supported in your environment)
    r = client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "system", "content": sys},
               {"role": "user", "content": user}]
    )
    return _parse_json_loose(r.output_text or "")
