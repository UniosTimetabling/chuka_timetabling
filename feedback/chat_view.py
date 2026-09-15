"""
Universal Intelligent Chatbot for Chuka University
Version 17.0 - ProgramCourse parser layer + DB-registered AI provider + polished responses
"""

import json
import re
import ast
import operator
import html as _html
import logging
from collections import defaultdict
from datetime import datetime, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
import time as time_module
import random
import hashlib
from difflib import get_close_matches, SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.core.cache import cache
from .models import Feedback
from .feedback_logger import log_chat_query, log_feedback_submit

logger = logging.getLogger(__name__)

# ── AI registry (DB-registered provider) ──────────────────────────────────────
try:
    from core.ai_registry import get_ai_client, AIError
    AI_REGISTRY_AVAILABLE = True
    logger.info("AI registry imported successfully")
except ImportError as e:
    AI_REGISTRY_AVAILABLE = False
    get_ai_client = lambda **kw: None
    AIError = Exception
    logger.warning(f"AI registry not available: {e}")

# ── Chatbot snapshot models ────────────────────────────────────────────────────
try:
    from .chatbot_models import (
        ChatbotTimetableEntry,
        ChatbotPublication,
        ChatConversationLog,
    )
    CHATBOT_MODELS_AVAILABLE = True
    CHAT_LOG_AVAILABLE = True
    logger.info("Chatbot models imported successfully")
except ImportError as e:
    CHATBOT_MODELS_AVAILABLE = False
    CHAT_LOG_AVAILABLE = False
    ChatConversationLog = None
    logger.warning(f"Chatbot models not available: {e}")

# ── Program / ProgramCourse / CourseAllocation models ─────────────────────────
try:
    from program_management.models import Program, ProgramCourse
    from course_allocation.models import CourseAllocation
    PROGRAM_MODELS_AVAILABLE = True
    logger.info("Program models imported successfully")
except ImportError as e:
    PROGRAM_MODELS_AVAILABLE = False
    logger.warning(f"Program models not available: {e}")

# ── Room/Venue master inventory ───────────────────────────────────────────────
# This is the ACTUAL physical room list (with buildings/capacity), separate
# from ChatbotTimetableEntry. Without this, "free venues" can only ever look
# at venues that happen to appear in the timetable snapshot — a room that
# exists but simply isn't scheduled anywhere would never be reported as free.
try:
    from room_management.models import Venue
    ROOM_MODELS_AVAILABLE = True
    logger.info("Room management Venue model imported successfully")
except ImportError as e:
    ROOM_MODELS_AVAILABLE = False
    logger.warning(f"Room management models not available: {e}")


def _norm_venue(code) -> str:
    """Normalize a venue code for comparison: 'BSL 303' and 'bsl303' match."""
    return re.sub(r'\s+', '', str(code)).upper()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0B — ProgramCourse Parser Layer
# Sits between user input and DB/AI.  Responsibilities:
#  1. Normalize the raw input to a candidate course code.
#  2. Fuzzy-match against ProgramCourse (cached in-memory, refreshed every 5 min).
#  3. Return (matched_pc, confidence) or (None, 0) for AI fallback.
# ══════════════════════════════════════════════════════════════════════════════

_PC_CACHE_KEY = "chatbot_program_course_cache"
_PC_CACHE_TTL = 300  # seconds


def _build_pc_index():
    """
    Pull all ProgramCourse rows once and build lookup structures.
    Returns a dict:
    {
      'exact': { 'COSC 301': <ProgramCourse obj>, ... },        # normalised → obj
      'codes': ['COSC 301', 'NURS 204', ...],                   # list for fuzzy
    }
    """
    if not PROGRAM_MODELS_AVAILABLE:
        return {'exact': {}, 'codes': []}
    try:
        pcs = list(
            ProgramCourse.objects
            .select_related('program')
            .values('id', 'course_code', 'course_name', 'year', 'semester',
                    'program__name', 'program__id')
        )
        exact = {}
        for pc in pcs:
            norm = _norm_code(pc['course_code'])
            exact[norm] = pc
        return {'exact': exact, 'codes': list(exact.keys())}
    except Exception as e:
        logger.error(f"Error building PC index: {e}")
        return {'exact': {}, 'codes': []}


def _get_pc_index():
    """Return cached index; rebuild if stale."""
    idx = cache.get(_PC_CACHE_KEY)
    if idx is None:
        idx = _build_pc_index()
        cache.set(_PC_CACHE_KEY, idx, _PC_CACHE_TTL)
    return idx


def _norm_code(raw: str) -> str:
    """Normalize a course code for matching:  'cosc301' → 'COSC 301'"""
    s = raw.upper().strip()
    s = re.sub(r'\s+', '', s)                # remove spaces
    s = re.sub(r'\([A-Z0-9]\)$', '', s)      # strip (A) suffixes
    s = re.sub(r'([A-Z]+)(\d+)[A-Z]?$', r'\1 \2', s)  # insert space: COSC301 → COSC 301
    return s.strip()


_CODE_SPLIT_RE = re.compile(r'^([A-Z]+)\s*(\d+)$')


def _safe_fuzzy_code_match(candidate: str, codes, letter_ratio_cutoff: float = 0.6):
    """
    Fuzzy-match a course-code candidate against known codes WITHOUT letting
    shared digits paper over a wrong department prefix.

    Naive difflib similarity on the whole string ('ASC345' vs 'SOCI345')
    scores deceptively high because the digits dominate the ratio, so a
    typo'd/garbled department code can silently resolve to a completely
    different course that just happens to share the same course number
    (e.g. 'ASC 345' -> 'SOCI 345', 'HDFT 345' -> 'MATH 345'). That is worse
    than not answering at all.

    Fix: only ever fuzz the letter prefix, and require the course NUMBER
    to match exactly - the number is the one part a student is unlikely to
    misremember, while the short department abbreviation is exactly what
    gets mistyped. If the letter-prefix similarity doesn't clear the
    cutoff even with matching digits, we refuse to guess.
    """
    cm = _CODE_SPLIT_RE.match(candidate.strip())
    if not cm:
        return None
    letters, digits = cm.group(1), cm.group(2)
    best = None
    best_ratio = 0.0
    for code in codes:
        km = _CODE_SPLIT_RE.match(code.strip())
        if not km:
            continue
        k_letters, k_digits = km.group(1), km.group(2)
        if k_digits != digits:
            continue
        ratio = SequenceMatcher(None, letters, k_letters).ratio()
        if ratio >= letter_ratio_cutoff and ratio > best_ratio:
            best, best_ratio = code, ratio
    return best


def _kw_regex(words):
    """
    Compile a case-insensitive, WORD-BOUNDARY-safe alternation from a list of
    keywords/phrases. Using this instead of `any(w in text for w in words)`
    prevents false positives like the literal substring 'cat' (Continuous
    Assessment Test) matching inside 'communicate', or 'time' matching
    inside 'timetabling', or 'form' matching inside 'information'.

    A plain trailing \\b is too strict though — it would also stop 'venue'
    from matching inside its own plural 'venues', or 'class' inside
    'classes'. So the last word of each phrase gets an optional s/es/ed/ing
    suffix before the closing boundary, which covers ordinary English
    inflection without reopening the substring-false-positive problem
    (the boundary still has to hold at the START of the match).
    """
    parts = []
    for w in words:
        segments = w.strip().split(' ')
        last = re.escape(segments[-1])
        prefix = ' '.join(re.escape(s) for s in segments[:-1])
        pat = (prefix + ' ' if prefix else '') + last + r'(?:s|es|ed|ing)?'
        parts.append(pat)
    parts.sort(key=len, reverse=True)
    return re.compile(r'\b(?:' + '|'.join(parts) + r')\b', re.IGNORECASE)


class ProgramCourseParser:
    """
    Lightweight parser layer that checks user input against ProgramCourse
    before reaching the AI or ChatbotTimetableEntry.

    Usage:
        parser = ProgramCourseParser()
        result = parser.parse(user_message)
        # result: {
        #   'matched': True/False,
        #   'course_code': 'COSC 301',     # canonical DB code
        #   'course_name': 'Data Structures',
        #   'program_name': 'BSc Computer Science',
        #   'year': 2,
        #   'semester': 1,
        #   'confidence': 1.0 | 0.85 | 0.0,
        #   'raw_input': str,
        # }
    """

    # Regex to extract raw code candidates from free text
    _CODE_RE = re.compile(r'\b([A-Z]{2,6})\s*(\d{3,6})\b', re.IGNORECASE)

    def parse(self, message: str) -> dict:
        idx = _get_pc_index()
        candidates = self._extract_candidates(message)

        for raw_candidate in candidates:
            norm = _norm_code(raw_candidate)

            # 1. Exact match
            if norm in idx['exact']:
                pc = idx['exact'][norm]
                return self._hit(pc, raw_candidate, 1.0)

            # 2. Prefix / partial scan (e.g. 'COSC30' → 'COSC 301')
            for key, pc in idx['exact'].items():
                if key.startswith(norm) or norm.startswith(key):
                    return self._hit(pc, raw_candidate, 0.9)

            # 3. Fuzzy match — letter-prefix similarity only, digits must match
            #    exactly (see _safe_fuzzy_code_match for why: naive whole-string
            #    fuzzing lets shared digits mask a wrong department guess).
            if idx['codes']:
                close = _safe_fuzzy_code_match(norm, idx['codes'])
                if close:
                    pc = idx['exact'][close]
                    return self._hit(pc, raw_candidate, 0.80)

        return {'matched': False, 'confidence': 0.0, 'raw_input': message}

    def _extract_candidates(self, text: str) -> list:
        matches = self._CODE_RE.findall(text.upper())
        return [f"{dept} {num}" for dept, num in matches]

    @staticmethod
    def _hit(pc: dict, raw: str, confidence: float) -> dict:
        return {
            'matched': True,
            'course_code': pc['course_code'],
            'course_name': pc['course_name'],
            'program_name': pc['program__name'],
            'year': pc['year'],
            'semester': pc['semester'],
            'confidence': confidence,
            'raw_input': raw,
        }


# Singleton
_program_course_parser = ProgramCourseParser()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0C — AI Polish / Fallback helpers (all via DB-registered provider)
# ══════════════════════════════════════════════════════════════════════════════

_POLISH_SYSTEM = (
    "You are ChukaBot, the Chuka University timetable assistant. "
    "Your job is to rewrite raw timetable data into a friendly, clearly formatted "
    "response for a student. Keep it concise. Use emojis sparingly. "
    "Never make up data — only use what is provided."
)

_FALLBACK_SYSTEM = (
    "You are ChukaBot, the Chuka University timetable assistant. "
    "A student typed a course query but the course was not found in the local database. "
    "Your task: identify the most likely intended course code from the student's message "
    "and return ONLY a JSON object like:\n"
    '{"course_code": "COSC 301", "confidence": 0.85}\n'
    "If you cannot determine a course code, return:\n"
    '{"course_code": null, "confidence": 0}\n'
    "Return JSON only — no prose, no markdown fences."
)


def _ai_polish(raw_db_text: str, user_query: str) -> str:
    """
    Send raw timetable text to the DB-registered AI provider to produce a
    polished, friendly response.  Falls back to the raw text if AI unavailable.
    """
    if not AI_REGISTRY_AVAILABLE:
        return raw_db_text
    client = get_ai_client()
    if client is None:
        return raw_db_text
    prompt = (
        f"{_POLISH_SYSTEM}\n\n"
        f"Student query: {user_query}\n\n"
        f"Raw timetable data:\n{raw_db_text}\n\n"
        "Write a friendly response using the data above:"
    )
    try:
        return client.call(prompt)
    except AIError as e:
        logger.warning(f"AI polish failed: {e}")
        return raw_db_text


