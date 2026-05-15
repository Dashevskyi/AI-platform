"""Language pin for LLM calls.

Multilingual models (Qwen, Llama, etc) frequently drift into another language —
especially Chinese — on technical content. Putting a single, consistent
language-lock system message into every service call is the universal fix.

Use `build_language_pin(language)` to get a system-role dict you prepend to
`messages` of any chat_completion call.
"""
from __future__ import annotations


# Language tag → human-readable name shown to the model. Add new ones here.
_LANGUAGE_NAMES: dict[str, str] = {
    "ru": "русский",
    "uk": "українська",
    "en": "English",
    "pl": "polski",
    "de": "Deutsch",
    "es": "español",
    "fr": "français",
}


def normalize_language(code: str | None) -> str:
    """Pick a canonical lowercase short tag. Defaults to 'ru' for our tenants.
    Accepts BCP-47-ish strings like 'ru-RU' or 'EN'."""
    if not code:
        return "ru"
    base = code.strip().lower().split("-")[0].split("_")[0]
    return base if base in _LANGUAGE_NAMES else "ru"


def language_name(code: str | None) -> str:
    """Human-readable language name in that very language (for the pin)."""
    return _LANGUAGE_NAMES[normalize_language(code)]


_PIN_TEMPLATES: dict[str, str] = {
    "ru": (
        "Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. "
        "Не переключайся на другие языки даже если запрос или входные данные на ином. "
        "Технические термины, идентификаторы, числа и единицы измерения сохраняй как есть "
        "(IP, MAC, VLAN, BGP, PON и т.п. — латиницей)."
    ),
    "uk": (
        "Відповідай ВИКЛЮЧНО українською мовою. "
        "Не перемикайся на інші мови, навіть якщо запит або вхідні дані іншою мовою. "
        "Технічні терміни, ідентифікатори, числа й одиниці виміру залишай як є."
    ),
    "en": (
        "Respond STRICTLY in English. "
        "Do not switch to other languages even when the input or query is in another language. "
        "Keep technical terms, identifiers, numbers and units verbatim."
    ),
    "pl": (
        "Odpowiadaj WYŁĄCZNIE w języku polskim. "
        "Nie przełączaj się na inne języki, nawet jeśli zapytanie lub dane wejściowe są w innym języku."
    ),
    "de": (
        "Antworte AUSSCHLIESSLICH auf Deutsch. "
        "Wechsle die Sprache nicht, auch wenn die Anfrage oder Eingabe in einer anderen Sprache verfasst ist."
    ),
    "es": (
        "Responde EXCLUSIVAMENTE en español. "
        "No cambies de idioma aunque la consulta o entrada estén en otro idioma."
    ),
    "fr": (
        "Réponds EXCLUSIVEMENT en français. "
        "Ne change pas de langue même si la requête ou l'entrée est dans une autre langue."
    ),
}


def build_language_pin_text(language: str | None) -> str:
    """Return the pin string for the requested language (default ru)."""
    return _PIN_TEMPLATES[normalize_language(language)]


def build_language_pin_message(language: str | None) -> dict:
    """Return a `{"role": "system", "content": ...}` dict ready to prepend
    into a `messages=` list of any chat_completion call."""
    return {"role": "system", "content": build_language_pin_text(language)}
