from dotenv import load_dotenv
import os


def load_keys(provider: str) -> list[str]:
    load_dotenv()
    keys: list[str] = []
    if "groq" in provider.lower():
        keys = [
            value
            for key, value in os.environ.items()
            if key.startswith("GROQ")
            ]
    if "openrouter" in provider.lower():
        keys = [
            value
            for key, value in os.environ.items()
            if key.startswith("OPENROUTER")
        ]
    if keys == []:
        raise ValueError(f"Unsupported provider: {provider}")
    return keys
