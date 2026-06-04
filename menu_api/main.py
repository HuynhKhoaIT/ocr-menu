"""FastAPI app — OCR menu + CMS import.

Run:
    uvicorn menu_api.main:app --reload --port 8000

OpenAPI docs:
    http://localhost:8000/docs
"""
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from menu_api.models import (
    HealthResponse,
    ImportResponse,
    ModelsResponse,
    OCRResponse,
)
from menu_api.orchestrator import import_menu as run_import
from menu_api.pipeline import extract_menu_from_image
from menu_api.settings import settings


app = FastAPI(
    title="Menu OCR Service",
    version="1.0.0",
    description=(
        "OCR menu images via LLM (Claude / OpenAI), validate against the CMS "
        "schema (food_conditions + groups → foods → beilages / combo_items), "
        "and optionally push the result to the CMS POS API."
    ),
)

_origins = (
    ["*"] if settings.CORS_ORIGINS.strip() == "*"
    else [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ============================================================
# Meta endpoints
# ============================================================
@app.get("/", include_in_schema=False)
def root():
    return {
        "service": "menu-ocr",
        "version": "1.0.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        ok=True,
        default_model=settings.DEFAULT_MODEL,
        anthropic_key_set=bool(settings.ANTHROPIC_API_KEY),
        openai_key_set=bool(settings.OPENAI_API_KEY),
        cms_configured=settings.cms_configured,
        cms_base_url=settings.CMS_BASE_URL,
        cms_tenant_id=settings.CMS_TENANT_ID,
        cms_restaurant_id=settings.CMS_RESTAURANT_ID,
    )


@app.get("/models", response_model=ModelsResponse)
def list_models():
    """List Claude / OpenAI models reachable with the configured keys.
    Returns the fallback list when a key is missing or the provider is unreachable."""
    from menu_ocr.clients_claude import get_client, list_available_models
    from menu_ocr.clients_openai import get_openai_client, list_available_openai_models

    claude_models: list = []
    openai_models: list = []
    if settings.ANTHROPIC_API_KEY:
        c, _ = get_client(settings.ANTHROPIC_API_KEY)
        if c:
            claude_models = list_available_models(c)
    if settings.OPENAI_API_KEY:
        c, _ = get_openai_client(settings.OPENAI_API_KEY)
        if c:
            openai_models = list_available_openai_models(c)
    return ModelsResponse(claude=claude_models, openai=openai_models)


# ============================================================
# OCR — extract only
# ============================================================
@app.post("/ocr/menu", response_model=OCRResponse)
async def ocr_menu(
    file: UploadFile = File(..., description="Menu image (PNG/JPG/WebP) or PDF"),
    model:           Optional[str] = Form(None, description=f"Override default model ({settings.DEFAULT_MODEL})"),
    max_tokens:      Optional[int] = Form(None, description=f"Override default max_tokens ({settings.DEFAULT_MAX_TOKENS})"),
    skip_blur_check: bool          = Form(False, description="Skip Laplacian sharpness check"),
    parallel:        bool          = Form(False, description="For multi-page PDF: fan out one LLM call per page and merge server-side. Reduces wall-clock latency for 3+ page PDFs."),
):
    """Upload image/PDF → run LLM extraction → validate → return JSON.

    Does NOT push to the CMS. Use /import/menu for the full pipeline."""
    if not file.content_type or not (
        file.content_type.startswith("image/") or file.content_type == "application/pdf"
    ):
        raise HTTPException(400, f"unsupported content type: {file.content_type!r}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "empty file")

    try:
        result = await extract_menu_from_image(
            file_bytes, file.content_type, model, max_tokens, skip_blur_check, parallel,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return result


# ============================================================
# Full pipeline — extract + push to CMS
# ============================================================
@app.post("/import/menu", response_model=ImportResponse)
async def import_menu_endpoint(
    file: UploadFile = File(..., description="Menu image (PNG/JPG/WebP) or PDF"),
    model:           Optional[str] = Form(None),
    max_tokens:      Optional[int] = Form(None),
    skip_blur_check: bool          = Form(False),
    skip_cms:        bool          = Form(False, description="Run OCR only, do not push to CMS"),
    restaurant_id:   Optional[int] = Form(None, description="Override CMS_RESTAURANT_ID for this request"),
    parallel:        bool          = Form(False, description="For multi-page PDF: extract each page in parallel then merge server-side."),
):
    """Full pipeline:
       1. OCR image → validated menu JSON.
       2. POST /v1/foodcondition/create (one per food_conditions[i]).
       3. POST /v1/group_food/create   (one per groups[i]).
       4. POST /v1/food/create         (one per food + combo sub-foods).

    Failure of any item is recorded in `cms_report` and the pipeline continues."""
    if not file.content_type or not (
        file.content_type.startswith("image/") or file.content_type == "application/pdf"
    ):
        raise HTTPException(400, f"unsupported content type: {file.content_type!r}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "empty file")

    try:
        ocr = await extract_menu_from_image(
            file_bytes, file.content_type, model, max_tokens, skip_blur_check, parallel,
        )
    except RuntimeError as e:
        raise HTTPException(500, f"OCR failed: {e}")

    # Bail before CMS if OCR refused or we were told to skip
    if ocr["status"] != "ok":
        return ImportResponse(ocr=ocr, skipped_cms=True,
                              skip_reason=f"OCR status={ocr['status']}")
    if skip_cms:
        return ImportResponse(ocr=ocr, skipped_cms=True, skip_reason="skip_cms=true")
    if not settings.cms_configured and restaurant_id is None:
        return ImportResponse(ocr=ocr, skipped_cms=True,
                              skip_reason="CMS env vars not configured")

    try:
        report = await run_import(ocr["data"], restaurant_id=restaurant_id)
    except RuntimeError as e:
        raise HTTPException(500, f"CMS import setup failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"CMS import crashed: {type(e).__name__}: {e}")

    return ImportResponse(ocr=ocr, cms_report=report, skipped_cms=False)
