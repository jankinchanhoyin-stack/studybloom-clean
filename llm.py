# llm.py
import os, json, hashlib, asyncio
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
import sympy as sp

# ---------- Models ----------
FAST_MODEL  = os.getenv("MODEL_FAST",  "gpt-4o-mini")
SMART_MODEL = os.getenv("MODEL_SMART", "gpt-4o")

client_async = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Tiny in-memory cache (swap to Redis/Supabase if needed) ----------
_CACHE: Dict[str, Any] = {}

def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---------- Simple splitter (char-based; replace with token splitter if you prefer) ----------
def chunk_text(text: str, target_chars: int = 8000, overlap: int = 400) -> List[str]:
    text = text.strip()
    if len(text) <= target_chars:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        j = min(len(text), i + target_chars)
        chunks.append(text[i:j])
        i = j - overlap
        if i < 0:
            i = 0
        if i >= len(text):
            break
    return chunks

# ---------- Chunk summarization (parallel, cached) ----------
async def _summ_chunk(text: str, audience: str, detail: int) -> Dict[str, Any]:
    key = f"summ:{_h(text)}:{audience}:{detail}"
    if key in _CACHE:
        return _CACHE[key]
    resp = await client_async.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=450,
        messages=[
            {
                "role": "system",
                "content": (
                    "Return JSON ONLY. Keys: bullets(array of strings), "
                    "key_terms(array of {term,definition})."
                ),
            },
            {
                "role": "user",
                "content": f"Audience:{audience}\nDetail:{detail}\nText:\n{text}",
            },
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    _CACHE[key] = data
    return data

async def summarize_text_fast(text: str, audience="high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    chunks = chunk_text(text, target_chars=8000, overlap=400)
    tasks = [_summ_chunk(c, audience, detail) for c in chunks]
    parts = await asyncio.gather(*tasks)

    merged = await client_async.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=900,
        messages=[
            {
                "role": "system",
                "content": (
                    "Fuse the inputs into concise, accurate study notes for the given subject."
                    "Return JSON ONLY with keys:"
                    " tl_dr(string), "
                    " sections(array of {heading,bullets}), "
                    " key_terms(array of {term,definition}), "
                    " formulas(optional array of {name,latex,meaning}), "
                    " examples(optional array), "
                    " common_pitfalls(optional array), "
                    " flashcards(optional array of {front,back}), "
                    " exam_questions(optional array)."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "subject": subject,
                        "audience": audience,
                        "detail": detail,
                        "parts": parts,
                    }
                ),
            },
        ],
    )
    return json.loads(merged.choices[0].message.content)

# Back-compat wrapper (some places call summarize_text)
def summarize_text(text: str, audience="high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    return asyncio.run(summarize_text_fast(text, audience=audience, detail=detail, subject=subject))

# ---------- Flashcards & Quizzes ----------
async def generate_flashcards_from_notes(notes_json: Dict[str, Any], audience="high school") -> List[Dict[str, str]]:
    key = f"fc:{_h(json.dumps(notes_json))}:{audience}"
    if key in _CACHE:
        return _CACHE[key]
    resp = await client_async.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=600,
        messages=[
            {"role": "system", "content": "Return JSON ONLY: flashcards(array of {front,back})."},
            {"role": "user", "content": json.dumps({"audience": audience, "notes": notes_json})},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    cards = data.get("flashcards") or []
    _CACHE[key] = cards
    return cards

async def generate_quiz_from_notes_async(
    notes_json: Dict[str, Any],
    subject="General",
    audience="high school",
    num_questions: int = 8,
    mode: str = "free",    # "free" or "mcq"
    mcq_options: int = 4,
) -> List[Dict[str, Any]]:
    """
    For free-response: return [{question, model_answer, markscheme_points[]}]
    For MCQ: return [{question, options[], correct_index, explanation}]
    """
    key = f"quiz:{mode}:{num_questions}:{mcq_options}:{subject}:{audience}:{_h(json.dumps(notes_json))}"
    if key in _CACHE:
        return _CACHE[key]

    if mode == "mcq":
        sys_msg = (
            "Return JSON ONLY: questions(array of {question, options(array), correct_index(int), explanation}). "
            "Only ask things that can be answered by choosing an option."
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
            "Questions should be exam-style, marked by points."
        )
        user_payload = {
            "subject": subject,
            "audience": audience,
            "num_questions": num_questions,
            "notes": notes_json,
        }

    resp = await client_async.chat.completions.create(
        model=FAST_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=900,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    out = data.get("questions") or []
    _CACHE[key] = out
    return out

def generate_quiz_from_notes(notes_json: Dict[str, Any], subject="General", audience="high school", num_questions: int = 8):
    # kept for older calls (free-response default)
    return asyncio.run(generate_quiz_from_notes_async(notes_json, subject, audience, num_questions, "free", 4))

# ---------- Hybrid grading (math local first, then LLM) ----------
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

async def _grade_free_llm_async(q, model_answer, markscheme, user_answer, subject="General"):
    resp = await client_async.chat.completions.create(
        model=SMART_MODEL,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=400,
        messages=[
            {"role": "system",
             "content": "Return JSON ONLY: {score:int,max_points:int,feedback:string}. Use the mark scheme."},
            {"role": "user",
             "content": json.dumps({
                 "subject": subject,
                 "question": q,
                 "model_answer": model_answer,
                 "markscheme_points": markscheme,
                 "user_answer": user_answer
             })}
        ],
    )
    return json.loads(resp.choices[0].message.content)

def grade_free_answer(q, model_answer, markscheme, user_answer, subject="General"):
    return asyncio.run(_grade_free_llm_async(q, model_answer, markscheme, user_answer, subject))

def grade_free_answer_fast(q, model_answer, markscheme, user_answer, subject="General"):
    if subject.lower().startswith("math"):
        eq = try_grade_math_numeric(user_answer, model_answer)
        if eq is None: eq = try_grade_math_expr(user_answer, model_answer)
        if eq is not None:
            return {"score": 10 if eq else 0, "max_points": 10, "feedback": "Auto-graded (math equivalence)."}
    return grade_free_answer(q, model_answer, markscheme, user_answer, subject=subject)







