# llm.py
# llm.py
import os, json, re, math
from typing import List, Dict
from openai import OpenAI

# ---------- key management ----------
def _get_api_key():
    try:
        import streamlit as st
        k = st.secrets.get("OPENAI_API_KEY")
        if k:
            return k
    except Exception:
        pass
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

# ---------- helpers ----------
def _parse_json_loose(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # last resort: wrap raw text
    return {"title": "Summary", "tl_dr": text.strip(), "sections": []}

def _chunk_text(text: str, chunk_chars: int = 9000, overlap: int = 500) -> List[str]:
    if len(text) <= chunk_chars:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i+chunk_chars])
        i += chunk_chars - overlap
    return chunks

# ---------- prompts ----------
SCHEMA_DESCRIPTION = (
    "Return ONLY JSON with keys exactly:\n"
    "title (string),\n"
    "tl_dr (string),\n"
    "sections (array of objects: {heading (string), bullets (array of strings)}),\n"
    "key_terms (array of objects: {term (string), definition (string)}),\n"
    "formulas (array of objects: {name (string), expression (string), meaning (string)}),\n"
    "examples (array of strings),\n"
    "common_pitfalls (array of strings),\n"
    "exam_questions (array of objects: {question (string), model_answer (string), markscheme_points (array of strings)}),\n"
    "flashcards (array of objects: {front (string), back (string)}).\n"
    "No prose before or after the JSON."
)

def _chunk_summary_prompt(chunk: str, audience: str) -> List[Dict]:
    sys = (
        "You are a meticulous study-note generator for exam prep. "
        "Be specific, use precise language, include concrete facts, definitions, and where relevant formulas. "
        "Bullets should be short but information-dense. Avoid fluff."
    )
    user = (
        f"Create rich study notes for a {audience} student from the CONTENT below.\n\n"
        f"{SCHEMA_DESCRIPTION}\n\n"
        "Requirements:\n"
        "- Sections should reflect the main topics in this chunk.\n"
        "- Include key_terms for all new vocabulary and concepts.\n"
        "- Include formulas only if present/relevant; otherwise return an empty array.\n"
        "- Provide at least one worked example if possible.\n"
        "- Include 3–5 common_pitfalls (typical misconceptions).\n"
        "- Include 5 exam_questions with model answers in the exam board's tone (concise, point-based).\n"
        "- Include 10 flashcards (front: question/term; back: definition/answer).\n\n"
        f"CONTENT (chunk):\n{chunk}"
    )
    return [{"role":"system","content":sys},{"role":"user","content":user}]

def _merge_prompt(partials_json: List[dict], audience: str) -> List[Dict]:
    sys = (
        "You are combining multiple partial study-note summaries into a single coherent set. "
        "Merge, deduplicate, and improve specificity. Keep it exam-oriented."
    )
    user = (
        f"Merge the following partial JSON summaries into one final, comprehensive set for a {audience} student.\n"
        f"{SCHEMA_DESCRIPTION}\n\n"
        "Guidelines:\n"
        "- Deduplicate overlapping bullets/terms.\n"
        "- Keep the most specific, factual phrasing.\n"
        "- Ensure a logical section order from fundamentals → applications.\n"
        "- Keep exam_questions concise with clear markscheme_points.\n"
        "- Keep total flashcards to ~20 best ones.\n\n"
        f"PARTIALS:\n{json.dumps(partials_json)[:120000]}"
    )
    return [{"role":"system","content":sys},{"role":"user","content":user}]

# ---------- public API ----------
def ask_gpt(prompt: str) -> str:
    client = get_client()
    r = client.responses.create(model="gpt-4o-mini", input=prompt)
    return r.output_text

def summarize_text(text: str, audience: str = "university") -> dict:
    """
    Map-reduce: if long, summarise chunks then merge into a rich final JSON.
    audience: 'university' or 'high school'
    """
    client = get_client()
    chunks = _chunk_text(text, chunk_chars=9000, overlap=500)

    # 1) summarise each chunk to structured JSON
    partials = []
    for idx, ch in enumerate(chunks, start=1):
        msgs = _chunk_summary_prompt(ch, audience)
        r = client.responses.create(model="gpt-4o-mini", input=msgs)
        partial = _parse_json_loose(r.output_text or "")
        partials.append(partial)

    # 2) if only one chunk, return it; else merge
    if len(partials) == 1:
        return partials[0]

    msgs = _merge_prompt(partials, audience)
    r = client.responses.create(model="gpt-4o-mini", input=msgs)
    return _parse_json_loose(r.output_text or "")