def _ai_guess_course_code(user_query: str) -> str | None:
    """
    Ask the DB-registered AI to identify the most likely course code from a
    query that failed parser + DB lookup.  Returns a normalised code or None.
    """
    if not AI_REGISTRY_AVAILABLE:
        return None
    client = get_ai_client()
    if client is None:
        return None
    prompt = (
        f"{_FALLBACK_SYSTEM}\n\n"
        f"Student message: {user_query}"
    )
    try:
        data = client.call_json(prompt)
        code = data.get('course_code')
        confidence = float(data.get('confidence', 0))
        if code and confidence >= 0.6:
            return _norm_code(str(code))
        return None
    except Exception as e:
        logger.warning(f"AI course-code guess failed: {e}")
        return None


def _about_bot_answer() -> str:
    return """🤖 About ChukaBot 🤖

I'm ChukaBot, an AI assistant for Chuka University students.

What I can do:
📚 Timetables — individual courses, multiple courses
🎓 Programs — all courses in a program or year
👨‍🏫 Lecturers — courses taught by a specific lecturer
🏛️ Venues — find free venues at specific times
🌍 Web Search — general knowledge questions
🧮 Math — calculations
📰 News — latest Kenya news
💡 Fun Facts — interesting trivia
🎓 University Info — VC, location, faculties, admissions

Examples: "when is COSC 301?", "courses in Computer Science", "who teaches NURS 204?", "free venues on Monday at 7 AM"

What would you like to know?"""


_ROUTING_SYSTEM = (
    "You are a routing classifier sitting in front of a university timetabling "
    "chatbot's live public web search. The chatbot is about to forward a "
    "student's raw message to a web search engine because nothing else matched. "
    "Before that happens, classify the message by MEANING (not literal words) "
    "into exactly one label:\n"
    "INAPPROPRIATE — profanity, slurs, harassment, or insults (directed at the "
    "bot, at people, or in general), in any spelling, spacing, or language.\n"
    "ABOUT_BOT — the user is asking about the chatbot/assistant itself: what it "
    "is, who built it, what model or company powers it, whether it's AI, etc. "
    "— in ANY phrasing.\n"
    "SEARCHABLE — a genuine question or statement that is fine to look up on "
    "the public web.\n"
    'Return ONLY JSON like: {"category": "INAPPROPRIATE"}  — no prose, no markdown.'
)


def _ai_route_before_search(user_query: str) -> str:
    """
    The one general gate that stands between "nothing else matched" and
    "forward this raw text to a live public search engine".

    A hand-maintained list of bad words/phrases (what used to live here)
    is structurally always one step behind: it catches 'fuck' but not
    'fuckoff', catches common insults but not slurs nobody thought to list,
    and catches 'who are you' but not 'who powers you' or 'so you're powered
    by grok'. Those are all the SAME underlying problem — matching literal
    substrings instead of understanding intent — so patching one phrase at a
    time never closes it. This asks the model already available in this
    system to classify by meaning instead, which generalizes to phrasings
    nobody has seen yet.

    Fails safe to 'SEARCHABLE' (i.e. behaves like before) if the AI is
    unavailable or errors, so this never blocks normal search traffic.
    """
    if not AI_REGISTRY_AVAILABLE:
        return 'SEARCHABLE'
    client = get_ai_client()
    if client is None:
        return 'SEARCHABLE'
    prompt = f"{_ROUTING_SYSTEM}\n\nStudent message: {user_query}"
    try:
        data = client.call_json(prompt)
        category = str(data.get('category', 'SEARCHABLE')).upper().strip()
        if category not in ('INAPPROPRIATE', 'ABOUT_BOT', 'SEARCHABLE'):
            return 'SEARCHABLE'
        return category
    except Exception as e:
        logger.warning(f"AI pre-search routing check failed: {e}")
        return 'SEARCHABLE'


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0A — Input Intelligence (unchanged from v16)
# ══════════════════════════════════════════════════════════════════════════════

class InputIntelligence:
    ABBREVIATIONS: dict = {
        "db": "database systems", "dbms": "database management systems",
        "oop": "object oriented programming", "ds": "data structures",
        "os": "operating systems", "cn": "computer networks",
        "se": "software engineering", "ai": "artificial intelligence",
        "ml": "machine learning", "dl": "deep learning",
        "hci": "human computer interaction", "is": "information systems",
        "it": "information technology", "dsa": "data structures and algorithms",
        "algo": "algorithms", "maths": "mathematics", "stats": "statistics",
        "calc": "calculus", "bio": "biology", "chem": "chemistry",
        "phys": "physics", "eco": "economics", "acc": "accounting",
        "fin": "finance", "mgt": "management", "comm": "communication",
        "eng": "english", "comp": "computer science", "cs": "computer science",
        "bsc cs": "bachelor of science computer science", "cosc": "computer science",
        "econ": "economics", "nurs": "nursing",
        "tmrw": "tomorrow", "tmr": "tomorrow", "tom": "tomorrow", "nxt": "next",
        "asubuhi": "morning", "mchana": "afternoon", "jioni": "evening", "usiku": "night",
        "lec": "lecture", "tut": "tutorial", "lab": "laboratory",
        "prac": "practical", "cat": "continuous assessment test",
        "gp": "group project", "tt": "timetable",
    }
    SWAHILI_MAP: dict = {
        "niko na": "i have", "nina": "i have", "niko": "i am",
        "iko wapi": "where is", "iko": "there is", "lini": "when is",
        "saa ngapi": "what time", "gani": "which", "leo": "today",
        "kesho": "tomorrow", "wiki hii": "this week", "wiki ijayo": "next week",
        "jana": "yesterday", "free": "free", "darasa": "class", "somo": "class",
        "masomo": "classes", "mwalimu": "lecturer", "profesa": "professor",
        "mtihani": "exam", "chumba": "room", "ukumbi": "hall",
        "acha": "skip", "niambie": "tell me", "nisaidie": "help me",
        "tafadhali": "please", "asante": "thank you", "sawa": "okay",
        "ndio": "yes", "hapana": "no", "class yangu": "my class",
        "mbona": "why", "aje": "how", "si": "isn't it",
    }
    LECTURER_TITLES = re.compile(r'\b(dr|prof|mr|mrs|ms|professor|mwalimu)\b', re.IGNORECASE)
    ROOM_PATTERNS = re.compile(r'\b(room|hall|lab|venue|block)\s+[A-Z0-9]+\b', re.IGNORECASE)
    DAY_MAP: dict = {
        "monday": "Monday", "mon": "Monday",
        "tuesday": "Tuesday", "tue": "Tuesday", "tues": "Tuesday",
        "wednesday": "Wednesday", "wed": "Wednesday",
        "thursday": "Thursday", "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
        "friday": "Friday", "fri": "Friday",
        "saturday": "Saturday", "sat": "Saturday",
        "sunday": "Sunday", "sun": "Sunday",
        "today": "__today__", "tomorrow": "__tomorrow__",
        "leo": "__today__", "kesho": "__tomorrow__", "jana": "__yesterday__",
    }
    TIME_RANGES: dict = {
        "morning": (6, 12), "afternoon": (12, 17),
        "evening": (17, 21), "night": (21, 24),
        "asubuhi": (6, 12), "mchana": (12, 17), "jioni": (17, 21),
    }
    BROAD_SIGNALS = re.compile(
        r'\b(all|every|entire|whole|full|complete|semester|everything|show all)\b', re.IGNORECASE
    )
    MULTI_INTENT_SPLITTERS = re.compile(
        r'\band\s+(?:where|when|who|what|how|which)\b|\balso\b|\bplus\b|(?<=\?)\s+',
        re.IGNORECASE
    )
    SPELLCHECK_VOCAB: list = [
        "database", "systems", "networks", "algorithms", "programming",
        "engineering", "software", "artificial", "intelligence", "machine",
        "learning", "mathematics", "statistics", "physics", "chemistry",
        "biology", "economics", "accounting", "management", "communication",
        "computer", "science", "nursing", "education", "timetable", "schedule",
        "lecturer", "lecture", "tutorial", "laboratory", "practical",
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "morning", "afternoon", "evening", "tomorrow", "today",
        "exam", "assessment", "test", "class", "course", "unit",
        "year", "semester", "program", "bachelor", "master", "diploma",
    ]
    STOPWORDS: set = {
        'is', 'it', 'in', 'on', 'at', 'to', 'do', 'of', 'or', 'an', 'a',
        'be', 'by', 'me', 'my', 'he', 'we', 'us', 'up', 'no', 'so', 'if',
        'as', 'am', 'ok', 'go',
    }

    # Matches DEPT+NUMBER tokens like 'cosc 341', 'cosc341', 'hdft345' — run
    # BEFORE abbreviation expansion / spellcheck so a department code never
    # gets rewritten into its expanded meaning (e.g. 'cosc' -> 'computer
    # science') just because it also happens to be a known abbreviation.
    # Course-code protection must happen first, or the digits that make it
    # identifiable as a code are still attached when we check.
    _COURSE_CODE_TOKEN_RE = re.compile(r'\b[a-z]{2,6}\s*\d{3,6}[a-z]?\b')

    def _protect_course_codes(self, text):
        placeholders = {}
        def _sub(m):
            token = f"xcodeplaceholderx{len(placeholders)}x"
            placeholders[token] = m.group(0)
            return token
        protected = self._COURSE_CODE_TOKEN_RE.sub(_sub, text)
        return protected, placeholders

    def _restore_course_codes(self, text, placeholders):
        for token, original in placeholders.items():
            text = text.replace(token, original)
        return text

    def preprocess(self, raw: str) -> dict:
        text = self._normalize(raw)
        text, _code_placeholders = self._protect_course_codes(text)
        text = self._translate_swahili(text)
        text = self._expand_abbreviations(text)
        text = self._correct_spelling(text)
        text = self._restore_course_codes(text, _code_placeholders)
        return {
            'cleaned': text, 'original': raw,
            'day': self._extract_day(text),
            'time_range': self._extract_time_range(text),
            'is_broad': bool(self.BROAD_SIGNALS.search(text)),
            'sub_queries': self._split_intents(text),
            'entity_hint': self._detect_entity_hint(text),
            'is_missing_context': self._is_missing_context(text),
        }

    def _normalize(self, text): return re.sub(r'\s+', ' ', text.lower()).strip()
    def _translate_swahili(self, text):
        for sw, en in sorted(self.SWAHILI_MAP.items(), key=lambda x: -len(x[0])):
            text = text.replace(sw, en)
        return text
    def _expand_abbreviations(self, text):
        words = text.split(); expanded = []; i = 0
        while i < len(words):
            if i + 1 < len(words):
                two = f"{words[i]} {words[i+1]}"
                if two in self.ABBREVIATIONS:
                    expanded.append(self.ABBREVIATIONS[two]); i += 2; continue
            single = words[i]
            expanded.append(single if single in self.STOPWORDS else self.ABBREVIATIONS.get(single, single))
            i += 1
        return ' '.join(expanded)
    def _correct_spelling(self, text):
        words = text.split(); corrected = []
        for word in words:
            if len(word) <= 3 or word.isdigit() or re.match(r'^[a-z]{2,5}\d{3,5}$', word):
                corrected.append(word); continue
            matches = get_close_matches(word, self.SPELLCHECK_VOCAB, n=1, cutoff=0.82)
            corrected.append(matches[0] if matches else word)
        return ' '.join(corrected)
    def _extract_day(self, text):
        today = datetime.now()
        for token, resolved in self.DAY_MAP.items():
            if re.search(rf'\b{re.escape(token)}\b', text):
                if resolved == '__today__': return today.strftime('%A')
                if resolved == '__tomorrow__':
                    import datetime as _dt
                    return (today + _dt.timedelta(days=1)).strftime('%A')
                if resolved == '__yesterday__':
                    import datetime as _dt
                    return (today - _dt.timedelta(days=1)).strftime('%A')
                return resolved
        return None
    def _extract_time_range(self, text):
        for period, hours in self.TIME_RANGES.items():
            if period in text: return hours
        m = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', text)
        if m:
            hour = int(m.group(1)); ampm = m.group(3) or ''
            if ampm == 'pm' and hour != 12: hour += 12
            elif ampm == 'am' and hour == 12: hour = 0
            return (hour, hour + 1)
        return None
    def _detect_entity_hint(self, text):
        if self.LECTURER_TITLES.search(text): return 'lecturer'
        if self.ROOM_PATTERNS.search(text): return 'room'
        return None
    def _split_intents(self, text):
        parts = self.MULTI_INTENT_SPLITTERS.split(text)
        parts = [p.strip() for p in parts if p and len(p.strip()) > 4]
        return parts if len(parts) > 1 else []
    def _is_missing_context(self, text):
        vague = [r'^(when is my class|what do i have today|next lecture|my schedule|'
                 r'my timetable|show my classes|what class|which class|niko na class gani)$']
        return any(re.match(p, text.strip()) for p in vague)


