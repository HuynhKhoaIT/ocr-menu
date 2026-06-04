"""Environment configuration. Reads from .env via python-dotenv."""
import os
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _opt(name: str) -> Optional[str]:
    v = os.environ.get(name, "").strip()
    return v or None


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


class Settings:
    # ===== LLM provider =====
    ANTHROPIC_API_KEY: Optional[str] = _opt("ANTHROPIC_API_KEY")
    OPENAI_API_KEY:    Optional[str] = _opt("OPENAI_API_KEY")
    DEFAULT_MODEL:     str           = os.environ.get("OCR_DEFAULT_MODEL", "gpt-5.5")
    DEFAULT_MAX_TOKENS: int          = _int("OCR_MAX_TOKENS", 12000)

    # ===== CMS target =====
    # Set these to enable /import/menu (full POST pipeline).
    # If any is missing the endpoint returns the validated JSON without pushing.
    CMS_BASE_URL:      Optional[str] = _opt("CMS_BASE_URL")        # e.g. https://tenant-api-beta.digibes.de/
    CMS_TOKEN:         Optional[str] = _opt("CMS_TOKEN")           # Bearer access token
    CMS_TENANT_ID:     Optional[str] = _opt("CMS_TENANT_ID")       # X-Tenant header value
    CMS_RESTAURANT_ID: Optional[str] = _opt("CMS_RESTAURANT_ID")   # restaurantId in payloads

    # ===== Image processing =====
    MAX_PDF_PAGES:  int = _int("OCR_MAX_PDF_PAGES",  5)
    MAX_IMAGE_SIDE: int = _int("OCR_MAX_IMAGE_SIDE", 1600)

    # ===== HTTP =====
    CMS_TIMEOUT_SECONDS: float = float(os.environ.get("CMS_TIMEOUT_SECONDS", "30"))
    CORS_ORIGINS:        str   = os.environ.get("CORS_ORIGINS", "*")

    @property
    def cms_configured(self) -> bool:
        return all([
            self.CMS_BASE_URL, self.CMS_TOKEN,
            self.CMS_TENANT_ID, self.CMS_RESTAURANT_ID,
        ])


settings = Settings()
