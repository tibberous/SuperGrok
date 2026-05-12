"""
fuzzy_match.py -- Forgiving string matcher for CLI args, language names, etc.

Normalizes input before comparing:
  - lowercase
  - strip spaces/dashes/underscores
  - common abbreviations (rus -> russian, eng -> english, etc.)

Usage:
    from tools.fuzzy_match import fuzzy_match, fuzzy_match_list

    fuzzy_match("russian",  LANGUAGES)   # -> "ru"
    fuzzy_match("rus",      LANGUAGES)   # -> "ru"
    fuzzy_match("-lang ru", LANGUAGES)   # -> "ru"
    fuzzy_match("ENGLISH",  LANGUAGES)   # -> "en"

    # For CLI args:
    lang = fuzzy_match(args.lang, LANGUAGES) or "en"
"""
from __future__ import annotations
import re
from typing import Any


def normalize(s: str) -> str:
    """Collapse to lowercase alphanum only. 'Ghost White' -> 'ghostwhite'"""
    return re.sub(r"[^a-z0-9]", "", s.lower().strip())


# Common abbreviations -> full normalized form
_ABBREVS: dict[str, str] = {
    # Languages
    "eng": "english",  "en": "english",
    "rus": "russian",  "ru": "russian",
    "spa": "spanish",  "es": "spanish",  "esp": "spanish",
    "fra": "french",   "fr": "french",   "fre": "french",
    "deu": "german",   "de": "german",   "ger": "german",
    "zho": "chinese",  "zh": "chinese",  "chi": "chinese",  "cn": "chinese",
    "jpn": "japanese", "ja": "japanese", "jp": "japanese",
    "kor": "korean",   "ko": "korean",   "kr": "korean",
    "por": "portuguese","pt": "portuguese",
    "ita": "italian",  "it": "italian",
    "nld": "dutch",    "nl": "dutch",
    "pol": "polish",   "pl": "polish",
    "ara": "arabic",   "ar": "arabic",
    "hin": "hindi",    "hi": "hindi",
    "tur": "turkish",  "tr": "turkish",
    "swe": "swedish",  "sv": "swedish",
    "nor": "norwegian","no": "norwegian",
    "dan": "danish",   "da": "danish",
    "fin": "finnish",  "fi": "finnish",
    "ukr": "ukrainian","uk": "ukrainian",
    "vie": "vietnamese","vi": "vietnamese",
    "tha": "thai",     "th": "thai",
    "heb": "hebrew",   "he": "hebrew",
    "ron": "romanian", "ro": "romanian",
    "hun": "hungarian","hu": "hungarian",
    "ces": "czech",    "cs": "czech",    "cze": "czech",
    "slk": "slovak",   "sk": "slovak",
    "hrv": "croatian", "hr": "croatian",
    "cat": "catalan",  "ca": "catalan",
    "bul": "bulgarian","bg": "bulgarian",
    "ell": "greek",    "el": "greek",    "gre": "greek",
    "ind": "indonesian","id": "indonesian",
    "msa": "malay",    "ms": "malay",
    "srp": "serbian",  "sr": "serbian",
    # Booleans / common flags
    "yes": "true",  "y": "true",  "on": "true",  "1": "true",  "enable": "true",
    "no":  "false", "n": "false", "off": "false", "0": "false", "disable": "false",
    # Debug modes
    "dbg": "debug", "verbose": "debug", "v": "verbose",
}


