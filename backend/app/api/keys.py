"""API keys entered in the app (the "API keys" dialog): GET/PUT /api/settings/keys.

Keys are saved in <data_dir>/api-keys.json (mode 600) and win over .env (config.py says when). A key is never logged
or returned — only whether it is set, its last 4 characters and where it comes from. Saving is allowed only from the
computer running the app (the keys bill its owner); it takes effect at once, without a restart."""

from __future__ import annotations

import logging
import re
import threading

from fastapi import APIRouter, HTTPException, Request

from .. import llm, llm_anthropic
from ..config import APP_KEY_FIELDS, APP_KEYS_FILE, KEY_NAMES, env_key, load_app_keys, reload_app_keys, save_app_keys, settings
from ..schemas import ApiKeys, ApiKeyStatus, ApiKeysUpdate
from .books import _is_local_client

log = logging.getLogger("ua.api.keys")

router = APIRouter()

MAX_KEY_LENGTH = 512
_KEY_CHARS = re.compile(r"[\x21-\x7e]+")  # printable ASCII, no spaces or line breaks
_lock = threading.Lock()


def _status(name: str, saved: dict[str, str]) -> ApiKeyStatus:
    """The key in use (with the agent route: the key that would be used), shown by its last 4 characters only."""
    from_app = name in settings.app_keys
    value = saved.get(name, "") if from_app else env_key(name)
    source = ("app" if from_app else "env") if value else None
    return ApiKeyStatus(set=bool(value), last4=value[-4:] if len(value) >= 12 else None, source=source)


def _keys() -> ApiKeys:
    saved = load_app_keys(settings.data_dir)
    missing = [p for p in settings.providers_in_use if not settings.api_key(p)]
    return ApiKeys(
        keys={name: _status(name, saved) for name in APP_KEY_FIELDS},
        provider=settings.llm_provider,
        agent_route=bool(settings.agent_dir),
        fake_llm=settings.fake_llm,
        llm_ready=settings.llm_ready,
        missing_keys=[] if settings.fake_llm or settings.agent_dir else [KEY_NAMES[p] for p in missing],
        file=str(settings.data_dir / APP_KEYS_FILE),
    )


@router.get("/api/settings/keys", response_model=ApiKeys)
def get_keys() -> ApiKeys:
    return _keys()


@router.put("/api/settings/keys", response_model=ApiKeys)
def put_keys(body: ApiKeysUpdate, request: Request) -> ApiKeys:
    """Save any of the three keys (an empty string removes the saved one) and/or the provider."""
    if not _is_local_client(request):
        raise HTTPException(status_code=403, detail="API keys can be changed only on the computer running the app.")
    cleaned: dict[str, str] = {}
    for name, value in body.keys.items():
        value = value.strip()
        # Never echo the value: the messages name the key only.
        if len(value) > MAX_KEY_LENGTH:
            raise HTTPException(status_code=422, detail=f"{name} is too long for an API key.")
        if value and not _KEY_CHARS.fullmatch(value):
            raise HTTPException(status_code=422, detail=f"{name} contains spaces, line breaks or other characters an API key doesn't have.")
        cleaned[name] = value
    with _lock:
        saved = load_app_keys(settings.data_dir)
        for name, value in cleaned.items():
            if value:
                saved[name] = value
            else:
                saved.pop(name, None)
        if body.provider:
            saved["UA_LLM_PROVIDER"] = body.provider
        save_app_keys(settings.data_dir, saved)
        reload_app_keys()
        # The SDK clients were built with the previous key: the next call builds them again.
        llm._client = None
        llm_anthropic._client = None
    log.info("API keys saved in the app: %s", ", ".join(sorted(saved)) or "none")
    return _keys()
