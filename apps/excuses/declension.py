"""
Polish name declension heuristics for formal excuse documents.

Covers the most common patterns for:
- Gender detection from first name
- Genitive case (dopełniacz) for student names: "nieobecności Pana Filipa Musiałowskiego"
- Accusative case (biernik) for lecturer names: "prowadzonych przez dr Emilię Tomczyk"
- Nominative → instrumental (narzędnik) for first word of reason text
"""

# Male first names that end in -a (exceptions to the female heuristic)
_MALE_NAMES_A = {"Kuba", "Barnaba", "Kosma", "Bonawentura", "Jarema", "Saba"}


def detect_gender(first_name: str) -> str:
    """Return 'f' or 'm' based on the first name ending."""
    name = first_name.strip()
    if name in _MALE_NAMES_A:
        return "m"
    return "f" if name.endswith("a") else "m"


# ---------------------------------------------------------------------------
# Genitive (dopełniacz) – used for the student: "Pana Filipa Musiałowskiego"
# ---------------------------------------------------------------------------

def _gen_male_first(name: str) -> str:
    if name.endswith("ek"):
        return name[:-2] + "ka"   # Marek→Marka, Jacek→Jacka
    if name.endswith("eł"):
        return name[:-2] + "ła"   # Paweł→Pawła
    if name.endswith("eń"):
        return name[:-2] + "nia"  # rare
    return name + "a"             # Jan→Jana, Filip→Filipa, Michał→Michała


def _gen_male_surname(name: str) -> str:
    if name.endswith("ski"):
        return name[:-1] + "iego"  # Kowalski→Kowalskiego
    if name.endswith("cki"):
        return name[:-1] + "iego"  # Lipnicki→Lipnickiego
    if name.endswith("dzki"):
        return name[:-1] + "iego"
    if name.endswith("ek"):
        return name[:-2] + "ka"    # Dymek→Dymka
    if name.endswith("eł"):
        return name[:-2] + "ła"
    return name + "a"              # Nowak→Nowaka, Sender→Sendera


def _gen_female_first(name: str) -> str:
    if name.endswith("ia"):
        return name[:-1] + "i"     # Maria→Marii, Natalia→Natalii
    if name.endswith("ja"):
        return name[:-1] + "i"     # Alicja→Alicji
    if len(name) >= 2 and name[-2] in "kg" and name.endswith("a"):
        return name[:-1] + "i"     # Monika→Moniki, Olga→Olgi
    if name.endswith("a"):
        return name[:-1] + "y"     # Anna→Anny, Marta→Marty, Aleksandra→Aleksandry
    return name


def _gen_female_surname(name: str) -> str:
    if name.endswith("ska"):
        return name[:-1] + "iej"   # Kowalska→Kowalskiej
    if name.endswith("cka"):
        return name[:-1] + "iej"
    if name.endswith("dzka"):
        return name[:-1] + "iej"
    if name.endswith("a"):
        # Other -a surnames decline like feminine -a nouns: Grzęda→Grzędy, Wojda→Wojdy, Sikora→Sikory
        return _gen_female_first(name)
    # Consonant-ending female surnames are unchanged: Truchel, Duk, Tomczyk
    return name


def genitive_full_name(first: str, last: str, gender: str) -> str:
    """Decline a full name to genitive case."""
    if gender == "m":
        return f"{_gen_male_first(first)} {_gen_male_surname(last)}"
    return f"{_gen_female_first(first)} {_gen_female_surname(last)}"


# ---------------------------------------------------------------------------
# Accusative (biernik) – used for lecturer: "prowadzonych przez dr Emilię Tomczyk"
# ---------------------------------------------------------------------------

def _acc_male_first(name: str) -> str:
    # Masculine animate accusative = genitive
    return _gen_male_first(name)


def _acc_male_surname(name: str) -> str:
    return _gen_male_surname(name)


def _acc_female_first(name: str) -> str:
    if name.endswith("a"):
        return name[:-1] + "ę"   # Emilia→Emilię, Anna→Annę, Monika→Monikę
    return name


def _acc_female_surname(name: str) -> str:
    if name.endswith("ska"):
        return name[:-1] + "ą"   # Kowalska→Kowalską
    if name.endswith("cka"):
        return name[:-1] + "ą"
    if name.endswith("dzka"):
        return name[:-1] + "ą"
    if name.endswith("a"):
        return _acc_female_first(name)  # Grzęda→Grzędę, Wojda→Wojdę
    return name                   # consonant endings unchanged


# ---------------------------------------------------------------------------
# Lecturer helper
# ---------------------------------------------------------------------------

def parse_lecturer(text: str):
    """Split 'dr hab. prof. SGH Emilia Tomczyk' → (title, first, last)."""
    parts = text.strip().split()
    if len(parts) >= 2:
        return " ".join(parts[:-2]), parts[-2], parts[-1]
    if len(parts) == 1:
        return "", parts[0], ""
    return "", "", ""


def decline_lecturer_accusative(text: str) -> str:
    """Decline the lecturer string to accusative for 'prowadzonych przez …'."""
    title, first, last = parse_lecturer(text)
    if not first:
        return text  # can't parse, return as-is

    gender = detect_gender(first)
    if gender == "m":
        first_d = _acc_male_first(first)
        last_d = _acc_male_surname(last) if last else ""
    else:
        first_d = _acc_female_first(first)
        last_d = _acc_female_surname(last) if last else ""

    # With academic title: just title + declined name
    # Without title: add Pana/Panią
    if title:
        parts = [title, first_d, last_d]
    else:
        prefix = "Pana" if gender == "m" else "Panią"
        parts = [prefix, first_d, last_d]

    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Reason: nominative → instrumental (first word only)
# ---------------------------------------------------------------------------

def _to_instrumental(word: str) -> str:
    """Convert a single Polish noun from nominative to instrumental (heuristic).

    Handles common patterns:
      -e  → -em  (przygotowanie→przygotowaniem, zajęcie→zajęciem)
      -o  → -em  (uczestnictwo→uczestnictwem)
      consonant → +em  (udział→udziałem, występ→występem)
    """
    w = word
    if w.endswith("e"):
        return w[:-1] + "em"     # przygotowanie→przygotowaniem
    if w.endswith("o"):
        return w[:-1] + "em"     # uczestnictwo→uczestnictwem
    # Ends in consonant (not a vowel)
    if w and w[-1] not in "aeioóuyąę":
        return w + "em"          # udział→udziałem, występ→występem
    return w


def format_reason_instrumental(reason: str) -> str:
    """Convert a reason phrase so the first word is in instrumental case.

    Input:  'Przygotowanie występu na festiwalu'
    Output: 'przygotowaniem występu na festiwalu'
    """
    words = reason.split(None, 1)
    if not words:
        return reason
    first = _to_instrumental(words[0])
    rest = words[1] if len(words) > 1 else ""
    result = first[0].lower() + first[1:]
    if rest:
        result += " " + rest
    return result
