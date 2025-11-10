# llm.py
import os, json, re
from typing import List, Dict, Any

def _get_api_key():
    try:
        import streamlit as st
        k = st.secrets.get("OPENAI_API_KEY")
        if k: return k
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
    try:
        from openai import OpenAI  # >=1.x client
        return OpenAI(api_key=key)
    except Exception:
        import openai
        openai.api_key = key
        return openai

def _supports_responses(client) -> bool:
    return hasattr(client, "responses") and hasattr(client.responses, "create")

def _parse_json_loose(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
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
    "formulas (array of objects: {name (string), expression (string), meaning (string), latex (string optional)}),\n"
    "examples (array of strings),\n"
    "common_pitfalls (array of strings),\n"
    "exam_questions (array of objects: {question (string), model_answer (string), markscheme_points (array of strings)}),\n"
    "flashcards (array of objects: {front (string), back (string)}).\n"
    "No prose before or after the JSON."
)

def _subject_rules(subject: str) -> str:
    s = (subject or "").lower()
    if "math" in s or "mathemat" in s:
        return (
            "For Mathematics: produce calculation/past-paper style questions only; "
            "avoid essays. Prefer LaTeX in formulas.latex (a^2 -> a^{2}, "
            "fractions \\frac{a}{b}, roots \\sqrt{}, products \\cdot, vectors \\vec{}). "
            "Keep model answers concise with mark-scheme points."
        )
    return (
        "Ensure exam_questions match the subject; avoid off-topic questions. "
        "Be concrete with definitions, processes and key facts."
    )

