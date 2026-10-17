"""Application settings. Secrets are loaded from .env and never returned by APIs."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(os.getenv("DATABASE_PATH", BASE_DIR / "storage" / "manjv.db"))
    static_dir: Path = BASE_DIR / "app" / "static"
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    knowledge_dir: Path = Path(os.getenv("KNOWLEDGE_DIR", BASE_DIR / "docs" / "rag" / "kb"))
    knowledge_chunks_path: Path = Path(
        os.getenv("KNOWLEDGE_CHUNKS_PATH", BASE_DIR / "docs" / "rag" / "knowledge_chunks.json")
    )


settings = Settings()
