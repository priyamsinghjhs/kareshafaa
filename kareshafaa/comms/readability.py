"""Reading-level and tone checks for patient-facing text (every supported language).

English: Flesch-Kincaid grade level and Flesch Reading Ease (standard formulas, heuristic syllable counter),
plus sentence length and long-word share. Arabic, Hindi and Urdu have no validated equivalent of FK in common
use, so we apply *proxy* checks that track the same plain-language principles (short sentences, short words,
few long words) with language-specific sentence delimiters. Proxies flag text for human review; they are not a
substitute for testing with patients.

Tone (all languages): no blame/command words, a supportive phrase in result messages, and alarming words
("cancer") only in a reassuring/negated sentence. Placeholders such as {name} are replaced by neutral samples
before measuring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SAMPLE = {"name": "Sara", "programme": "screening", "meaning": "", "next_step": "a check", "timeframe": "soon",
          "link": "the link", "date": "Monday"}
_SENT = {"en": r"[.!?]+(?:\s|$)", "ar": r"[.!?؟]+(?:\s|$)", "ur": r"[.!?؟۔]+(?:\s|$)|۔", "hi": r"[.!?।]+(?:\s|$)|।"}

# thresholds (documented in docs/patient_messages.md)
EN_MAX_GRADE = 8.0          # NHS / AMA guidance: patient information at or below US grade 6-8
EN_MAX_GRADE_SIMPLE = 6.0   # 'simplified' variants
EN_MIN_EASE = 60.0          # Flesch Reading Ease >= 60 = "plain English"
MAX_AVG_WORDS = 20.0        # average words per sentence (all languages)
MAX_SENTENCE_WORDS = 30     # any single sentence
MAX_LONG_WORD_SHARE = {"en": 0.20, "ar": 0.25, "hi": 0.15, "ur": 0.15}  # en: 3+ syllables; others: >= 9 letters (ar), >= 8 chars (hi/ur)

BLAME = {"en": [r"\bfailed to\b", r"\byou must\b", r"\bnon-?complian", r"\byour fault\b", r"\bignored\b", r"\brefused\b"],
         "ar": ["يجب عليك", "رفضت", "تجاهلت"], "hi": ["आपको हर हाल में", "आपकी गलती", "आपने मना किया"],
         "ur": ["آپ کو ہر حال میں", "آپ کی غلطی", "آپ نے انکار کیا"]}
ALARM = {"en": r"\bcancer\b", "ar": "سرطان", "hi": "कैंसर", "ur": "کینسر"}
REASSURE = {"en": r"\b(not|no|usually|most|many)\b|NOT", "ar": "لا |لا يعني|غالباً|معظم|كثير", "hi": "नहीं|ज़्यादातर|कई|आमतौर",
            "ur": "نہیں|زیادہ تر|کئی|عام طور"}


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    if w.endswith("ed") and not w.endswith(("ted", "ded")) and n > 1:
        n -= 1
    return max(1, n)


def _fill(text: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: SAMPLE.get(m.group(1), "it"), text)


def sentences(text: str, lang: str) -> list[str]:
    parts = re.split(_SENT.get(lang, _SENT["en"]), _fill(text).strip())
    return [p.strip() for p in parts if p and re.search(r"\w", p)]


def words(s: str) -> list[str]:
    # whitespace tokenisation: Python's \w does not match Devanagari vowel signs (category Mn/Mc), so a regex word
    # pattern would split Hindi words; punctuation is stripped and digits-only tokens are ignored
    toks = (t.strip(".,;:!?()\"'“”‘’-–—،؛؟۔।") for t in s.split())
    return [t for t in toks if t and any(ch.isalpha() for ch in t)]


@dataclass
class Readability:
    lang: str
    sentences: int
    words: int
    avg_words_per_sentence: float
    max_sentence_words: int
    long_word_share: float
    fk_grade: float | None = None
    reading_ease: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("lang", "sentences", "words", "avg_words_per_sentence",
                                               "max_sentence_words", "long_word_share", "fk_grade", "reading_ease",
                                               "problems")} | {"ok": self.ok}


def _is_long(w: str, lang: str) -> bool:
    if lang == "en":
        return _syllables(w) >= 3
    return len(w) >= (9 if lang == "ar" else 8)


def measure(text: str, lang: str = "en", *, simplified: bool = False, result_message: bool = False) -> Readability:
    ss = sentences(text, lang)
    ws_per = [words(s) for s in ss]
    allw = [w for ws in ws_per for w in ws]
    n_s, n_w = max(1, len(ss)), max(1, len(allw))
    r = Readability(lang=lang, sentences=len(ss), words=len(allw), avg_words_per_sentence=round(n_w / n_s, 1),
                    max_sentence_words=max((len(ws) for ws in ws_per), default=0),
                    long_word_share=round(sum(_is_long(w, lang) for w in allw) / n_w, 3))
    if lang == "en":
        syl = sum(_syllables(w) for w in allw)
        r.fk_grade = round(0.39 * n_w / n_s + 11.8 * syl / n_w - 15.59, 1)
        r.reading_ease = round(206.835 - 1.015 * n_w / n_s - 84.6 * syl / n_w, 1)
        limit = EN_MAX_GRADE_SIMPLE if simplified else EN_MAX_GRADE
        if r.fk_grade > limit and n_w >= 12:  # FK is unstable on very short texts
            r.problems.append(f"Flesch-Kincaid grade {r.fk_grade} > {limit}")
        if r.reading_ease < EN_MIN_EASE and n_w >= 12:
            r.problems.append(f"Flesch reading ease {r.reading_ease} < {EN_MIN_EASE}")
    if r.avg_words_per_sentence > MAX_AVG_WORDS:
        r.problems.append(f"average sentence {r.avg_words_per_sentence} words > {MAX_AVG_WORDS}")
    if r.max_sentence_words > MAX_SENTENCE_WORDS:
        r.problems.append(f"longest sentence {r.max_sentence_words} words > {MAX_SENTENCE_WORDS}")
    lim = MAX_LONG_WORD_SHARE.get(lang, 0.2)
    if r.long_word_share > lim and n_w >= 8:
        r.problems.append(f"long-word share {r.long_word_share} > {lim}")
    r.problems += tone_problems(text, lang, result_message=result_message)
    return r


def tone_problems(text: str, lang: str, *, result_message: bool = False) -> list[str]:
    out = []
    t = _fill(text)
    for pat in BLAME.get(lang, []):
        if re.search(pat, t, flags=re.I if lang == "en" else 0):
            out.append(f"blaming/commanding wording: {pat}")
    alarm = ALARM.get(lang)
    if alarm:
        for s in sentences(text, lang):
            if re.search(alarm, s, flags=re.I if lang == "en" else 0) and not re.search(REASSURE[lang], s):
                out.append("alarming word without reassurance in the same sentence")
    if lang == "en" and re.search(r"!{1,}", t):
        out.append("exclamation mark (can read as alarming)")
    if lang == "en" and re.search(r"\b[A-Z]{4,}\b", re.sub(r"\b(NOT|HPV|FIT|HbA1c|HIV)\b", "", t)):
        out.append("shouting (all-capital words)")
    return out
