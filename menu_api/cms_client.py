"""Async HTTP client for the CMS POS API (hq-qrcode-admin backend).

Auth pattern (confirmed from CMS source):
    Authorization: Bearer <token>
    X-Tenant:      <tenant_id>

The CMS returns `{result: bool, code, message, data: {...}}`. We do NOT raise
on `result=false` — the orchestrator inspects the response and decides whether
to record success / failure per item. We DO raise on HTTP 4xx/5xx so transport
problems surface immediately.
"""
from typing import Any, Optional

import httpx

from menu_api.settings import settings


class CMSError(RuntimeError):
    """Wraps any non-2xx response or transport failure from the CMS."""


class CMSClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token:    Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout:  Optional[float] = None,
    ):
        self.base_url  = (base_url  or settings.CMS_BASE_URL  or "").rstrip("/")
        self.token     = token      or settings.CMS_TOKEN
        self.tenant_id = tenant_id  or settings.CMS_TENANT_ID
        timeout        = timeout    or settings.CMS_TIMEOUT_SECONDS

        missing = [n for n, v in [
            ("CMS_BASE_URL",  self.base_url),
            ("CMS_TOKEN",     self.token),
            ("CMS_TENANT_ID", self.tenant_id),
        ] if not v]
        if missing:
            raise CMSError(f"CMS env vars missing: {', '.join(missing)}")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Tenant":      str(self.tenant_id),
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
            timeout=timeout,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self._client.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.HTTPError as e:
            raise CMSError(f"transport error on POST {path}: {type(e).__name__}: {e}") from e
        if resp.status_code >= 400:
            raise CMSError(f"HTTP {resp.status_code} on POST {path}: {resp.text[:300]}")
        try:
            return resp.json()
        except Exception as e:
            raise CMSError(f"non-JSON response on POST {path}: {resp.text[:200]}") from e

    # ---------- Concrete endpoints ----------
    async def create_food_condition(self, payload: dict) -> dict:
        return await self._post("/v1/foodcondition/create", payload)

    async def create_group_food(self, payload: dict) -> dict:
        return await self._post("/v1/group_food/create", payload)

    async def create_food(self, payload: dict) -> dict:
        return await self._post("/v1/food/create", payload)


# ---------- Convenience helpers ----------
def extract_id(cms_response: Any) -> Optional[str]:
    """Pull `data.id` out of a CMS response, tolerating shape variations."""
    if not isinstance(cms_response, dict):
        return None
    data = cms_response.get("data")
    if isinstance(data, dict):
        return data.get("id") or data.get("Id")
    return None


def is_success(cms_response: Any) -> bool:
    return isinstance(cms_response, dict) and cms_response.get("result") is True


def error_message(cms_response: Any) -> str:
    if not isinstance(cms_response, dict):
        return "unknown response"
    return cms_response.get("message") or cms_response.get("error") or "no message"