input_intelligence = InputIntelligence()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 0 — Smart Local DB Matcher (unchanged, but now used only after parser)
# ══════════════════════════════════════════════════════════════════════════════

class SmartLocalMatcher:
    # Ordered so multi-word forms are tried before abbreviations that could
    # otherwise partially overlap (e.g. "master of" before "msc").
    _QUALIFIER_DEFS = [
        (re.compile(r'^bachelor(?:s)?\s+of\b'), 'bachelor'),
        (re.compile(r'^b\.?\s*sc\b'), 'bachelor'),
        (re.compile(r'^b\.?\s*a\b'), 'bachelor'),
        (re.compile(r'^bed\b'), 'bachelor'),
        (re.compile(r'^bcom\b'), 'bachelor'),
        (re.compile(r'^masters?(?:\s+(?:in|of))?\b'), 'masters'),
        (re.compile(r'^m\.?\s*sc\b'), 'masters'),
        (re.compile(r'^doctor(?:\s+of)?\b'), 'phd'),
        (re.compile(r'^phd(?:\s*in)?\b'), 'phd'),
        (re.compile(r'^diploma(?:\s+in)?\b'), 'diploma'),
        (re.compile(r'^certificate(?:\s+in)?\b'), 'certificate'),
    ]
    # Same qualifiers, used to strip a level phrase off the front of a query
    # so the remaining text is pure subject matter (e.g. "bsc computer
    # science" -> "computer science") while still remembering what level
    # the user asked for.
    _QUALIFIER_STRIP_RE = re.compile(
        r'^(bachelor(?:s)?\s+of|masters?(?:\s+(?:in|of))?|doctor(?:\s+of)?|'
        r'b\.?\s*sc|b\.?\s*a|bed|bcom|m\.?\s*sc|phd(?:\s*in)?|'
        r'diploma(?:\s+in)?|certificate(?:\s+in)?)\s*\.?\s*'
    )
    # A few abbreviations conventionally imply their own subject when typed
    # completely bare, with nothing else in the query (e.g. "bcom" on its
    # own means commerce). Anything else left bare ("diploma", "masters",
    # "phd" alone) has no inferable subject and must not be guessed.
    _BARE_ABBREV_SUBJECT = {
        'bsc': 'science', 'b.sc': 'science', 'b sc': 'science',
        'ba': 'arts', 'b.a': 'arts', 'b a': 'arts',
        'bed': 'education',
        'bcom': 'commerce',
        'msc': 'science', 'm.sc': 'science', 'm sc': 'science',
    }

    def _detect_level(self, s):
        s = s.strip()
        for pat, level in self._QUALIFIER_DEFS:
            if pat.match(s):
                return level
        return None

    def __init__(self):
        self.programs = []
        self.program_variations = {}
        self.program_levels = {}
        self.course_codes = set()
        self.course_codes_normalized = {}
        self.course_names = set()
        self.lecturers = set()
        self.program_keywords = set()
        self._load_data()

    def _load_data(self):
        if PROGRAM_MODELS_AVAILABLE:
            try:
                for prog in Program.objects.all().values('id', 'name'):
                    pn = prog['name']
                    self.programs.append({'id': prog['id'], 'name': pn, 'name_lower': pn.lower()})
                    self._add_program_variations(pn)
            except Exception as e:
                logger.error(f"Error loading programs: {e}")
        if CHATBOT_MODELS_AVAILABLE:
            try:
                pub_ids = list(ChatbotPublication.objects.filter(is_latest=True).values_list('id', flat=True))
                if pub_ids:
                    for c in ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids).values('course_code', 'course_name', 'lecturer_name'):
                        if c['course_code']:
                            code = c['course_code'].upper().strip()
                            self.course_codes.add(code)
                            self.course_codes_normalized[self._norm(code)] = code
                        if c['course_name']: self.course_names.add(c['course_name'].lower())
                        if c['lecturer_name']: self.lecturers.add(c['lecturer_name'].lower())
            except Exception as e:
                logger.error(f"Error loading timetable data: {e}")
        if PROGRAM_MODELS_AVAILABLE:
            try:
                for a in CourseAllocation.objects.filter(submitted_to_tt=True).values('course_code', 'course_name'):
                    if a['course_code']:
                        code = a['course_code'].upper().strip()
                        self.course_codes.add(code)
                        self.course_codes_normalized[self._norm(code)] = code
                    if a['course_name']: self.course_names.add(a['course_name'].lower())
            except Exception as e:
                logger.error(f"Error loading course allocations: {e}")

    def _norm(self, code):
        n = code.replace(' ', '').upper()
        n = re.sub(r'\([A-Z]\)$', '', n)
        n = re.sub(r'(\d+)[A-Z]$', r'\1', n)
        return n

    def _add_program_variations(self, program_name):
        variations = set()
        nl = program_name.lower(); variations.add(nl)
        for prefix in ['bachelor of','bachelors of','bsc','b.sc','ba','b.a','diploma in',
                        'certificate in','masters in','master of','msc','m.sc','phd in','doctor of']:
            if nl.startswith(prefix):
                without = nl[len(prefix):].strip(); variations.add(without)
                compact = f"{prefix.replace(' ','').replace('.','')}{without.replace(' ','')}"
                variations.add(compact)
        variations.add(nl.replace(' ', ''))
        self.program_levels[program_name] = self._detect_level(nl)
        for var in variations:
            # Accumulate candidates instead of overwriting: two distinct real
            # programs can legitimately reduce to the same bare fragment
            # (e.g. "BSc Computer Science" and "Diploma in Computer Science"
            # both strip down to "computer science"). Silently dropping one
            # of them is the bug -- both must stay reachable, and the level
            # qualifier in the query is what tells them apart at lookup time.
            bucket = self.program_variations.setdefault(var, [])
            if program_name not in bucket:
                bucket.append(program_name)
            if len(var) > 3: self.program_keywords.add(var)

    def match_course_code(self, text):
        if not text: return None
        tu = text.upper().strip()
        base = re.sub(r'\s*\([A-Z0-9]\)$', '', tu).strip()
        m = re.match(r'^([A-Z]{2,5})\s*(\d+)[A-Z]?$', base.replace(' ', ''))
        if m: base = f"{m.group(1)} {m.group(2)}"
        if base in self.course_codes: return base
        if tu in self.course_codes: return tu
        norm = self._norm(tu)
        if norm in self.course_codes_normalized:
            matched = self.course_codes_normalized[norm]
            bn = self._norm(base)
            if bn in self.course_codes_normalized:
                mb = self.course_codes_normalized[bn]
                if not re.search(r'\([A-Z]\)$|[A-Z]$', mb.replace(' ','')): return mb
            return matched
        base_m = None; suffix_m = None
        for code in self.course_codes:
            if base == code: return code
            if base in code or code in tu:
                if not re.search(r'\([A-Z]\)$', code): base_m = code
                else: suffix_m = suffix_m or code
        if base_m: return base_m
        if suffix_m: return suffix_m
        # Refuse to silently guess across course numbers (e.g. 'ASC 345' must
        # never resolve to 'SOCI 345' just because the digits match) — only
        # fuzz the department-letter prefix, require the number exact.
        return _safe_fuzzy_code_match(base if _CODE_SPLIT_RE.match(base) else tu, list(self.course_codes))

    def extract_course_codes(self, message):
        mu = message.upper(); courses = []; seen = set()
        for pat in [r'\b([A-Z]{2,5})\s+(\d{4,5})\b', r'\b([A-Z]{2,5})\s+(\d{3,4})\b',
                    r'\b([A-Z]{2,5})\s*(\d{3,5})\b', r'\b([A-Z]{2,5})(\d{3,5})\b',
                    r'\b([A-Z]{2,5})\s+(\d{3,4})[A-Z]?\b', r'\b([A-Z]{2,5})(\d{3,4})[A-Z]?\b']:
            for dept, num in re.findall(pat, mu):
                c = f"{dept} {num}"
                if c not in seen: seen.add(c); courses.append(c)
        return courses

    def _resolve_program_candidates(self, candidates, qualifier):
        """candidates: list of program names sharing a matched fragment.
        Disambiguates using the level the user actually typed (bsc/ba/diploma/
        masters/phd/etc). If no qualifier was given, or it doesn't narrow
        things down, falls back to a fixed, documented preference order
        (bachelor > diploma > certificate > masters > phd) rather than an
        accidental, insertion-order-dependent pick."""
        if len(candidates) == 1:
            return candidates[0]
        if qualifier:
            leveled = [c for c in candidates if self.program_levels.get(c) == qualifier]
            if leveled:
                candidates = leveled
        if len(candidates) == 1:
            return candidates[0]
        for lvl in ('bachelor', 'diploma', 'certificate', 'masters', 'phd'):
            for c in candidates:
                if self.program_levels.get(c) == lvl:
                    return c
        return candidates[0]

    def match_program(self, text):
        if not text: return None, None
        tl = text.lower().strip()
        ym = re.search(r'year\s*(\d+)|yr\s*(\d+)', tl); year = None
        if ym: year = ym.group(1) or ym.group(2)
        clean = re.sub(r'year\s*\d+|yr\s*\d+', '', tl).strip()
        clean = re.sub(r'\s+', ' ', clean).strip()
        # Capture the level the user typed (bsc/ba/diploma/masters/phd/...)
        # BEFORE stripping it, so it can be used to disambiguate fragments
        # shared by multiple programs (e.g. "computer science" is reachable
        # from both "BSc Computer Science" and "Diploma in Computer
        # Science" -- the qualifier is the only thing that tells them apart).
        qualifier = self._detect_level(clean)
        stripped = self._QUALIFIER_STRIP_RE.sub('', clean).strip()
        stripped = re.sub(r'\s+', ' ', stripped).strip()
        if not stripped:
            # Nothing left after removing the level phrase -- the query was
            # just "bcom", "bed", "diploma", etc. A few abbreviations
            # conventionally imply their own subject; anything else has no
            # inferable subject, so don't fall through to blind containment
            # matching (an empty string is a "substring" of everything and
            # would otherwise match whichever variation happens to be
            # longest, which is meaningless).
            stripped = self._BARE_ABBREV_SUBJECT.get(clean, '')
        clean = stripped
        if not clean:
            return None, year
        if clean in self.program_variations:
            return self._resolve_program_candidates(self.program_variations[clean], qualifier), year
        # Longest-match wins: dict/list iteration order must never decide the result.
        # A short, generic variation (e.g. "science" from "Master of Science") must
        # not out-rank a longer, more specific one (e.g. "computer science" from
        # "BSc Computer Science") just because it happens to be seen first.
        best_var, best_len = None, -1
        for var in self.program_variations:
            if var in clean or clean in var:
                if len(var) > best_len:
                    best_len = len(var); best_var = var
        if best_var is not None:
            return self._resolve_program_candidates(self.program_variations[best_var], qualifier), year
        close = get_close_matches(clean, list(self.program_variations.keys()), n=1, cutoff=0.7)
        if close:
            return self._resolve_program_candidates(self.program_variations[close[0]], qualifier), year
        best_prog, best_len = None, -1
        for prog in self.programs:
            pl = prog['name_lower']
            if pl in clean or clean in pl:
                if len(pl) > best_len:
                    best_len = len(pl); best_prog = prog
        if best_prog is not None:
            return best_prog['name'], year
        return None, year

    def match_lecturer(self, text):
        if not text: return None
        tl = text.lower().strip()
        tc = re.sub(r'^(dr|prof|mr|ms|mrs)\.?\s*', '', tl).strip()
        if tc in self.lecturers: return tc
        best_lec, best_len = None, -1
        for lec in self.lecturers:
            if tc in lec or lec in tc:
                if len(lec) > best_len:
                    best_len = len(lec); best_lec = lec
        if best_lec is not None: return best_lec
        qw = set(tc.split())
        for lec in self.lecturers:
            overlap = {w for w in qw & set(lec.split()) if len(w) > 2}
            if overlap: return lec
        close = get_close_matches(tc, list(self.lecturers), n=1, cutoff=0.65)
        return close[0] if close else None

    def extract_and_match(self, message):
        result = {'program': None, 'program_year': None, 'course_codes': [],
                  'lecturer': None, 'query_type': None, 'unmatched_codes': []}
        ml = message.lower()
        codes = self.extract_course_codes(message)
        if codes:
            valid = []; unmatched = []
            for c in codes:
                matched = self.match_course_code(c) or self.match_course_code(re.sub(r'\s+', '', c))
                if matched: valid.append(matched)
                else: unmatched.append(c)
            if valid:
                result['course_codes'] = valid
                result['query_type'] = 'courses' if len(valid) > 1 else 'course'
                result['unmatched_codes'] = unmatched
                return result
            if unmatched:
                result['course_codes'] = unmatched
                result['query_type'] = 'courses' if len(unmatched) > 1 else 'course'
                result['unmatched_codes'] = unmatched
                return result
        pm = re.search(r'(?:courses? in|timetable for|program\s*)?\s*([a-z\s]+?)(?:\s+year\s*\d+|\s+yr\s*\d+)?$', ml)
        if pm:
            potential = pm.group(1).strip()
            if len(potential) > 3:
                program, year = self.match_program(potential)
                if program:
                    result['program'] = program
                    ym2 = re.search(r'year\s*(\d+)|yr\s*(\d+)', ml)
                    if ym2: result['program_year'] = ym2.group(1) or ym2.group(2)
                    result['query_type'] = 'program'
                    return result
        lm = re.search(r'(?:lecturer|taught by|instructor|courses? for|teaches?|who is)\s+([a-z\s\.]+)', ml)
        if not lm: lm = re.search(r'^(dr|prof|mr|ms|mrs)\.?\s+(.+)$', ml)
        if lm:
            potential = (lm.group(2) if lm.lastindex and lm.lastindex >= 2 else lm.group(1)).strip()
            lec = self.match_lecturer(potential)
            if lec: result['lecturer'] = lec; result['query_type'] = 'lecturer'; return result
        bare = re.sub(r'^(dr|prof|mr|ms|mrs)\.?\s*', '', ml).strip()
        if re.match(r'^[a-z][a-z\s\.]{1,50}$', bare) and len(bare) >= 3:
            lec = self.match_lecturer(bare)
            if lec: result['lecturer'] = lec; result['query_type'] = 'lecturer'; return result
        return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1-9 — Context, Crisis, Requester, Wikipedia, DDG, Calculator, News,
