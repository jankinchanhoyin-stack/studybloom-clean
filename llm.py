# llm.py
import os, json
from typing import List, Dict, Any, Optional
from openai import OpenAI
import sympy as sp

# ---------- Model choices ----------
FAST_MODEL  = os.getenv("MODEL_FAST",  "gpt-4o-mini")
SMART_MODEL = os.getenv("MODEL_SMART", "gpt-4o")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Summarization (single call; simple) ----------
def summarize_text(text: str, audience: str = "high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    text = (text or "").strip()
    # soft guard to keep prompt size sane, but no chunking
    if len(text) > 200_000:
        text = text[:200_000]

    resp = client.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=1200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Fuse the input into concise, accurate study notes for the given subject and audience. "
                    "RETURN JSON ONLY with keys: "
                    " tl_dr(string), "
                    " sections(array of {heading,bullets}), "
                    " key_terms(array of {term,definition}), "
                    " formulas(optional array of {name,latex,meaning}), "
                    " flashcards(optional array of {front,back}), "
                    " exam_questions(optional array)."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "subject": subject,
                    "audience": audience,
                    "detail": detail,
                    "text": text
                }),
            },
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ---------- Flashcards ----------
def generate_flashcards_from_notes(notes_json: Dict[str, Any], audience: str = "high school") -> List[Dict[str, str]]:
    resp = client.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=800,
        messages=[
            {"role": "system", "content": "Return JSON ONLY: flashcards(array of {front,back})."},
            {"role": "user", "content": json.dumps({"audience": audience, "notes": notes_json})},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("flashcards") or []

# ---------- Quizzes (free-response or MCQ) ----------
def generate_quiz_from_notes(
    notes_json: Dict[str, Any],
    subject: str = "General",
    audience: str = "high school",
    num_questions: int = 8,
    mode: str = "free",        # "free" or "mcq"
    mcq_options: int = 4,
) -> List[Dict[str, Any]]:
    if mode == "mcq":
        sys_msg = (
            "Return JSON ONLY: questions(array of {question, options(array), correct_index(int), explanation}). "
            "Only ask questions answerable by selecting exactly one option."
        )
        user_payload = {
            "subject": subject,
            "audience": audience,
            "num_questions": num_questions,
            "mcq_options": mcq_options,
            "notes": notes_json,
        }
    else:
        sys_msg = (
            "Return JSON ONLY: questions(array of {question, model_answer, markscheme_points(array)}). "
            "Questions should be exam-style and point-marked."
        )
        user_payload = {
            "subject": subject,
            "audience": audience,
            "num_questions": num_questions,
            "notes": notes_json,
        }

    resp = client.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("questions") or []

# ---------- Grading (math local first, then LLM) ----------
def try_grade_math_numeric(user_answer: str, model_answer: str) -> Optional[bool]:
    try:
        u = sp.N(sp.sympify(user_answer))
        m = sp.N(sp.sympify(model_answer))
        return bool(sp.Abs(u - m) < sp.Float("1e-6"))
    except Exception:
        return None

def try_grade_math_expr(user_answer: str, model_answer: str) -> Optional[bool]:
    try:
        u = sp.simplify(sp.sympify(user_answer))
        m = sp.simplify(sp.sympify(model_answer))
        return bool(sp.simplify(u - m) == 0)
    except Exception:
        return None

def grade_free_answer(q, model_answer, markscheme, user_answer, subject: str = "General") -> Dict[str, Any]:
    # Quick local math equivalence if subject is math-like
    if (subject or "").lower().startswith("math"):
        eq = try_grade_math_numeric(user_answer, model_answer)
        if eq is None:
            eq = try_grade_math_expr(user_answer, model_answer)
        if eq is not None:
            return {"score": 10 if eq else 0, "max_points": 10, "feedback": "Auto-graded (math equivalence)."}

    resp = client.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=500,
        messages=[
            {"role": "system", "content": "Return JSON ONLY: {score:int,max_points:int,feedback:string}. Use the mark scheme."},
            {"role": "user", "content": json.dumps({
                "subject": subject,
                "question": q,
                "model_answer": model_answer,
                "markscheme_points": markscheme,
                "user_answer": user_answer
            })},
        ],
    )
    return json.loads(resp.choices[0].message.content)


