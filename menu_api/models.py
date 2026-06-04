"""Request / response Pydantic schemas for the FastAPI service."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Usage(BaseModel):
    input_tokens:  int = 0
    output_tokens: int = 0
    cache_read:    int = 0
    cache_create:  int = 0


class HealthResponse(BaseModel):
    ok: bool
    default_model:        str
    anthropic_key_set:    bool
    openai_key_set:       bool
    cms_configured:       bool
    cms_base_url:         Optional[str] = None
    cms_tenant_id:        Optional[str] = None
    cms_restaurant_id:    Optional[str] = None


class OCRResponse(BaseModel):
    """Common shape for /ocr/menu — covers both success and refusal."""
    status: str = Field(..., description="'ok' = menu extracted; 'unreadable' = model refused")
    model: str

    # When status="ok"
    data:    Optional[Dict[str, Any]] = Field(None, description="Validated menu {food_conditions, groups}")
    raw:     Optional[Dict[str, Any]] = Field(None, description="Raw LLM output before validation")
    errors:  List[Dict[str, Any]]     = Field(default_factory=list)
    counts:  Optional[Dict[str, int]] = None
    truncated: bool = False
    max_tokens_used: Optional[int] = None

    # When status="unreadable"
    reason:     Optional[str] = None
    suggestion: Optional[str] = None

    # Always present
    sec:          float
    cost_usd:     Optional[float] = None
    usage:        Optional[Usage] = None
    blur_score:   Optional[float] = None
    stop_reason:  Optional[str]   = None
    n_pages:      Optional[int]   = None


class ImportResponse(BaseModel):
    """Response for /import/menu — wraps the OCR result + the CMS push report."""
    ocr: OCRResponse
    cms_report:  Optional[Dict[str, Any]] = None
    skipped_cms: bool = False
    skip_reason: Optional[str] = None


class ModelsResponse(BaseModel):
    claude: List[str] = []
    openai: List[str] = []