#               Fun Facts, Chuka KB  (identical to v16 — no changes needed)
# ══════════════════════════════════════════════════════════════════════════════

class ConversationContext:
    def __init__(self): self.user_contexts = {}
    def get_context(self, sid):
        if sid not in self.user_contexts:
            self.user_contexts[sid] = {
                'last_topic': None, 'last_entity': None, 'last_question': None,
                'last_answer': None, 'last_query_type': None, 'conversation_history': []
            }
        return self.user_contexts[sid]
    def update_context(self, sid, topic=None, entity=None, question=None, answer=None, query_type=None):
        ctx = self.get_context(sid)
        if topic is not None: ctx['last_topic'] = topic
        if entity is not None: ctx['last_entity'] = entity
        if question: ctx['last_question'] = question
        if answer: ctx['last_answer'] = answer
        if query_type: ctx['last_query_type'] = query_type
        ctx['conversation_history'].append({'question': question[:100] if question else None,
            'answer': answer[:200] if answer else None, 'topic': topic,
            'query_type': query_type, 'timestamp': datetime.now().isoformat()})
        if len(ctx['conversation_history']) > 15:
            ctx['conversation_history'] = ctx['conversation_history'][-15:]
    def is_followup(self, message, ctx):
        ml = message.lower()
        if not ctx.get('last_topic'): return False
        pronouns = ['it','its','this','that','they','them','he','him','she','her','his']
        starters = ['and','what about','how about','also','then','so','okay','ok','and the']
        words = ml.split()
        return any(p in words[:3] for p in pronouns) or any(ml.startswith(s) for s in starters) or len(words) <= 3

context_manager = ConversationContext()

CRISIS_KEYWORDS = [
    'kill myself','suicide','self harm','end my life','want to die',
    'how to die','commit suicide','depressed','hopeless','give up',
    'hurt myself','take my life'
]
def is_crisis_query(msg): return any(k in msg.lower() for k in CRISIS_KEYWORDS)
def get_crisis_response():
    return """⚠️ I'm really concerned about what you're saying. ⚠️

Please reach out to someone who can help:

Kenya Crisis Support:
• Befrienders Kenya: 0722 178 177 / 020 205 4980 (24/7)
• Kenya Red Cross: 1199 (toll-free)
• Chuka University Counselling Department: Visit the Student Welfare Office

You matter, and there are people who care about you. Please reach out to them now.

If you'd like to talk to someone at the university, use the feedback form below and we'll connect you with a counsellor."""

