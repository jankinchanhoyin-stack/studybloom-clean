# llm.py
import os, json, re
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

# -----------------------------------
# Helper: format verbatim definitions
# -----------------------------------
def _format_verbatim_defs(verbatim_definitions: Optional[List[Dict[str, str]]]) -> str:
    """
    Render as 'term := definition' lines. We never alter the definition text.
    """
    if not verbatim_definitions:
        return ""
    lines = []
    for d in verbatim_definitions:
        term = (d.get("term") or "").strip()
        definition = (d.get("definition") or "").strip()
        if term and definition:
            lines.append(f"- {term} := {definition}")
    return "\n".join(lines)

def _length_hint(detail: int) -> str:
    # Nudge notes longer while staying concise
    d = max(1, min(int(detail or 3), 5))
    return {1:"brief", 2:"compact", 3:"standard", 4:"extended", 5:"thorough"}[d]


# ---------- Summarization (slightly longer + verbatim defs) ----------
def summarize_text(
    text: str,
    audience: str = "high school",
    detail: int = 3,
    subject: str = "General",
    verbatim_definitions: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Return JSON ONLY with keys:
      tl_dr (string),
      sections (array of {heading, bullets}),
      key_terms (array of {term, definition}),
      formulas (optional array of {name, latex, meaning}),
      pitfalls (optional array of strings),
      examples (optional array of {prompt, worked_solution})
    Ensures any defs supplied in verbatim_definitions are quoted EXACTLY in key_terms
    and mirrored in the notes content where relevant.
    """
    text = (text or "").strip()
    if len(text) > 200_000:
        text = text[:200_000]

    defs_block = _format_verbatim_defs(verbatim_definitions)
    defs_instruction = (
        "KNOWN VERBATIM DEFINITIONS (use EXACT wording wherever these terms appear in notes or key_terms):\n"
        f"{defs_block}\n"
        if defs_block else
        "If the source provides explicit Term → Definition lines, quote them EXACTLY (no paraphrasing) in key_terms."
    )

    sys = (
        f"Fuse the input into { _length_hint(detail) } study notes for the given subject and audience.\n"
        + QUALITY_GUIDELINES +
        "\nReturn JSON ONLY with keys:\n"
        "  tl_dr (string),\n"
        "  sections (array of {heading, bullets}),\n"
        "  key_terms (array of {term, definition}),\n"
        "  formulas (optional array of {name, latex, meaning}),\n"
        "  pitfalls (optional array of strings: common misconceptions),\n"
        "  examples (optional array of {prompt, worked_solution}).\n"
        "Keep bullets short, exam-relevant, and self-contained.\n"
        "For any definition present in KNOWN VERBATIM DEFINITIONS, copy the definition TEXT EXACTLY (no paraphrasing)."
    )

    payload = {
        "subject": subject,
        "audience": audience,
        "detail": detail,
        "text": text,
        "verbatim_definitions": verbatim_definitions or [],
        "verbatim_defs_block": defs_block,
        "length_hint": _length_hint(detail),
    }

    resp = client.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=2300,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": defs_instruction},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    return json.loads(resp.choices[0].message.content)


# ---------- Flashcards (verbatim defs + target_count) ----------
def generate_flashcards_from_notes(
    notes_json: Dict[str, Any],
    audience: str = "high school",
    target_count: Optional[int] = None,
    verbatim_definitions: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """
    Return JSON ONLY: {"flashcards":[{front, back}, ...]}
    - If a card is a definition card and the term exists in verbatim_definitions,
      the BACK must contain the EXACT definition string (no paraphrase, no quotes).
    - Use active recall; make cards atomic; include some cloze deletions.
    """
    defs_block = _format_verbatim_defs(verbatim_definitions)
    sys = (
        "Return JSON ONLY: {\"flashcards\": [{\"front\":\"...\",\"back\":\"...\"}, ...]}.\n"
        + QUALITY_GUIDELINES +
        "\nGuidance for cards:\n"
        "- Active recall questions; avoid yes/no.\n"
        "- Make cards atomic; split multi-ideas into multiple cards.\n"
        "- Prefer definition → application → misconception coverage.\n"
        "- Use clear variables/units; include short worked steps when needed.\n"
        "- Include ~10–20% cloze deletions like 'The ___ law states ...'.\n"
        "- If a term has a KNOWN VERBATIM DEFINITION, the back MUST be that exact text (no paraphrasing or quotes).\n"
    )
    payload: Dict[str, Any] = {"audience": audience, "notes": notes_json}
    if target_count:
        payload["target_count"] = int(target_count)

    instruction = (
        "KNOWN VERBATIM DEFINITIONS (term := EXACT back text when making definition cards):\n"
        f"{defs_block or '(none provided)'}"
    )

    resp = client.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=1700,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": instruction},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    cards = data.get("flashcards") or []
    # sanitize
    out = []
    for c in cards:
        f = (c.get("front") or "").strip()
        b = (c.get("back") or "").strip()
        if f and b:
            out.append({"front": f, "back": b})
    return out


# ---------- Quizzes (free or MCQ; verbatim defs enforced) ----------
def generate_quiz_from_notes(
    notes_json: Dict[str, Any],
    subject: str = "General",
    audience: str = "high school",
    num_questions: int = 8,
    mode: str = "free",        # "free" or "mcq"
    mcq_options: int = 4,
    verbatim_definitions: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return JSON ONLY:
      if mode == "mcq":
        {"questions":[{"question","options":[...], "correct_index":int, "explanation"}]}
      else:
        {"questions":[{"question","model_answer","markscheme_points":[...]}]}
    For questions that test a DEFINITION present in verbatim_definitions,
    ensure the correct answer (or model_answer) contains the EXACT wording.
    """
    defs_block = _format_verbatim_defs(verbatim_definitions)
    instruction = (
        "KNOWN VERBATIM DEFINITIONS (use EXACT wording in the correct answer/model answer when asked for that definition):\n"
        f"{defs_block or '(none provided)'}"
    )

    if mode == "mcq":
        sys_msg = (
            "Return JSON ONLY: {\"questions\": [{\"question\":\"...\",\"options\":[...],\"correct_index\":0,\"explanation\":\"...\"}, ...]}.\n"
            + QUALITY_GUIDELINES +
            "\nConstraints:\n"
            f"- Exactly one correct option per question, total options = {mcq_options}.\n"
            "- Mix difficulty: ~40% easy, ~40% medium, ~20% challenging.\n"
            "- Options must be plausible; avoid giveaways like length or grammar.\n"
            "- Include a brief explanation focusing on misconception busting.\n"
            "- If a definition is tested and KNOWN VERBATIM DEFINITIONS include it, the correct option MUST contain that exact string.\n"
        )
        user_payload = {
            "subject": subject,
            "audience": audience,
            "num_questions": int(num_questions or 8),
            "mcq_options": int(mcq_options or 4),
            "notes": notes_json,
            "verbatim_definitions": verbatim_definitions or [],
        }
    else:
        sys_msg = (
            "Return JSON ONLY: {\"questions\": [{\"question\":\"...\",\"model_answer\":\"...\",\"markscheme_points\":[\"...\"]}, ...]}.\n"
            + QUALITY_GUIDELINES +
            "\nConstraints:\n"
            "- Exam-style phrasing; point-marked. Provide concise, stepwise markscheme points.\n"
            "- Mix difficulty: ~40% recall, ~40% application, ~20% problem solving.\n"
            "- Prefer questions whose answers are demonstrably present/derivable from the notes.\n"
            "- If a definition is tested and KNOWN VERBATIM DEFINITIONS include it, the model_answer MUST contain that exact string.\n"
        )
        user_payload = {
            "subject": subject,
            "audience": audience,
            "num_questions": int(num_questions or 8),
            "notes": notes_json,
            "verbatim_definitions": verbatim_definitions or [],
        }

    resp = client.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=2300,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": instruction},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    questions = data.get("questions") or []

    # light shape check
    out: List[Dict[str, Any]] = []
    if mode == "mcq":
        m = max(3, min(6, int(mcq_options or 4)))
        for q in questions:
            ques = (q.get("question") or "").trim() if hasattr(str, "trim") else (q.get("question") or "").strip()
            opts = q.get("options") or []
            ci   = q.get("correct_index", -1)
            exp  = (q.get("explanation") or "").strip()
            if ques and isinstance(opts, list) and len(opts) == m and 0 <= ci < len(opts):
                out.append({"question": ques, "options": opts, "correct_index": ci, "explanation": exp})
    else:
        for q in questions:
            ques = (q.get("question") or "").trim() if hasattr(str, "trim") else (q.get("question") or "").strip()
            ans  = (q.get("model_answer") or "").strip()
            pts  = q.get("markscheme_points") or []
            if ques and ans and isinstance(pts, list):
                out.append({"question": ques, "model_answer": ans, "markscheme_points": pts})
    return out


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

