COMMAND_PREFIX = "!"
MAX_TTS_CHARS = 300
FALLBACK_VOICE = "en_us_001"
VOICE_FAILURE_THRESHOLD = 3
VOICE_COOLDOWN_DURATION = 300

TIKTOK_TTS_URL = "https://tiktok-tts.weilnet.workers.dev/api/generation"
GOOGLE_TTS_URL = "https://translate.google.com/translate_tts"
USER_AGENT = "Mozilla/5.0"

# Voice options ported from `js-bot/src/config.js` for `/voice` autocomplete.
TIKTOK_VOICES: list[tuple[str, str]] = [
    # Disney Characters
    ("en_us_ghostface", "Ghost Face"),
    ("en_us_c3po", "C3PO"),
    ("en_us_stitch", "Stitch"),
    ("en_us_stormtrooper", "Stormtrooper"),
    ("en_us_rocket", "Rocket"),
    ("en_female_madam_leota", "Madame Leota"),
    ("en_male_ghosthost", "Ghost Host"),
    ("en_male_pirate", "Pirate"),
    # Standard Voices
    ("en_us_002", "Jessie"),
    ("en_us_006", "Joey"),
    ("en_us_007", "Professor"),
    ("en_us_009", "Scientist"),
    ("en_us_010", "Confidence"),
    # Character Voices
    ("en_male_jomboy", "Game On"),
    ("en_female_samc", "Empathetic"),
    ("en_male_cody", "Serious"),
    ("en_female_makeup", "Beauty Guru"),
    ("en_female_richgirl", "Bestie"),
    ("en_male_grinch", "Trickster"),
    ("en_male_narration", "Story Teller"),
    ("en_male_deadpool", "Mr. GoodGuy"),
    ("en_male_jarvis", "Alfred"),
    ("en_male_ashmagic", "ashmagic"),
    ("en_male_olantekkers", "olantekkers"),
    ("en_male_ukneighbor", "Lord Cringe"),
    ("en_male_ukbutler", "Mr. Meticulous"),
    ("en_female_shenna", "Debutante"),
    ("en_female_pansino", "Varsity"),
    ("en_male_trevor", "Marty"),
    ("en_female_betty", "Bae"),
    ("en_male_cupid", "Cupid"),
    ("en_female_grandma", "Granny"),
    ("en_male_wizard", "Magician"),
    # Regional Voices
    ("en_uk_001", "Narrator"),
    ("en_uk_003", "Male English UK"),
    ("en_au_001", "Metro"),
    ("en_au_002", "Smooth"),
    ("es_mx_002", "Warm"),
]

GOOGLE_VOICES: list[tuple[str, str]] = [
    ("google_translate", "Normal voice"),
]

ALL_VOICES: list[tuple[str, str]] = [*TIKTOK_VOICES, *GOOGLE_VOICES]
VOICE_ID_TO_NAME: dict[str, str] = {voice_id: name for voice_id, name in ALL_VOICES}

POPULAR_VOICE_IDS: list[str] = [
    "en_us_ghostface",
    "en_us_002",
    "en_us_006",
    "en_us_007",
    "en_us_009",
    "en_us_010",
    "en_us_rocket",
    "en_us_c3po",
    "en_us_stitch",
    "en_male_jomboy",
    "en_female_samc",
    "en_male_cody",
    "en_female_makeup",
    "en_female_richgirl",
    "en_male_grinch",
    "en_male_narration",
    "en_male_deadpool",
    "en_male_jarvis",
    "en_female_betty",
    "en_male_cupid",
    "en_female_grandma",
    "en_uk_001",
    "en_au_001",
    "google_translate",
]