class SmartRequester:
    def __init__(self):
        self.session = requests.Session()
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36',
        ]
        adapter = HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[429,500,502,503,504]))
        self.session.mount("http://", adapter); self.session.mount("https://", adapter)
        self.timeout = 10
    def get(self, url):
        try:
            resp = self.session.get(url, headers={'User-Agent': random.choice(self.user_agents)}, timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status(); return resp
        except Exception: return None

class WikipediaSearch:
    def __init__(self): self.requester = SmartRequester(); self.cache = {}
    def search(self, query):
        ck = hashlib.md5(query.lower().encode()).hexdigest()
        if ck in self.cache: return self.cache[ck]
        try:
            api = "https://en.wikipedia.org/w/api.php"
            resp = self.requester.get(api + f"?action=query&list=search&srsearch={quote(query)}&format=json&srlimit=1")
            if resp:
                data = resp.json()
                if data.get('query', {}).get('search'):
                    title = data['query']['search'][0]['title']
                    er = self.requester.get(api + f"?action=query&prop=extracts&exintro=true&explaintext=true&titles={quote(title)}&format=json")
                    if er:
                        for page in er.json().get('query', {}).get('pages', {}).values():
                            if 'extract' in page:
                                content = re.sub(r'\s+', ' ', page['extract'][:800])
                                result = {'title': page.get('title', title), 'content': content}
                                self.cache[ck] = result; return result
        except Exception as e: logger.error(f"Wikipedia error: {e}")
        return None

class DuckDuckGoSearch:
    def __init__(self): self.requester = SmartRequester(); self.cache = {}
    def search(self, query):
        ck = hashlib.md5(f"ddg_{query.lower()}".encode()).hexdigest()
        if ck in self.cache: return self.cache[ck]
        try:
            resp = self.requester.get(f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1")
            if resp:
                data = resp.json()
                text = data.get('Abstract','').strip() or data.get('Answer','').strip() or data.get('Definition','').strip()
                if text and len(text) > 40:
                    result = text[:600]; self.cache[ck] = result; return result
        except Exception: pass
        return None

_CALC_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_CALC_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

class UnsafeExpressionError(ValueError): pass

def _safe_eval_node(node):
    if isinstance(node, ast.Expression): return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool): return node.value
        raise UnsafeExpressionError("Only numeric literals are allowed")
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op not in _CALC_BINOPS: raise UnsafeExpressionError(f"Operator {op.__name__} not allowed")
        l, r = _safe_eval_node(node.left), _safe_eval_node(node.right)
        if op is ast.Pow and (abs(r) > 1000 or abs(l) > 10**6): raise UnsafeExpressionError("Exponent too large")
        return _CALC_BINOPS[op](l, r)
    if isinstance(node, ast.UnaryOp):
        op = type(node.op)
        if op not in _CALC_UNARYOPS: raise UnsafeExpressionError(f"Operator {op.__name__} not allowed")
        return _CALC_UNARYOPS[op](_safe_eval_node(node.operand))
    raise UnsafeExpressionError(f"Unsupported: {type(node).__name__}")

def safe_arithmetic_eval(expr):
    return _safe_eval_node(ast.parse(expr, mode="eval"))

class Calculator:
    def calculate(self, expression):
        expr = re.sub(r'(?:what is|calculate|solve|compute|math)\s*', '', expression, flags=re.IGNORECASE).strip()
        if re.match(r'^[\d\s\+\-\*\/\(\)\.\%\^]+$', expr):
            try:
                result = safe_arithmetic_eval(expr.replace('^', '**'))
                return {'expression': expr, 'result': result}
            except Exception: pass
        return None

class NewsSearch:
    def __init__(self): self.requester = SmartRequester()
    def search(self, topic):
        try:
            resp = self.requester.get(f"https://news.google.com/rss/search?q={quote(topic)}&hl=en-KE&gl=KE&ceid=KE:en")
            if resp:
                soup = BeautifulSoup(resp.text, 'xml'); items = soup.find_all('item')[:4]
                if items:
                    news = []
                    for item in items:
                        t = item.find('title'); d = item.find('description')
                        if t:
                            ct = re.sub(r'\s*-\s*[^-]+$', '', t.text).strip()
                            news.append({'title': ct, 'snippet': BeautifulSoup(d.text, 'html.parser').get_text()[:200] if d else ''})
                    return {'items': news}
        except Exception as e: logger.debug(f"News error: {e}")
        return None

FUN_FACTS = [
    "🐙 Octopuses have three hearts and blue blood!",
    "🍯 Honey never spoils — archaeologists found 3000-year-old honey in Egyptian tombs!",
    "🌍 A day on Venus is longer than its year!",
    "👃 Your nose can remember 50,000 different scents!",
    "🍌 Bananas are berries, but strawberries aren't!",
    "🗼 The Eiffel Tower grows about 6 inches taller in summer!",
    "🦩 A group of flamingos is called a 'flamboyance'!",
    "🍊 The first oranges weren't orange — they were green!",
    "🦛 Hippos produce their own natural sunscreen!",
    "🐜 The total weight of all ants on Earth equals the weight of all humans!",
]
def get_fun_fact(): return random.choice(FUN_FACTS)

CHUKA_KNOWLEDGE = {
    'vc': {'keywords': ['vice chancellor','who is the vc','chuka vc','who leads chuka'],
           'answer': "Vice Chancellor of Chuka University\n\n" + "="*50 + "\n\nProfessor Henry Mutembei was appointed in June 2023 after serving as Deputy Vice Chancellor for Administration, Finance, Planning and Development.\n\nPrevious VC: Professor Erastus Njoka served from 2004 to 2022."},
    'dvc': {'keywords': ['dvc','deputy vice chancellor','deputy vc','who is the dvc'],
            'answer': "Deputy Vice Chancellors\n\n" + "="*50 + "\n\nChuka University has Deputy Vice Chancellors responsible for different areas. For specific DVC information, please contact the Vice Chancellor's office or visit the university website at www.chuka.ac.ke/leadership."},
    'history': {'keywords': ['when was it started','when started','established','founded','history'],
                'answer': "Chuka University History\n\n" + "="*50 + "\n\n• 2004: Established as a campus of Egerton University\n• 2007: Became Chuka University College\n• January 8, 2013: Granted full university charter status"},
    'location': {'keywords': ['where is chuka','chuka location','address','where is the university','allocated'],
                 'answer': "Chuka University Location\n\n" + "="*50 + "\n\n📍 Address: P.O. Box 109-60400, Chuka, Kenya\n\n📍 Physical Location: Chuka Town, Tharaka-Nithi County, Kenya\n\n📍 Directions: Along the Chuka-Embu Road, about 170 km northeast of Nairobi"},
    'faculties': {'keywords': ['faculties','schools','departments','list the faculties','what schools'],
                  'answer': "Chuka University Schools/Faculties\n\n" + "="*50 + "\n\n• School of Agriculture and Environmental Sciences\n• School of Business and Economics\n• School of Education and Social Sciences\n• School of Engineering and Technology\n• School of Health Sciences\n• School of Humanities and Social Sciences\n• School of Law\n• School of Nursing and Public Health\n• School of Pure and Applied Sciences\n\nFor detailed programme information, visit www.chuka.ac.ke/academics."},
    'admission': {'keywords': ['admission','how to apply','join chuka'],
                  'answer': "Admission to Chuka University\n\n" + "="*50 + "\n\nGovernment-Sponsored Students: Apply through KUCCPS\n\nSelf-Sponsored Students: Apply directly at www.chuka.ac.ke\n\nMinimum Requirement: KCSE mean grade C+ for degree programmes\n\nContact: admissions@chuka.ac.ke"},
    'contacts': {'keywords': ['contact','phone','email','reach'],
                 'answer': "Chuka University Contacts\n\n" + "="*50 + "\n\n📞 Phone: +254 (0)71 628 0000\n\n📧 Email: info@chuka.ac.ke | admissions@chuka.ac.ke\n\n🌐 Website: www.chuka.ac.ke\n\n🔑 Student Portal: portal.chuka.ac.ke"},
    'timetabling_office': {
        'keywords': ['timetabling office', 'communicate with timetabling', 'talk to timetabling',
                     'speak to timetabling', 'contact the timetabling office', 'reach the timetabling office',
                     'talk to a human', 'speak to a human', 'speak to someone', 'talk to someone',
                     'real person', 'human being'],
        'answer': "Reach the Timetabling Office\n\n" + "="*50 +
                  "\n\n📧 Directorate of Examinations & Timetabling: extt@chuka.ac.ke"
                  "\n\n🛠️ IT / Timetable Support: ttsupport@chuka.ac.ke"
                  "\n\n📍 Visit: Science Complex (S102)"
                  "\n\nYou can also use the feedback form right here in the chat — it goes "
                  "directly to the timetabling office. Just tell me what the issue is (a course "
                  "code, a wrong venue, a clash, a missing class, etc.) and I can log it for you.",
    },
}

# Words too short/common to be a meaningful fuzzy-match anchor on their own —
# excluding them avoids matching 'with' against an unrelated typo like 'iwth'
# while still fuzzing genuinely distinctive keyword words like 'communicate'.
_KB_FUZZY_MIN_LEN = 5


def _kb_fuzzy_hit(ql_words, keyword_phrase, cutoff=0.8):
    significant = [w for w in keyword_phrase.split() if len(w) >= _KB_FUZZY_MIN_LEN]
    if not significant:
        return False
    return all(get_close_matches(w, ql_words, n=1, cutoff=cutoff) for w in significant)


def get_chuka_answer(query, context_topic=None):
    ql = query.lower()
    if context_topic and context_topic in CHUKA_KNOWLEDGE: return CHUKA_KNOWLEDGE[context_topic]['answer'], context_topic
    # 1. Exact substring match (fast path, unchanged behaviour)
    for key, data in CHUKA_KNOWLEDGE.items():
        if any(kw in ql for kw in data['keywords']): return data['answer'], key
    # 2. Typo-tolerant fallback — catches things like "comunicate iwth
    #    timetaqbling" that a literal substring check would never find.
    ql_words = ql.split()
    if ql_words:
        for key, data in CHUKA_KNOWLEDGE.items():
            if any(_kb_fuzzy_hit(ql_words, kw) for kw in data['keywords']):
                return data['answer'], key
    return None, None


# Precompiled once at import time (not per-request) — both for correctness
# (word-boundary safety) and for speed, since these are checked on every
# single message.
_GREETING_RE = _kw_regex([
    'hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening', 'howdy',
    'how are you', 'how are doing', 'how are you doing', 'how is it going', "what's up", 'whats up',
])
_NEWS_RE = _kw_regex(['news'])
_FACT_RE = _kw_regex(['fact', 'did you know'])
_TIME_RE = _kw_regex(['time', 'date', 'what time'])
_ABOUT_BOT_RE = _kw_regex(['who are you', 'what are you', 'are you a', 'about you', 'tell me about yourself'])
_THANKS_RE = _kw_regex(['thank', 'thanks'])
_FAREWELL_RE = _kw_regex(['bye', 'goodbye', 'see you'])
_FORM_RE = _kw_regex(['form', 'feedback', 'report'])
_HELP_RE = _kw_regex(['help'])
# Catches insults/profanity directed at the bot so they never get forwarded
# as a literal web-search query (that's how "you are dumb fuck" ended up
# returning a Wikipedia bio for a band called "Fuck Buttons" — the search
# engine matched on the swear word itself).
_PROFANITY_RE = _kw_regex([
    'fuck', 'shit', 'bitch', 'asshole', 'bastard', 'dumbass', 'motherfucker',
    'you are dumb', "you're dumb", 'you are stupid', "you're stupid", 'useless bot', 'stupid bot',
])


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — TimetableHandler (now with ProgramCourse parser integration)
# ══════════════════════════════════════════════════════════════════════════════

class TimetableHandler:
    def __init__(self):
        self.matcher = SmartLocalMatcher()

    # ── formatting ────────────────────────────────────────────────────────────

    def format_timetable_entry(self, entry, include_program=True):
        time_str = entry.start_time.strftime('%I:%M %p').lstrip('0') if entry.start_time else ''
        date_str = entry.date.strftime('%A, %d %b %Y') if entry.date else (entry.day or '')
        prog = ''
        if include_program and entry.program_name:
            prog = f"🎓 Program: {entry.program_name}"
            if entry.year_of_study and entry.year_of_study not in ('-', ''):
                prog += f" (Year {entry.year_of_study})"
        result = (f"\n┌{'─'*60}┐\n│ 📚 {entry.course_code} - {entry.course_name or 'No course name'}\n"
                  f"├{'─'*60}┤\n│ 📅 When: {date_str} at {time_str}\n│ 🏛️  Venue: {entry.venue_code or 'TBA'}")
        if prog: result += f"\n│ {prog}"
        if entry.lecturer_name: result += f"\n│ 👨‍🏫 Lecturer: {entry.lecturer_name}"
        result += f"\n└{'─'*60}┘"
        return result

    # ── low-level lookups ─────────────────────────────────────────────────────

    def lookup_course(self, course_code, ttype):
        if not CHATBOT_MODELS_AVAILABLE: return {"found": False, "reason": "models_unavailable"}
        try:
            pub_qs = ChatbotPublication.objects.filter(is_latest=True)
            if ttype and ttype != "ALL": pub_qs = pub_qs.filter(timetable_type=ttype)
            pub_ids = list(pub_qs.values_list("id", flat=True))
            if not pub_ids: return {"found": False, "reason": "no_publication"}

            # Normalise and build search terms
            sl = re.sub(r'\s*\([A-Z0-9]\)$', '', course_code).strip()
            sl = re.sub(r'(\d+)[A-Z]$', r'\1', sl)
            m = re.match(r'^([A-Z]{2,5})\s*(\d+)$', sl.replace(' ', ''))
            if m: sl = f"{m.group(1)} {m.group(2)}"
            terms = list(dict.fromkeys([course_code, sl, course_code.replace(' ', ''), sl.replace(' ', '')]))

            entries = ChatbotTimetableEntry.objects.none()
            for term in terms:
                entries = ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids, course_code__iexact=term)
                if entries.exists(): break
                entries = ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids, course_code__icontains=term)
                if entries.exists(): break

            if not entries.exists(): return {"found": False, "reason": "not_found"}
            results = [self.format_timetable_entry(e) for e in entries[:10]]
            return {"found": True, "results": results, "count": len(results)}
        except Exception as e:
            logger.error(f"Error looking up course {course_code}: {e}")
            return {"found": False, "reason": "error", "error": str(e)}

    def lookup_by_program(self, program_name, year=None):
        if not CHATBOT_MODELS_AVAILABLE: return {"found": False, "reason": "models_unavailable"}
        try:
            pub_ids = list(ChatbotPublication.objects.filter(is_latest=True).values_list("id", flat=True))
            if not pub_ids: return {"found": False, "reason": "no_publication"}
            entries = ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids, program_name__icontains=program_name)
            if year: entries = entries.filter(year_of_study=year)
            if not entries.exists():
                if PROGRAM_MODELS_AVAILABLE:
                    try:
                        prog = next((p for p in self.matcher.programs if p['name'] == program_name or p['name_lower'] == program_name.lower()), None)
                        if prog:
                            courses = ProgramCourse.objects.filter(program_id=prog['id'])
                            if year: courses = courses.filter(year=year)
                            if courses.exists():
                                results = []
                                for c in courses[:15]:
                                    r = (f"\n┌{'─'*60}┐\n│ 📚 {c.course_code} - {c.course_name or 'No course name'}\n"
                                         f"├{'─'*60}┤\n│ 📅 When: Not scheduled in current timetable\n│ 🏛️  Venue: Check with department\n│ 🎓 Program: {program_name} (Year {c.year})")
                                    if c.semester: r += f"\n│ 📖 Semester: {c.semester}"
                                    r += f"\n└{'─'*60}┘"
                                    results.append(r)
                                return {"found": True, "results": results, "count": courses.count()}
                    except Exception as e:
                        logger.error(f"Error getting program courses: {e}")
                return {"found": False, "reason": "not_found"}
            unique = {}
            for e in entries:
                if e.course_code not in unique: unique[e.course_code] = e
            results = [self.format_timetable_entry(e, include_program=False) for e in list(unique.values())[:15]]
            return {"found": True, "results": results, "count": len(unique)}
        except Exception as e:
            logger.error(f"Error looking up program {program_name}: {e}")
            return {"found": False, "reason": "error", "error": str(e)}

    def lookup_by_lecturer(self, lecturer_name):
        if not CHATBOT_MODELS_AVAILABLE: return {"found": False, "reason": "models_unavailable"}
        try:
            pub_ids = list(ChatbotPublication.objects.filter(is_latest=True).values_list("id", flat=True))
            if not pub_ids: return {"found": False, "reason": "no_publication"}
            entries = ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids, lecturer_name__icontains=lecturer_name)
            if not entries.exists(): return {"found": False, "reason": "not_found"}
            results = [self.format_timetable_entry(e) for e in entries[:15]]
            return {"found": True, "results": results, "count": len(entries)}
        except Exception as e:
            logger.error(f"Error looking up lecturer {lecturer_name}: {e}")
            return {"found": False, "reason": "error", "error": str(e)}

    def lookup_free_venues(self, day=None, slot_time=None):
        if not CHATBOT_MODELS_AVAILABLE: return {"found": False, "reason": "models_unavailable"}
        try:
            pub_ids = list(ChatbotPublication.objects.filter(is_latest=True).values_list("id", flat=True))
            if not pub_ids: return {"found": False, "reason": "no_publication"}
            entries = ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids)
            if day: entries = entries.filter(day__icontains=day)
            if slot_time:
                tm = slot_time.hour * 60 + slot_time.minute
                entries = [e for e in list(entries) if e.start_time and abs(e.start_time.hour * 60 + e.start_time.minute - tm) <= 30]
            occupied = {_norm_venue(e.venue_code) for e in entries if e.venue_code}

            # The universe of "all venues" MUST come from the real room
            # inventory (room_management.Venue), not from the timetable
            # snapshot. Deriving it from ChatbotTimetableEntry only lists
            # venues that happen to already be scheduled somewhere — a
            # perfectly free, unscheduled room would never appear at all,
            # and if most known venues are used at some point in the day,
            # the venue set shrinks to near-nothing ("all venues occupied")
            # even though plenty of real rooms were never touched.
            venue_display = {}
            if ROOM_MODELS_AVAILABLE:
                for code in Venue.objects.values_list('code', flat=True):
                    if code: venue_display[_norm_venue(code)] = code
            if not venue_display:
                # Fallback only — used if room_management isn't available/
                # populated, so this doesn't regress environments without it.
                for code in ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids).values_list('venue_code', flat=True):
                    if code: venue_display[_norm_venue(code)] = code

            free_keys = set(venue_display) - occupied
            if not free_keys: return {"found": False, "reason": "no_free_venues"}
            free_codes = sorted(venue_display[k] for k in free_keys)
            results = [f"🏛️ {v} - Available" for v in free_codes[:20]]
            return {"found": True, "results": results, "count": len(free_keys)}
        except Exception as e:
            logger.error(f"Error finding free venues: {e}")
            return {"found": False, "reason": "error", "error": str(e)}

    def lookup_all_timetables(self, course_code):
        reg = self.lookup_course(course_code, "REGULAR")
        exam = self.lookup_course(course_code, "EXAM")
        results = []
        has_reg = reg.get('found', False); has_exam = exam.get('found', False)
        if has_reg: results.extend(reg.get('results', []))
        if has_exam: results.extend(exam.get('results', []))
        if results: return {"found": True, "results": results, "count": len(results), "has_regular": has_reg, "has_exam": has_exam}
        return {"found": False, "reason": "not_found"}

    # ── core query handler (with ProgramCourse parser + AI) ───────────────────

    def handle_query(self, message, has_data):
        # ── Step A: ProgramCourse parser ──────────────────────────────────────
        parse_result = _program_course_parser.parse(message)

        if parse_result['matched']:
            canonical = parse_result['course_code']
            confidence = parse_result.get('confidence', 1.0)
            raw_in = parse_result.get('raw_input', canonical)
            # Confidence was being tracked but never surfaced — a fuzzy guess
            # (e.g. 'COSC 301' typed, 'ACSC 301' returned) was silently
            # substituted with no indication anything was corrected at all.
            note = ""
            if confidence < 1.0 and _norm_code(raw_in) != _norm_code(canonical):
                note = (f"ℹ️ I couldn't find an exact match for '{raw_in.strip()}', "
                        f"so here's '{canonical}' — let me know if that's not what you meant.\n\n")
            db_result = self.lookup_all_timetables(canonical)

            if db_result.get('found'):
                # Already clean, well-formatted text — an AI "polish" round
                # trip here only adds latency (and hallucination risk on
                # data we already trust) for no visible benefit.
                return note + self._format_course_result(canonical, db_result), False

            # Course is in ProgramCourse but not yet in timetable snapshot
            pc_info = (
                f"Course: {parse_result['course_code']} — {parse_result['course_name']}\n"
                f"Program: {parse_result['program_name']} (Year {parse_result['year']}, Semester {parse_result['semester']})\n"
                f"Status: Not yet scheduled in the published timetable."
            )
            return note + pc_info, False

        # ── Step B: SmartLocalMatcher (program / lecturer / multi-course) ─────
        matched = self.matcher.extract_and_match(message)

        if matched['query_type'] == 'program' and matched['program']:
            return self.handle_program_query(matched['program'], matched['program_year'])
        if matched['query_type'] == 'lecturer' and matched['lecturer']:
            return self.handle_lecturer_query(matched['lecturer'])
        if matched['query_type'] == 'course' and matched['course_codes']:
            course = matched['course_codes'][0]
            if course in matched.get('unmatched_codes', []):
                # Parser also failed — ask AI to guess the code
                return self._ai_fallback_course(course, message)
            return self.handle_single_course_query(course, message)
        if matched['query_type'] == 'courses' and len(matched['course_codes']) >= 2:
            return self.handle_multiple_courses_query(matched['course_codes'], message)

        return None, False

    def _format_course_result(self, course_code, result):
        header = f"📅 Timetable for {course_code}\n{'='*60}\n"
        if result.get('has_regular') and result.get('has_exam'):
            header += "📘 Regular Classes + 📗 Exams\n\n"
        elif result.get('has_regular'):
            header += "📘 Regular Classes\n\n"
        elif result.get('has_exam'):
            header += "📗 Exams\n\n"
        header += f"Found {result['count']} schedule{'s' if result['count'] > 1 else ''}:\n\n"
        return header + "\n".join(result['results'])

    def _ai_fallback_course(self, raw_code, message):
        """
        Course code was not found in ProgramCourse or ChatbotTimetableEntry.
        This is the one place an AI round-trip earns its latency cost: we
        genuinely don't know what the student meant, and a language model
        can make a much better guess than a plain fuzzy string match.
        """
        guessed = _ai_guess_course_code(message)
        if guessed:
            db_result = self.lookup_all_timetables(guessed)
            if db_result.get('found'):
                raw_text = self._format_course_result(guessed, db_result)
                note = f"(I interpreted '{raw_code}' as '{guessed}')\n\n"
                return note + raw_text, False
        # AI couldn't help either — return clean not-found
        return self.get_not_found_response(raw_code)

    # ── query handlers (unchanged logic; DB text returned as-is — no AI
    #    round trip on the hot path, see note above) ───────────────────────

    def handle_single_course_query(self, course, message):
        is_exam = any(w in message.lower() for w in ['exam','cat','test','assessment'])
        result = self.lookup_course(course, "EXAM") if is_exam else self.lookup_all_timetables(course)
        if result.get('found'):
            raw = self._format_course_result(course, result)
            raw += f"\n{'='*60}\n💡 Tip: Try \"{course} exam\" for exams only, or \"courses in [program]\" for the full program."
            return raw, False
        return self._ai_fallback_course(course, message)

    def handle_multiple_courses_query(self, courses, message):
        is_exam = any(w in message.lower() for w in ['exam','cat','test','assessment'])
        parts = []; found_count = 0
        for course in courses:
            result = self.lookup_course(course, "EXAM") if is_exam else self.lookup_all_timetables(course)
            if result.get('found'):
                found_count += 1
                parts.append(f"\n📌 {course}\n" + "-"*40 + "\n" + "\n".join(result['results']))
        if parts:
            header = f"📅 {'Exam' if is_exam else 'Timetable'} for {len(courses)} courses\n{'='*60}\nFound {found_count}/{len(courses)} courses:\n"
            raw = header + "\n".join(parts)
            return raw, False
        return f"❌ I could not find schedules for any of: {', '.join(courses)}", True

    def handle_program_query(self, program_name, year=None):
        result = self.lookup_by_program(program_name, year)
        if result.get('found'):
            year_text = f" (Year {year})" if year else ""
            header = f"📚 Courses in {program_name}{year_text}\n{'='*60}\nFound {result['count']} course{'s' if result['count'] > 1 else ''}:\n\n"
            raw = header + "\n".join(result['results'])
            if result.get('count', 0) > 15: raw += f"\n\n(Showing first 15 of {result['count']} courses)"
            raw += f"\n{'='*60}\n💡 Tip: To see a specific course, try \"when is [course code]\""
            return raw, False
        return f"❌ I could not find any courses for '{program_name}'. Please check the program name and try again.", True

    def handle_lecturer_query(self, lecturer_name):
        result = self.lookup_by_lecturer(lecturer_name)
        if result.get('found'):
            dn = lecturer_name.title()
            header = f"👨‍🏫 Courses taught by {dn}\n{'='*60}\nFound {result['count']} schedule{'s' if result['count'] > 1 else ''}:\n\n"
            raw = header + "\n".join(result['results'])
            return raw, False
        return f"❌ I could not find any courses taught by '{lecturer_name.title()}'. Please check the name and try again.", True

    def handle_venue_query(self, day, time_slot):
        result = self.lookup_free_venues(day, time_slot)
        if result.get('found'):
            day_text = f" on {day}" if day else ""
            time_text = f" at {time_slot.strftime('%I:%M %p').lstrip('0')}" if time_slot else ""
            header = f"🏛️ Free Venues{day_text}{time_text}\n{'='*60}\nFound {result['count']} free venue{'s' if result['count'] > 1 else ''}:\n\n"
            return header + "\n".join(result['results']), False
        day_text = f" on {day}" if day else ""
        time_text = f" at {time_slot.strftime('%I:%M %p').lstrip('0')}" if time_slot else ""
        return f"❌ No free venues{day_text}{time_text}. All venues may be occupied.", True

    def get_not_found_response(self, course):
        return (
            f"❌ Course Not Found\n\n"
            f"I could not find **{course}** in the timetable or curriculum.\n\n"
            f"Possible reasons:\n"
            f"• The course code may be incorrect — please double-check\n"
            f"• The course may not be scheduled this semester\n"
            f"• Try with or without a space: \"{course.replace(' ', '')}\" or \"{course}\"\n\n"
            f"Other things you can try:\n"
            f"• \"courses in [your program name]\"\n"
            f"• \"courses taught by [lecturer name]\"\n"
            f"• \"free venues on Friday at 7 AM\"\n\n"
            f"If this course should be listed, use the feedback form below to report it."
        ), True


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — Multi-Source Search Engine
# ══════════════════════════════════════════════════════════════════════════════

