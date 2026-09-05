# Source: "Scenariusz aktorski v2" Notion page
SOURCE_PAGE_ID = "32e3f4160a3781f3bff9cddcb62627ee"

# Obsady database — maps roles to actors
OBSADY_DATABASE_ID = "18e3f4160a378027a209f84d6f931131"

# Parent page for the output page (parent of the script page)
OUTPUT_PARENT_PAGE_ID = "1883f4160a37804d9bb3fdc5d1fa6b54"
OUTPUT_PAGE_TITLE = "Scenariusze indywidualne"

# Google Drive folder for generated PDFs
DRIVE_FOLDER_NAME = "Theatre SGH - Scenariusze Aktorskie"

# Obsady role names use "/" for multi-role entries (e.g. "Moliere/Nauczyciel
# Filozofii").  Simple roles are matched automatically (case-insensitive);
# this dict provides overrides for non-obvious names.
ROLE_OVERRIDES = {
    "Lokaj1/Muzyk1/Krawczyk1": ["LOKAJ I", "KRAWCZYK I"],
    "Lokaj2/Muzyk2": ["LOKAJ II"],
    "Muzyk3/Krawczyk2": ["KRAWCZYK II"],
    "Dorymena/śpiewaczka": ["DORYMENA", "ŚPIEWACZKA"],
    "Moliere/Nauczyciel Filozofii": ["MOLIER", "NAUCZYCIEL FILOZOFII"],
    "Kleont/Malarz": ["KLEONT"],
    "Covielle/Manekin": ["COVIELLE"],
    "Krawcowa": ["KRAWIEC"],
    "Kosmetolożka": [],
    "Luminacja/Oświetlenie": [],
}

# Technical roles: get a cue-sheet style script where ALL callout blocks
# (stage directions + music cues) are treated as their "lines" and all
# character speeches provide context.
TECHNICAL_ROLES = {"Audiacja/Dźwięk"}

# Combined character names that appear in the script → individual characters
COMBINED_CHARACTERS = {
    "NAUCZYCIEL MUZYKI I NAUCZYCIEL TAŃCA": [
        "NAUCZYCIEL MUZYKI",
        "NAUCZYCIEL TAŃCA",
    ],
    "NAUCZYCIEL TAŃCA I NAUCZYCIEL MUZYKI": [
        "NAUCZYCIEL MUZYKI",
        "NAUCZYCIEL TAŃCA",
    ],
    "LOKAJ I I LOKAJ II": ["LOKAJ I", "LOKAJ II"],
    "LUCYLLA I MICHASIA": ["LUCYLLA", "MICHASIA"],
    "PANI JOURDAIN I MICHASIA": ["PANI JOURDAIN", "MICHASIA"],
    "NAUCZYCIEL MUZYKI I NAUCZYCIEL TAŃCA I NAUCZYCIEL FECHTUNKU": [
        "NAUCZYCIEL MUZYKI",
        "NAUCZYCIEL TAŃCA",
        "NAUCZYCIEL FECHTUNKU",
    ],
    "NAUCZYCIEL MUZYKI, NAUCZYCIEL TAŃCA I NAUCZYCIEL FECHTUNKU": [
        "NAUCZYCIEL MUZYKI",
        "NAUCZYCIEL TAŃCA",
        "NAUCZYCIEL FECHTUNKU",
    ],
    "KRAWCZYK I I KRAWCZYK II": ["KRAWCZYK I", "KRAWCZYK II"],
    "JOURDAIN": ["PAN JOURDAIN"],
    "ŚPIEWACZKA": ["DORYMENA"],
}
