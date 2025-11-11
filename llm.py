# llm.py
import os, json
from typing import List, Dict, Any, Optional
from openai import OpenAI
import sympy as sp

QUALITY_GUIDELINES = (
    "Quality rubric:\n"
    "- Faithful to source; no outside facts unless clearly general knowledge.\n"
    "- Precise terminology; include units, conditions/assumptions, edge-cases.\n"
    "- Prefer worked examples over abstract claims; show steps succinctly.\n"
    "- Highlight common misconceptions and contrast similar concepts.\n"
    "- Use exam-style phrasing and concise bullets; avoid filler.\n"
)

# ---------- Model choices ----------
FAST_MODEL  = os.getenv("MODEL_FAST",  "gpt-4o-mini")
SMART_MODEL = os.getenv("MODEL_SMART", "gpt-4o")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Summarization (single call; simple) ----------
def summarize_text(text: str, audience: str = "high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    import json
    text = (text or "").strip()
    if len(text) > 200_000:
        text = text[:200_000]

    sys = (
        "Fuse the input into concise, accurate study notes for the given subject and audience.\n"
        + QUALITY_GUIDELINES +
        "\nReturn JSON ONLY with keys:\n"
        "  tl_dr (string),\n"
        "  sections (array of {heading, bullets}),\n"
        "  key_terms (array of {term, definition}),\n"
        "  formulas (optional array of {name, latex, meaning}),\n"
        "  pitfalls (optional array of strings: common misconceptions),\n"
        "  examples (optional array of {prompt, worked_solution}).\n"
        "Keep bullets short, exam-relevant, and self-contained."
    )

    resp = client.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps({
                "subject": subject,
                "audience": audience,
                "detail": detail,
                "text": text
            })},
        ],
    )
    return json.loads(resp.choices[0].message.content)

# ---------- Flashcards (with target_count) ----------
def generate_flashcards_from_notes(
    notes_json: Dict[str, Any],
    audience: str = "high school",
    target_count: int | None = None
) -> List[Dict[str, str]]:
    """
    Return a high-quality deck targeting target_count if provided.
    Cards should be active-recall, unambiguous, and atomic (1 fact/step).
    Include a few cloze deletions and a few multi-step reasoning cards where relevant.
    """
    import json
    sys = (
        "Return JSON ONLY: flashcards (array of {front, back}).\n"
        + QUALITY_GUIDELINES +
        "\nGuidance for cards:\n"
        "- Active recall questions; avoid yes/no.\n"
        "- Make cards atomic; split multi-ideas into multiple cards.\n"
        "- Prefer definition → application → misconception coverage.\n"
        "- Use clear variables/units; include short worked steps when needed.\n"
        "- Include 10–20% cloze deletions like 'The ___ law states ...'.\n"
    )
    payload = {"audience": audience, "notes": notes_json}
    if target_count:
        payload["target_count"] = int(target_count)

    resp = client.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1600,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("flashcards") or []

# ---------- Quizzes (free-response or MCQ; higher quality) ----------
def generate_quiz_from_notes(
    notes_json: Dict[str, Any],
    subject: str = "General",
    audience: str = "high school",
    num_questions: int = 8,
    mode: str = "free",        # "free" or "mcq"
    mcq_options: int = 4,
) -> List[Dict[str, Any]]:
    import json
    if mode == "mcq":
        sys_msg = (
            "Return JSON ONLY: questions (array of {question, options(array), correct_index(int), explanation}).\n"
            + QUALITY_GUIDELINES +
            "\nConstraints:\n"
            f"- Exactly one correct option per question, total options = {mcq_options}.\n"
            "- Mix difficulty: ~40% easy, ~40% medium, ~20% challenging.\n"
            "- Options must be plausible; avoid giveaways like length or grammar.\n"
            "- Include brief rationale/explanation, focusing on misconception busting.\n"
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
            "Return JSON ONLY: questions (array of {question, model_answer, markscheme_points(array)}).\n"
            + QUALITY_GUIDELINES +
            "\nConstraints:\n"
            "- Exam-style phrasing; point-marked. Provide concise, stepwise markscheme points.\n"
            "- Mix difficulty: ~40% recall, ~40% application, ~20% problem solving.\n"
            "- Prefer questions whose answers are demonstrably present/derivable from the notes.\n"
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
        max_tokens=2000,
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