class MultiSourceSearch:
    def __init__(self): self.wikipedia = WikipediaSearch(); self.ddg = DuckDuckGoSearch()
    def search(self, query):
        r = self.ddg.search(query)
        if r: return r
        w = self.wikipedia.search(query)
        if w: return w['content']
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — Response Generator
# ══════════════════════════════════════════════════════════════════════════════

class ResponseGenerator:
    def __init__(self):
        self.multi_search = MultiSourceSearch()
        self.calculator = Calculator()
        self.news_search = NewsSearch()
        self.venue_keywords = ['free venue','free room','available venue','available room','empty venue']
        self.general_query_patterns = [
            r'^what is\s+', r'^who is\s+', r'^where is\s+', r'^when is\s+', r'^why is\s+', r'^how to\s+',
            r'^meaning of\s+', r'^definition of\s+', r'^explain\s+', r'^tell me about\s+',
            r'^history of\s+', r'^origin of\s+', r'^facts about\s+', r'^information about\s+',
        ]
        self.non_timetable_words = [
            'weather','sports','politics','economy','music','movie','film','book',
            'author','artist','celebrity','recipe','cook','travel','tourism',
        ]
        self.timetable_signals = [
            'timetable','schedule','class','course','unit','lecture','lec','lecturer',
            'venue','room','exam','cat','test','program','programme','year','yr','semester',
            'monday','tuesday','wednesday','thursday','friday','today','tomorrow',
        ]
        # Word-boundary-safe compiled versions — plain `substring in text` checks
        # false-positive badly here (e.g. 'cat' inside 'communicate', 'yr'
        # inside almost anything). All matching below goes through these.
        self.venue_kw_re = _kw_regex(self.venue_keywords)
        self.non_tt_re = _kw_regex(self.non_timetable_words)
        self.tt_signal_re = _kw_regex(self.timetable_signals)

    def is_general_query(self, message):
        ml = message.lower().strip()
        if self.tt_signal_re.search(ml): return False
        if re.search(r'\b[A-Z]{2,5}\s*\d{3,5}', message, re.IGNORECASE): return False
        for pat in self.general_query_patterns:
            if re.match(pat, ml, re.IGNORECASE): return True
        return bool(self.non_tt_re.search(ml))

    def generate(self, message, session_id, has_timetable_data, timetable_handler):
        if is_crisis_query(message): return get_crisis_response(), True
        intel = input_intelligence.preprocess(message)
        processed = intel['cleaned']
        if intel['is_missing_context']:
            answer = ("🤔 I'd love to help! Could you tell me which course or unit you're asking about?\n\nFor example:\n"
                      "• \"When is COSC 301?\"\n• \"Database Systems timetable\"\n• \"What do I have on Monday?\"")
            context_manager.update_context(session_id, topic='clarification', question=message, answer=answer)
            return answer, False
        if intel['is_broad'] and not self._has_course_intent(message):
            answer = ("📋 That's a broad request!\n\nFor a focused answer, try:\n"
                      "• \"Show timetable for Computer Science Year 2\"\n• \"When is COSC 301?\"\n• \"Courses taught by Dr Ogembo\"\n\n"
                      "Would you like the full week timetable for a specific program?")
            context_manager.update_context(session_id, topic='broad', question=message, answer=answer)
            return answer, False
        if intel['sub_queries']:
            parts = []; sfany = False
            for i, sub in enumerate(intel['sub_queries'], 1):
                sub2 = self._enrich_with_day(sub, intel)
                rep, sf = self._dispatch(sub2, session_id, has_timetable_data, timetable_handler, intel)
                parts.append(f"{i}. {rep}"); sfany = sfany or sf
            if parts: return "\n\n---\n\n".join(parts), sfany
        enriched = self._enrich_with_day(processed, intel)
        return self._dispatch(enriched, session_id, has_timetable_data, timetable_handler, intel)

    def _enrich_with_day(self, text, intel):
        if intel and intel.get('day') and intel['day'].lower() not in text.lower():
            return f"{text} {intel['day']}"
        return text

    def _has_course_intent(self, message: str) -> bool:
        """
        Returns True when the message contains a clear timetable / course intent
        that must NOT be swallowed by any soft-topic handler (greeting, time,
        news, thanks, bye, fact, form, about-bot, etc.).

        Criteria (any one is sufficient):
          • A course-code pattern  e.g. COSC 301 / NURS204
          • A timetable-signal keyword  e.g. lecture, schedule, venue, exam …
          • A "when/where/what/who is … [digits]" query  e.g. "when is nuru 101"
          • A lecturer-title phrase  e.g. "courses taught by Dr Ogembo"
          • A "courses in / timetable for [program]" phrase
        """
        ml = message.lower()
        if re.search(r'\b[A-Z]{2,6}\s*\d{3,6}\b', message, re.IGNORECASE):
            return True
        if self.tt_signal_re.search(ml):
            return True
        if re.search(r'\b(when|where|schedule|timetable)\b.{1,60}\b\d{3,6}\b', message, re.IGNORECASE):
            return True
        if re.search(r'\b(taught by|courses? (in|for|by)|timetable for|lecturer for)\b', ml):
            return True
        if re.search(r'\b(dr|prof|mr|ms|mrs)\.?\s+\w+\b', ml):
            return True
        return False

    def _dispatch(self, message, session_id, has_timetable_data, timetable_handler, intel=None):
        context = context_manager.get_context(session_id)
        msg_lower = message.lower()

        # ── Pre-compute course intent once; used to guard every soft-topic branch ──
        course_intent = self._has_course_intent(message)

        # ── 1. GREETING ───────────────────────────────────────────────────────────
        # Only fire when the message is PURELY a greeting — no course question mixed in.
        is_greeting_word = bool(_GREETING_RE.search(msg_lower))
        if is_greeting_word and not course_intent:
            answer = random.choice([
                "👋 Hello! How can I help you today?\n\nI can help you with:\n• Course timetables\n• Program courses\n• Lecturer schedules\n• Free venues\n• University info\n\nWhat would you like to know?",
                "👋 Hi there! Try asking:\n• 'when is COSC 301?'\n• 'courses in Computer Science'\n• 'courses taught by Dr Ogembo'\n• 'free venues on Friday at 7 AM'",
            ])
            context_manager.update_context(session_id, topic='greeting', question=message, answer=answer)
            return answer, False

        # ── 2. FOLLOW-UP (context continuation) ──────────────────────────────────
        # Only follow up if there is no independent course intent in this message;
        # otherwise a new question like "ok, when is COSC 301?" would be eaten by
        # the previous topic's context.
        is_followup = context_manager.is_followup(message, context)
        if is_followup and context.get('last_topic') and not course_intent:
            ca, tu = get_chuka_answer(message, context_topic=context['last_topic'])
            if ca:
                context_manager.update_context(session_id, topic=tu, question=message, answer=ca)
                return ca, False

        # ── 3. ENTITY-HINT CLARIFICATIONS ────────────────────────────────────────
        # Only redirect when there is NO course intent mixed in (e.g. "where is
        # Dr Ogembo" is fine to redirect; "where is COSC 301 venue" must not be).
        if intel and not course_intent:
            ml = msg_lower
            if intel.get('entity_hint') == 'lecturer' and re.match(r'^where is\s+', ml):
                name = re.sub(r'^where is\s+', '', ml).strip()
                d = f"🤔 Did you mean where does {name.title()} teach?\nTry: 'courses taught by {name.title()}'"
                context_manager.update_context(session_id, topic='clarification', question=message, answer=d)
                return d, False
            if intel.get('entity_hint') == 'room' and re.match(r'^when is\s+', ml):
                # Only redirect if there is no digit sequence that looks like a course number
                if not re.search(r'\b\d{3,6}\b', message):
                    room = re.sub(r'^when is\s+', '', ml).strip()
                    d = f"🏛️ It looks like you're asking about Room {room.upper()}.\nTry: 'free venues on [day] at [time]'"
                    context_manager.update_context(session_id, topic='clarification', question=message, answer=d)
                    return d, False

        # ── 4. MY-LECTURER shorthand ──────────────────────────────────────────────
        if re.search(r'\b(my lec|my lecturer|my teacher|my instructor)\b', msg_lower):
            ce = context.get('last_entity')
            answer = (f"The lecturer for {ce} is shown in the timetable above.\nAsk 'courses taught by [name]' for their full schedule." if ce
                      else "I don't know which course you mean yet.\nTry: 'who teaches COSC 301?' or 'courses taught by Dr Ogembo'")
            context_manager.update_context(session_id, topic='lecturer', question=message, answer=answer)
            return answer, False

        # ── 5. CALCULATOR ─────────────────────────────────────────────────────────
        # Guard: skip if message also looks like a course query
        if not course_intent:
            calc = self.calculator.calculate(message)
            if calc:
                answer = f"🧮 Calculation Result\n\n{calc['expression']} = {calc['result']}"
                context_manager.update_context(session_id, topic='calculation', question=message, answer=answer)
                return answer, False

        # ── 6. TIMETABLE / COURSE / VENUE (primary handler) ──────────────────────
        has_code_pat = bool(re.search(r'\b[A-Z]{2,5}\s*\d{3,5}', message, re.IGNORECASE))
        has_tt_signal = bool(self.tt_signal_re.search(msg_lower))
        # Catch "when is [name] [number]" even when the prefix isn't a standard code
        has_when_course = bool(re.search(
            r'\b(when|where|schedule|timetable)\b.{1,60}\b\d{3,5}\b', message, re.IGNORECASE
        ))
        if has_timetable_data or has_code_pat or has_tt_signal or has_when_course or course_intent:
            if self.venue_kw_re.search(msg_lower):
                day = (intel['day'] if intel and intel.get('day') else
                       (lambda m: m.group(1).capitalize() if m else None)(re.search(r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', msg_lower)))
                time_slot = None
                if intel and intel.get('time_range'):
                    time_slot = time(intel['time_range'][0], 0)
                else:
                    tm = re.search(r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', msg_lower)
                    if tm:
                        h = int(tm.group(1)); mn = int(tm.group(2)) if tm.group(2) else 0
                        ap = tm.group(3) or 'am'
                        if ap == 'pm' and h != 12: h += 12
                        elif ap == 'am' and h == 12: h = 0
                        time_slot = time(h, mn)
                result, sf = timetable_handler.handle_venue_query(day, time_slot)
                if result:
                    context_manager.update_context(session_id, topic='venue', question=message, answer=result[:500])
                    return result, sf

            result, sf = timetable_handler.handle_query(message, has_timetable_data)
            if result:
                context_manager.update_context(session_id, topic='timetable', question=message, answer=result[:500])
                return result, sf

            if has_code_pat:
                extracted = timetable_handler.matcher.extract_course_codes(message)
                if extracted:
                    result, sf = timetable_handler.get_not_found_response(extracted[0])
                    context_manager.update_context(session_id, topic='timetable', question=message, answer=result[:500])
                    return result, sf

        # ── 7. GENERAL WEB SEARCH ─────────────────────────────────────────────────
        if self.is_general_query(message):
            sr = self.multi_search.search(message)
            if sr:
                answer = f"🔍 Search Results\n\n{sr}"
                if len(answer) > 600:
                    cut = answer.rfind('. ', 0, 600); answer = answer[:cut+1] if cut > 100 else answer[:600] + '...'
                context_manager.update_context(session_id, topic='general', question=message, answer=answer)
                return answer, False

        # ── 8. NEWS ───────────────────────────────────────────────────────────────
        # Guard: "news on COSC 301 schedule" must not land here
        if _NEWS_RE.search(msg_lower) and not course_intent:
            topic = re.sub(r'\b(news|latest|about)\b', '', msg_lower).strip() or 'Kenya'
            nr = self.news_search.search(topic)
            if nr and nr.get('items'):
                resp = f"📰 Latest {topic.title()} News 📰\n\n"
                for item in nr['items'][:3]:
                    resp += f"• {item['title']}\n"
                    if item.get('snippet'): resp += f"  {item['snippet']}\n\n"
                context_manager.update_context(session_id, topic='news', question=message, answer=resp[:500])
                return resp, False

        # ── 9. FUN FACT ───────────────────────────────────────────────────────────
        # Guard: "did you know when COSC 301 is?" must not land here
        if _FACT_RE.search(msg_lower) and not course_intent:
            answer = f"💡 Fun Fact 💡\n\n{get_fun_fact()}"
            context_manager.update_context(session_id, topic='fact', question=message, answer=answer)
            return answer, False

        # ── 10. TIME / DATE ───────────────────────────────────────────────────────
        # Guard: "what time is COSC 301?" or "date of NURS 101 exam?" must not land here
        if _TIME_RE.search(msg_lower) and not course_intent:
            now = datetime.now()
            answer = f"🕐 Current Time 🕐\n\n{now.strftime('%A, %B %d, %Y')}\n{now.strftime('%I:%M %p')}"
            context_manager.update_context(session_id, topic='time', question=message, answer=answer)
            return answer, False

        # ── 11. ABOUT BOT ─────────────────────────────────────────────────────────
        if _ABOUT_BOT_RE.search(msg_lower) and not course_intent:
            answer = _about_bot_answer()
            context_manager.update_context(session_id, topic='about_bot', question=message, answer=answer)
            return answer, False

        # ── 12. THANKS ────────────────────────────────────────────────────────────
        # Guard: "thanks, when is COSC 301?" must not return "You're welcome!"
        if _THANKS_RE.search(msg_lower) and not course_intent:
            answer = random.choice(["You're welcome! 😊", "My pleasure! 👍", "Happy to help! 🌟"])
            context_manager.update_context(session_id, topic='thanks', question=message, answer=answer)
            return answer, False

        # ── 13. FAREWELL ──────────────────────────────────────────────────────────
        # Guard: "bye the way, when is COSC 301?" must not return "Goodbye!"
        if _FAREWELL_RE.search(msg_lower) and not course_intent:
            answer = random.choice(["Goodbye! 👋 Come back if you have more questions.", "Take care! 🌟", "Bye! 😊"])
            context_manager.update_context(session_id, topic='farewell', question=message, answer=answer)
            return answer, False

        # ── 14. CHUKA KB FALLBACK ─────────────────────────────────────────────────
        ca, tu = get_chuka_answer(message)
        if ca:
            context_manager.update_context(session_id, topic=tu, question=message, answer=ca)
            return ca, False

        # ── 14.5 PRE-SEARCH SAFETY/ROUTING GATE ──────────────────────────────────
        # Everything from here down used to be "forward the raw message to a
        # live public web search and show whatever comes back" with only a
        # hardcoded bad-word list standing in the way. That list is always one
        # step behind — it caught 'fuck' but not 'fuckoff', and had no chance
        # against a slur nobody thought to add, or against "who powers you" /
        # "so you're powered by grok" (which just matched literal words like
        # "powers" in the search engine and returned nonsense). The cheap regex
        # below is a fast, zero-latency first pass for the obvious cases; the
        # AI classifier right after it is the actual general fix — it judges
        # MEANING, so it generalizes to phrasings we've never seen instead of
        # needing a new keyword added every time someone finds a new way past
        # the list.
        if _PROFANITY_RE.search(msg_lower):
            return ("I hear you're frustrated 🙂 I'm just a timetable assistant, so I won't "
                    "respond to that — but I'm still happy to help with courses, programs, "
                    "lecturers, or venues whenever you're ready."), False

        route = _ai_route_before_search(message)
        if route == 'INAPPROPRIATE':
            return ("I hear you're frustrated 🙂 I'm just a timetable assistant, so I won't "
                    "respond to that — but I'm still happy to help with courses, programs, "
                    "lecturers, or venues whenever you're ready."), False
        if route == 'ABOUT_BOT':
            answer = _about_bot_answer()
            context_manager.update_context(session_id, topic='about_bot', question=message, answer=answer)
            return answer, False

        # ── 15. BROAD WEB SEARCH FALLBACK ────────────────────────────────────────
        if len(message) > 2:
            sr = self.multi_search.search(message)
            if sr:
                answer = f"🔍 Search Results 🔍\n\n{sr}"
                if len(answer) > 600:
                    cut = answer.rfind('. ', 0, 600); answer = answer[:cut+1] if cut > 100 else answer[:600] + '...'
                context_manager.update_context(session_id, topic='general', question=message, answer=answer)
                return answer, False

        # ── 16. FEEDBACK FORM ─────────────────────────────────────────────────────
        # Guard: "can you report when COSC 301 is?" must not open the form
        if _FORM_RE.search(msg_lower) and not course_intent:
            answer = "📝 Report an Issue\n\nUse the feedback form below — it goes directly to the timetabling office.\n\nPlease include the course code, your program, and any relevant details."
            context_manager.update_context(session_id, topic='form', question=message, answer=answer)
            return answer, True

        # ── 17. HELP ──────────────────────────────────────────────────────────────
        if _HELP_RE.search(msg_lower) and not course_intent:
            answer = """❓ How Can I Help You?

📚 Single Course:  "when is COSC 301?" / "when is NURS 101?"
📚 Multiple:        "when is COSC 301 and COSC 333"
🎓 Program:        "courses in Computer Science" / "nursing year 2"
👨‍🏫 Lecturer:       "courses taught by Dr Ogembo"
🏛️ Venues:         "free venues on Friday at 7 AM"
🌍 General:        "what is artificial intelligence?"
🧮 Math:           "15 * 32"
📰 News:           "latest Kenya news"
💡 Facts:          "tell me a fun fact"
🎓 University:     "who is the vc?" / "where is chuka located?"

What would you like to know?"""
            context_manager.update_context(session_id, topic='help', question=message, answer=answer)
            return answer, False

        # ── 18. FINAL FALLBACK ────────────────────────────────────────────────────
        answer = """🤔 I'm Not Sure I Understood

Try asking about:
📚 Timetables: "when is COSC 301?"
🎓 Programs:   "courses in Computer Science"
👨‍🏫 Lecturers: "courses taught by Dr Ogembo"
🏛️ Venues:     "free venues on Friday at 7 AM"
🌍 General:    "what is artificial intelligence?"
💡 Help:       type "help" for a full list"""
        context_manager.update_context(session_id, topic='fallback', question=message, answer=answer)
        return answer, True


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — DB helper
# ══════════════════════════════════════════════════════════════════════════════

def _has_data(ttype=None):
    if not CHATBOT_MODELS_AVAILABLE: return False
    try:
        qs = ChatbotPublication.objects.filter(is_latest=True)
        if ttype: qs = qs.filter(timetable_type=ttype)
        if not qs.exists(): return False
        pub_ids = list(qs.values_list('id', flat=True))
        return ChatbotTimetableEntry.objects.filter(publication_id__in=pub_ids).exists()
    except Exception as e:
        logger.error(f"Error checking timetable data: {e}"); return False


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 14 — Feedback form handler
# ══════════════════════════════════════════════════════════════════════════════

def save_feedback(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "success": False, "error": "Method not allowed."}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "success": False, "error": "Invalid JSON."}, status=400)
    full_name = data.get("full_name", "").strip()
    email = data.get("email", "").strip() or "no-email@provided.com"
    admission_number = data.get("admission_number", "").strip()
    message = data.get("message", "").strip()
    if not message:
        return JsonResponse({"ok": False, "success": False, "error": "Message is required."})
    try:
        fb = Feedback.objects.create(
            full_name=full_name or "Anonymous", email=email,
            admission_number=admission_number or "", message=message, status="unseen"
        )
        log_feedback_submit(
            full_name=full_name or "Anonymous",
            email=email,
            admission_number=admission_number,
            message_length=len(message),
            saved=True,
        )
        return JsonResponse({"ok": True, "success": True, "id": fb.id, "feedback_id": fb.id,
                             "message": "Thank you! Your feedback has been sent to the timetabling office."})
    except Exception as e:
        logger.error(f"Error saving feedback: {e}")
        log_feedback_submit(
            full_name=full_name or "Anonymous",
            email=email,
            admission_number=admission_number,
            message_length=len(message),
            saved=False,
            error=str(e),
        )
        return JsonResponse({"ok": False, "success": False, "error": "Unable to save. Please try again."})


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 15 — Main Chat Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
def bot_chat(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    raw_message = _html.escape((body.get("message") or "").strip())[:1200]
    session_id = body.get("session_id", "default_session")
    direct_verify = body.get("_direct_verify", False)

    if not raw_message and not direct_verify:
        return JsonResponse({"ok": False, "error": "Empty message."}, status=400)

    if direct_verify:
        try:
            payload = json.loads(body.get("message", "{}"))
        except Exception:
            return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)
        if not payload.get("_bot_verify"):
            return JsonResponse({"ok": False, "error": "Not a verify request."}, status=400)
        return JsonResponse({"ok": True, "action": "no_collision"})

    timetable_handler = TimetableHandler()
    response_generator = ResponseGenerator()
    has_data = _has_data()

    reply, show_form = response_generator.generate(raw_message, session_id, has_data, timetable_handler)

    # Determine topic for logging
    topic = "other"
    try:
        ctx = context_manager.get_context(session_id)
        t = ctx.get("last_topic") or "other"
        _map = {
            "greeting": "greeting", "farewell": "greeting",
            "timetable": "timetable", "program": "program",
            "lecturer": "lecturer", "venue": "venue",
            "calculation": "calculation", "news": "news",
            "general": "general", "fallback": "fallback",
            "form": "form", "about_bot": "other", "thanks": "other", "time": "other",
        }
        for k in CHUKA_KNOWLEDGE: _map[k] = "university"
        topic = _map.get(t, "other")
    except Exception:
        pass

    if CHAT_LOG_AVAILABLE and ChatConversationLog is not None:
        try:
            ChatConversationLog.objects.create(
                session_id=session_id[:128], user_message=raw_message[:2000],
                bot_reply=reply[:4000], topic=topic[:80], query_type=topic[:40],
                bot_failed=show_form,
            )
            if random.random() < 0.05:
                try: ChatConversationLog.purge_old()
                except Exception: pass
        except Exception as log_err:
            logger.warning(f"Failed to log conversation: {log_err}")

    # ── Write to feedback action log file ─────────────────────────────────────
    log_chat_query(
        user_message=raw_message,
        intent=topic,
        handler_used="ResponseGenerator.generate",
        ai_used=AI_REGISTRY_AVAILABLE,
        response_text=reply,
    )

    return JsonResponse({"ok": True, "reply": reply, "show_form": show_form, "action": None})
