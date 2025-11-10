import os, json, re
from typing import List, Dict
from openai import OpenAI

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
    return {"title": "Summary", "tl_dr": text.strip(), "sections": []}

def _chunk_text(text: str, chunk_chars: int = 9000, overlap: int = 500) -> List[str]:
    if len(text) <= chunk_chars:
        return [text]
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i+chunk_chars])
        i += max(1, chunk_chars - overlap)
    return chunks

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

def _chunk_summary_prompt(chunk: str, audience: str, detail: int) -> List[Dict]:
    q_count = min(8, 3 + detail)
    fc_count = min(30, 8 + detail * 4)
    ex_count = min(5, 1 + detail // 2)
    pit_count = min(8, 3 + detail)

    sys = (
        "You are a meticulous study-note generator for exam prep. "
        "Be specific, use precise language, include concrete facts, definitions, and formulas if present. "
        "Bullets should be short but information-dense. Avoid fluff."
    )
    user = (
        f"Create rich study notes for a {audience} student from the CONTENT below.\n\n"
        f"{SCHEMA_DESCRIPTION}\n\n"
        "Requirements:\n"
        f"- Provide ~{ex_count} worked example(s) if possible.\n"
        f"- Include ~{pit_count} common_pitfalls (typical misconceptions).\n"
        f"- Include ~{q_count} exam_questions with concise model answers and point-based markscheme_points.\n"
        f"- Include ~{fc_count} flashcards (front: question/term; back: definition/answer).\n\n"
        f"CONTENT (chunk):\n{chunk}"
    )
    return [{"role":"system","content":sys},{"role":"user","content":user}]

def _merge_prompt(partials_json: List[dict], audience: str, detail: int) -> List[Dict]:
    q_count = min(12, 5 + detail)
    fc_count = min(40, 12 + detail * 5)

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
        f"- Keep exam_questions concise; provide ~{q_count} of the best.\n"
        f"- Keep flashcards to ~{fc_count} high-quality items.\n\n"
        f"PARTIALS:\n{json.dumps(partials_json)[:120000]}"
    )
    return [{"role":"system","content":sys},{"role":"user","content":user}]

def ask_gpt(prompt: str) -> str:
    client = get_client()
    r = client.responses.create(model="gpt-4o-mini", input=prompt)
    return r.output_text

def summarize_text(text: str, audience: str = "university", detail: int = 3) -> dict:
    client = get_client()
    chunks = _chunk_text(text, chunk_chars=9000, overlap=500)
    partials = []
    for ch in chunks:
        msgs = _chunk_summary_prompt(ch, audience, detail)
        r = client.responses.create(model="gpt-4o-mini", input=msgs)
        partial = _parse_json_loose(r.output_text or "")
        partials.append(partial)

    if len(partials) == 1:
        return partials[0]

    msgs = _merge_prompt(partials, audience, detail)
    r = client.responses.create(model="gpt-4o-mini", input=msgs)
    return _parse_json_loose(r.output_text or "")


