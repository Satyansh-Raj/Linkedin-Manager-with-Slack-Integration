import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompt_template.txt"

TONE_DIRECTIVES = {
    "as_is": {
        "instruction": "bypass", 
        "label": "Post As-Is (No Improvements)",
    },
    "warm_human": {
        "instruction": (
            "Write in a warm, casual, first-person tone. Use relatable language, "
            "short sentences, and high empathy. Sound like a real person, not a press release. "
            "Include 3-5 relevant hashtags at the end."
        ),
        "label": "Warm & Human",
    },
    "highly_technical": {
        "instruction": (
            "Write with precision and technical depth. Lead with the problem, "
            "name the tools and architecture decisions explicitly, and focus on "
            "engineering outcomes. No fluff, no buzzwords. No hashtags."
        ),
        "label": "Highly Technical",
    },
    "empathetic": {
        "instruction": (
            "Focus on the human struggle behind the work. Be vulnerable and honest "
            "about the difficulty. Readers should feel the journey, not just the outcome. "
            "Write with emotional honesty. Include 3-5 relevant hashtags at the end."
        ),
        "label": "Empathetic / Human Journey",
    },
    "professional": {
        "instruction": (
            "Use crisp, corporate language. Focus on business value, ROI, and strategic impact. "
            "Avoid casual phrasing. Structure the post clearly with a strong opening statement. "
            "No hashtags."
        ),
        "label": "Professional / Corporate",
    },
    "aggressive_sales": {
        "instruction": (
            "Write with bold, direct energy. Create urgency. Use a clear call to action. "
            "Be confident and assertive — this post should make people stop scrolling. "
            "Include 3-5 high-impact hashtags at the end."
        ),
        "label": "Aggressive / Sales",
    },
}

DEFAULT_TONE = "professional"


def _load_base_rules() -> str:

    if not PROMPT_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"prompt_template.txt not found at {PROMPT_TEMPLATE_PATH}. "
            "This file contains the base writing rules — it must exist."
        )
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()


def generate_post(raw_notes: str, tone: str = DEFAULT_TONE, feedback: str = None) -> str:

    if tone == "as_is":
        return raw_notes.strip()
    # Initialise client — picks up GEMINI_API_KEY from environment automatically
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    # Load base rules from file + inject tone directive
    base_rules       = _load_base_rules()
    tone_config      = TONE_DIRECTIVES.get(tone, TONE_DIRECTIVES[DEFAULT_TONE])
    tone_label       = tone_config["label"]
    tone_instruction = tone_config["instruction"]

    system_instruction = (
        f"{base_rules}\n\n"
        f"ACTIVE TONE DIRECTIVE: {tone_label}\n"
        f"{tone_instruction}"
    )

    user_message = f"Write a LinkedIn post based on these notes:\n{raw_notes}"
    if feedback:
        user_message += f"\n\nThe previous version was rejected. Address this feedback: {feedback}"

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
        ),
    )

    return response.text.strip()


def get_tone_options() -> list:

    return [
        {
            "text": {"type": "plain_text", "text": config["label"]},
            "value": key,
        }
        for key, config in TONE_DIRECTIVES.items()
    ]