def _chunk_summary_prompt(chunk: str, audience: str, detail: int, subject: str):
    q_count = min(8, 3 + detail)
    fc_count = min(30, 8 + detail * 4)
    ex_count = min(5, 1 + detail // 2)
    pit_count = min(8, 3 + detail)

    sys = (
        "You are a meticulous study-note generator for exam prep. "
        "Be specific, include precise definitions, facts, and formulas if present. "
        "Bullets must be short but information-dense."
    )
    user = (
        f"SUBJECT: {subject}\n"
        f"Create rich study notes for a {audience} student from the CONTENT below.\n\n"
        f"{SCHEMA_DESCRIPTION}\n\n"
        "Requirements:\n"
        f"- Provide ~{ex_count} worked example(s) if possible.\n"
        f"- Include ~{pit_count} common_pitfalls.\n"
        f"- Include ~{q_count} exam_questions with concise model answers and point-based markscheme_points.\n"
        f"- Include ~{fc_count} flashcards.\n"
        f"- {_subject_rules(subject)}\n\n"
        f"CONTENT (chunk):\n{chunk}"
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

def _merge_prompt(partials_json: List[dict], audience: str, detail: int, subject: str):
    q_count = min(12, 5 + detail)
    fc_count = min(40, 12 + detail * 5)
    sys = (
        "You are combining multiple partial study-note summaries into a single coherent set. "
        "Merge, deduplicate, and improve specificity. Keep it exam-oriented."
    )
    user = (
        f"SUBJECT: {subject}\n"
        f"Merge the following partial JSON summaries into one final, comprehensive set for a {audience} student.\n"
        f"{SCHEMA_DESCRIPTION}\n\n"
        "Guidelines:\n"
        "- Deduplicate overlapping bullets/terms.\n"
        "- Keep the most specific, factual phrasing.\n"
        "- Ensure a logical section order from fundamentals → applications.\n"
        "- Keep questions strictly within the SUBJECT.\n"
        f"- Keep exam_questions concise; provide ~{q_count} of the best.\n"
        f"- Keep flashcards to ~{fc_count} high-quality items.\n\n"
        f"PARTIALS:\n{json.dumps(partials_json)[:120000]}"
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

def _call_model(client, messages) -> str:
    model = "gpt-4o-mini"
    if _supports_responses(client):
        resp = client.responses.create(model=model, input=messages)
        try:
            return resp.output_text
        except Exception:
            try:
                return resp.choices[0].message.content[0].text
            except Exception:
                return ""
    else:
        try:
            import openai as old
            r = old.ChatCompletion.create(model=model, messages=messages, temperature=0.2)
            return r["choices"][0]["message"]["content"]
        except Exception:
            r = client.chat.completions.create(model=model, messages=messages, temperature=0.2)
            return r.choices[0].message.content

def summarize_text(text: str, audience: str = "university", detail: int = 3, subject: str = "General") -> dict:
    client = get_client()
    chunks = _chunk_text(text, chunk_chars=9000, overlap=500)
    partials = []
    for ch in chunks:
        msgs = _chunk_summary_prompt(ch, audience, detail, subject)
        out = _call_model(client, msgs) or ""
        partials.append(_parse_json_loose(out))
    if len(partials) == 1:
        return partials[0]
    msgs = _merge_prompt(partials, audience, detail, subject)
    out = _call_model(client, msgs) or ""
    return _parse_json_loose(out)

# --------- AI grading for free-text quiz answers ----------
def grade_free_answer(question: str, model_answer: str, markscheme: List[str], user_answer: str, subject: str = "General") -> Dict[str, Any]:
    """
    Returns: {"correct": bool, "score": int, "max_points": int, "feedback": str}
    """
    client = get_client()
    max_points = max(5, min(10, len(markscheme) or 5))  # heuristic
    sys = (
        "You are a strict but fair examiner. Score the student's answer against the model answer "
        "and mark-scheme points. Accept equivalent phrasing and equivalent maths/units. "
        "Return ONLY JSON: {\"correct\": bool, \"score\": int, \"max_points\": int, \"feedback\": string}."
    )
    user = (
        f"SUBJECT: {subject}\n"
        f"QUESTION: {question}\n\n"
        f"MODEL_ANSWER: {model_answer}\n"
        f"MARK_SCHEME_POINTS: {json.dumps(markscheme)}\n\n"
        f"STUDENT_ANSWER: {user_answer}\n"
        f"MAX_POINTS: {max_points}\n"
        "Rules:\n"
        "- Map student's content to mark-scheme points.\n"
        "- Don't nit-pick wording if the idea/calculation is correct.\n"
        "- If the answer is largely correct but missing minor detail, give partial credit.\n"
        "- Respond ONLY with the JSON."
    )
    messages = [{"role":"system","content":sys},{"role":"user","content":user}]
    out = _call_model(client, messages) or ""
    try:
        data = json.loads(out)
        data["max_points"] = int(max_points)
        data["score"] = int(max(0, min(max_points, data.get("score",0))))
        data["correct"] = bool(data.get("correct", data["score"] >= max_points*0.7))
        data["feedback"] = str(data.get("feedback",""))
        return data
    except Exception:
        return {"correct": False, "score": 0, "max_points": max_points, "feedback": "Could not parse grading response."}

# --------- Generate a NEW quiz from existing notes ----------
def generate_quiz_from_notes(notes: dict, subject: str = "General", audience: str = "high school", num_questions: int = 8) -> List[Dict[str, Any]]:
    """
    notes: the 'data' of a saved summary (sections, key_terms, formulas, etc.)
    Returns a list of exam_questions.
    """
    client = get_client()
    sys = (
        "You generate exam-style questions from provided study notes. "
        "Return ONLY JSON array exam_questions with objects: "
        "{question, model_answer, markscheme_points}."
    )
    user = (
        f"SUBJECT: {subject}\nAUDIENCE: {audience}\n"
        f"NUM_QUESTIONS: {num_questions}\n"
        f"{_subject_rules(subject)}\n\n"
        f"NOTES_JSON:\n{json.dumps(notes)[:120000]}"
    )
    messages = [{"role":"system","content":sys},{"role":"user","content":user}]
    out = _call_model(client, messages) or ""
    try:
        data = json.loads(out)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "exam_questions" in data:
            return data["exam_questions"]
    except Exception:
        pass
    return []





