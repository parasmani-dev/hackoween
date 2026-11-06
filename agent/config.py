"""Load only the selected provider's local credentials into its environment."""

import os
from pathlib import Path

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
PROVIDER_VARIABLES = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_MODEL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL"),
}


def load_provider_environment(provider):
    """Use the repository .env as a fallback; existing environment wins."""
    try:
        values = dotenv_values(ENV_FILE, encoding="utf-8-sig", interpolate=False)
    except (OSError, UnicodeError):
        raise ValueError("Could not read local provider configuration.") from None
    for name in PROVIDER_VARIABLES[provider]:
        value = values.get(name)
        if value:
            os.environ.setdefault(name, value)