def fuzzy_match(raw: str, options: dict[str, Any] | list[str],
                threshold: float = 0.6) -> str | None:
    """
    Match `raw` against keys of `options` dict or items of list.

    Matching order (first match wins):
      1. Exact key match (after normalization)
      2. Abbreviation expansion -> exact match
      3. Prefix match (normalized input is prefix of a key)
      4. Substring match
      5. Character-overlap ratio (simple, no external deps)

    Returns the matched key/item, or None if no match above threshold.
    """
    if not raw:
        return None

    raw_clean = normalize(raw)
    # Strip leading punctuation like -lang or /language or lan=
    # Handle key=value: "lan=russian" -> use the value side
    if "=" in raw:
        raw_clean = normalize(raw.split("=", 1)[1])
    else:
        # Strip leading flag tokens: "-lang rus" / "/language RUSSIAN" -> last token
        tokens = re.split(r"\s+", raw.strip())
        if len(tokens) > 1:
            raw_clean = normalize(tokens[-1])
        else:
            raw_clean = re.sub(r"^[^a-z0-9]*", "", raw_clean)

    keys = list(options.keys()) if isinstance(options, dict) else list(options)
    norm_keys = {normalize(k): k for k in keys}

    # 1. Exact
    if raw_clean in norm_keys:
        return norm_keys[raw_clean]

    # 2. Abbreviation expansion
    expanded = _ABBREVS.get(raw_clean)
    if expanded and expanded in norm_keys:
        return norm_keys[expanded]

    # 3. Prefix
    prefix_matches = [orig for nk, orig in norm_keys.items() if nk.startswith(raw_clean)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        # Return shortest (most specific prefix match)
        return min(prefix_matches, key=len)

    # 4. Substring
    sub_matches = [orig for nk, orig in norm_keys.items() if raw_clean in nk]
    if len(sub_matches) == 1:
        return sub_matches[0]

    # 5. Character overlap ratio
    best_key, best_score = None, 0.0
    for nk, orig in norm_keys.items():
        score = _overlap(raw_clean, nk)
        if score > best_score:
            best_score, best_key = score, orig
    if best_score >= threshold:
        return best_key

    return None


def fuzzy_match_list(raw: str, options: list[str], **kw) -> str | None:
    return fuzzy_match(raw, options, **kw)


def _overlap(a: str, b: str) -> float:
    """Fraction of characters in `a` that appear in `b` in order."""
    if not a or not b:
        return 0.0
    i = j = matches = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            matches += 1
            i += 1
        j += 1
    return matches / max(len(a), len(b))


# ---------------------------------------------------------------------------
# CLI arg parser helper
# ---------------------------------------------------------------------------

LANGUAGE_CODES: dict[str, str] = {
    "english": "en",   "spanish": "es",   "french": "fr",
    "german": "de",    "chinese": "zh",   "japanese": "ja",
    "korean": "ko",    "portuguese": "pt","italian": "it",
    "russian": "ru",   "arabic": "ar",    "hindi": "hi",
    "turkish": "tr",   "dutch": "nl",     "polish": "pl",
    "swedish": "sv",   "norwegian": "no", "danish": "da",
    "finnish": "fi",   "ukrainian": "uk", "vietnamese": "vi",
    "thai": "th",      "hebrew": "he",    "romanian": "ro",
    "hungarian": "hu", "czech": "cs",     "slovak": "sk",
    "croatian": "hr",  "catalan": "ca",   "bulgarian": "bg",
    "greek": "el",     "indonesian": "id","malay": "ms",
    "serbian": "sr",
}


def parse_language(raw: str) -> str:
    """
    Parse any reasonable language string to a 2-letter code.

    parse_language("russian")      -> "ru"
    parse_language("rus")          -> "ru"
    parse_language("-lang RUSSIAN")-> "ru"
    parse_language("/language ru") -> "ru"
    parse_language("lan=russian")  -> "ru"
    parse_language("unknown_xyz")  -> "en"  (fallback)
    """
    matched = fuzzy_match(raw, LANGUAGE_CODES)
    if matched:
        return LANGUAGE_CODES[matched]
    # Maybe raw IS already a 2-letter code
    code = normalize(raw)[:2]
    for lang, lcode in LANGUAGE_CODES.items():
        if lcode == code:
            return lcode
    return "en"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python fuzzy_match.py <input>")
        sys.exit(1)
    raw = " ".join(sys.argv[1:])
    print(f"Input:    '{raw}'")
    print(f"Language: {parse_language(raw)!r}")
    matched = fuzzy_match(raw, LANGUAGE_CODES)
    print(f"Matched:  {matched!r}")
