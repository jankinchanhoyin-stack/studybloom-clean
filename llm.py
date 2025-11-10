# llm.py
import os, json, hashlib, asyncio
from typing import List, Dict, Any, Optional
from collections import OrderedDict

import nest_asyncio
from openai import AsyncOpenAI
import sympy as sp

# ---------- Model choices ----------
FAST_MODEL  = os.getenv("MODEL_FAST",  "gpt-4o-mini")
SMART_MODEL = os.getenv("MODEL_SMART", "gpt-4o")

client_async = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ---------- Safe async runner for Streamlit ----------
def run_async(coro):
    """
    Run a coroutine safely in Streamlit (works whether an event loop is already running).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        nest_asyncio.apply(loop)
        return loop.run_until_complete(coro)
    else:
        return loop.run_until_complete(coro)

# ---------- Size & concurrency guards ----------
MAX_INPUT_CHARS = 200_000    # hard cap per upload
CHUNK_CHARS     = 8_000
CHUNK_OVERLAP   = 400
PARALLEL_LIMIT  = 6          # avoid too many concurrent requests

# ---------- Tiny LRU cache ----------
_CACHE: "OrderedDict[str, Any]" = OrderedDict()
CACHE_LIMIT = 100

def _cache_get(k: str):
    if k in _CACHE:
        v = _CACHE.pop(k)
        _CACHE[k] = v
        return v
    return None

def _cache_set(k: str, v: Any):
    _CACHE[k] = v
    _CACHE.move_to_end(k)
    if len(_CACHE) > CACHE_LIMIT:
        _CACHE.popitem(last=False)

def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---------- Chunking ----------
def chunk_text(text: str, target_chars: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    text = (text or "").strip()
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    if len(text) <= target_chars:
        return [text]
    chunks, i = [], 0
    while i < len(text):
        j = min(len(text), i + target_chars)
        chunks.append(text[i:j])
        i = j - overlap
        if i < 0: i = 0
        if i >= len(text): break
    return chunks

# ---------- Chunk summarization (parallel, cached) ----------
async def _summ_chunk(text: str, audience: str, detail: int) -> Dict[str, Any]:
    key = f"summ:{_h(text)}:{audience}:{detail}"
    c = _cache_get(key)
    if c is not None:
        return c
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
    _cache_set(key, data)
    return data

async def summarize_text_fast(text: str, audience="high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    chunks = chunk_text(text)
    sem = asyncio.Semaphore(PARALLEL_LIMIT)

    async def _bounded(c):
        async with sem:
            return await _summ_chunk(c, audience, detail)

    parts = await asyncio.gather(*[_bounded(c) for c in chunks])

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
                    {"subject": subject, "audience": audience, "detail": detail, "parts": parts}
                ),
            },
        ],
    )
    return json.loads(merged.choices[0].message.content)

# Back-compat sync wrapper (uses safe run_async)
def summarize_text(text: str, audience="high school", detail: int = 3, subject: str = "General") -> Dict[str, Any]:
    return run_async(summarize_text_fast(text, audience=audience, detail=detail, subject=subject))

# ---------- Flashcards & Quizzes ----------
async def generate_flashcards_from_notes(notes_json: Dict[str, Any], audience="high school") -> List[Dict[str, str]]:
    key = f"fc:{_h(json.dumps(notes_json, sort_keys=True))}:{audience}"
    c = _cache_get(key)
    if c is not None:
        return c
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
    _cache_set(key, cards)
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
    For free-response: [{question, model_answer, markscheme_points[]}]
    For MCQ:           [{question, options[], correct_index, explanation}]
    """
    key = f"quiz:{mode}:{num_questions}:{mcq_options}:{subject}:{audience}:{_h(json.dumps(notes_json, sort_keys=True))}"
    c = _cache_get(key)
    if c is not None:
        return c

    if mode == "mcq":
        sys_msg = (
            "Return JSON ONLY: questions(array of {question, options(array), correct_index(int), explanation}). "
            "Only ask things answerable by choosing one option."
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
            "Questions should be exam-style, point-marked."
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
    _cache_set(key, out)
    return out

def generate_quiz_from_notes(notes_json: Dict[str, Any], subject="General", audience="high school", num_questions: int = 8):
    # free-response default
    return run_async(generate_quiz_from_notes_async(notes_json, subject, audience, num_questions, "free", 4))

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

def grade_free_answer(q, model_answer, markscheme, user_answer, subject="General"):
    return run_async(_grade_free_llm_async(q, model_answer, markscheme, user_answer, subject))

def grade_free_answer_fast(q, model_answer, markscheme, user_answer, subject="General"):
    if subject.lower().startswith("math"):
        eq = try_grade_math_numeric(user_answer, model_answer)
        if eq is None: eq = try_grade_math_expr(user_answer, model_answer)
        if eq is not None:
            return {"score": 10 if eq else 0, "max_points": 10, "feedback": "Auto-graded (math equivalence)."}
    return grade_free_answer(q, model_answer, markscheme, user_answer, subject=subject)





