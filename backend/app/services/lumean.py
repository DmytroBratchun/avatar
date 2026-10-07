"""Client for the Lumean public API (text-to-speech through ElevenLabs and others).

Lumean works in orders: a text is sent with a template that holds the voice and its
settings, the service cuts it into chunks, voices them and joins the result. A chunk
can be voiced again on its own, with other wording, which is what the editor's
voice-over builds on (app/editor/voiceover.py).

Written from Lumean's API reference. Every answer is an envelope
{success, message, data}; refusals carry a machine-readable `reason`.
"""

import time
from pathlib import Path

import httpx

from app.services.runtime_settings import current_lumean_api_key

BASE_URL = "https://api.lumean.net/api/public"
# a request-rate refusal is waited out when the wait is at most this long
MAX_RATE_WAIT = 30
# rights the voice-over needs; the "automation" key preset lacks templates.write
NEEDED_RIGHTS = "orders.read, orders.write, orders.download, templates.read, templates.write, voices.read, billing.read"

# what to do about a refusal, by its reason
HINTS = {
    "no_subscription": "the Lumean account has no active subscription for this service",
    "entitlement_missing": "the Lumean subscription does not include this service",
    "insufficient_balance": "not enough balance on the Lumean account to pay for what the subscription doesn't cover",
    "insufficient_allowance": "the subscription's allowance is used up and the plan does not allow paying for more",
    "limit_exceeded": "the subscription's usage limit is reached",
    "concurrency_limit_exceeded": "too many orders are running at once - wait for one to finish",
    "content_blocked": "this text was rejected by the provider's content policy before - reword it",
    "policy_cooldown_active": "several texts in a row were rejected by the content policy - new orders are paused for a few minutes",
    "token_quota_exceeded": "the API key's token quota is used up",
    "template_model_outdated": "the voice settings name a model the service no longer has - pick the model again",
    "retry_config_outdated": "the order's settings no longer fit the service - generate the voice-over again",
    "text_over_capacity": "the text is too long for one fragment",
    "text_not_voiceable": "there is nothing to say in this text",
    "max_attempts_reached": "this fragment has used all its retries - generate the voice-over again",
    "stitch_in_progress": "the audio is being joined right now - try again in a moment",
    "active_retry_exists": "this fragment is already being voiced again",
}


class LumeanError(RuntimeError):
    """A refusal from Lumean, or no answer at all. `reason` is Lumean's machine code
    for it when there is one."""

    def __init__(self, message: str, status: int | None = None, reason: str | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.body = body or {}


def _explain(status: int, body: dict) -> str:
    reason = body.get("reason") or body.get("error_code")
    text = str(body.get("message") or "").strip()
    errors = body.get("errors")
    if isinstance(errors, dict):
        # validation: which fields, and what is wrong with each
        text = "; ".join(f"{field}: {' '.join(map(str, problems))}" for field, problems in errors.items()) or text
    if status == 401:
        hint = "the API key is missing, wrong or expired - set it in Settings"
    elif status == 403 and not reason:
        hint = f"the API key lacks a right this needs ({NEEDED_RIGHTS})"
    else:
        hint = HINTS.get(reason or "")
    if reason == "token_quota_exceeded" and body.get("window") == "quota":
        hint = "the API key's token pool is used up and does not refill by itself"
    if body.get("retry_at"):
        hint = f"{hint or 'try again'} (after {body['retry_at']})"
    parts = [f"Lumean {status}"] + [p for p in (hint, text) if p]
    return ": ".join(parts[:2]) + (f" [{parts[2]}]" if len(parts) > 2 else "")


class Lumean:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        key = api_key if api_key is not None else current_lumean_api_key()
        if not key:
            raise LumeanError("No Lumean API key - add it in Settings", status=401)
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-API-KEY": key, "Accept": "application/json", "Accept-Language": "en"},
            timeout=httpx.Timeout(60, connect=15),
            transport=transport,
        )
        # result files are fetched from signed links on another host, without the key
        self._files = httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=True, transport=transport)

    def _request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None):
        """The `data` of the answer. Waits out short request-rate refusals and retries
        reads the server stumbled on; everything else raises LumeanError."""
        for attempt in range(4):
            try:
                response = self._http.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                if method == "GET" and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise LumeanError(f"Lumean did not answer: {exc.__class__.__name__}: {exc}") from exc
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {"data": body}
            if response.status_code < 300:
                return body.get("data", body)
            reason = body.get("reason") or body.get("error_code")
            wait = body.get("retry_after") or response.headers.get("Retry-After")
            if response.status_code == 429 and reason == "rate_limit_exceeded" and attempt < 3:
                try:
                    seconds = float(wait or 5)
                except (TypeError, ValueError):
                    seconds = 5
                if seconds <= MAX_RATE_WAIT:
                    time.sleep(seconds)
                    continue
            if response.status_code >= 500 and method == "GET" and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise LumeanError(_explain(response.status_code, body), response.status_code, reason, body)
        raise LumeanError("Lumean kept refusing the request")

    # --- account ---

    def user(self) -> dict:
        return self._request("GET", "/user")

    def tokens_remaining(self) -> int | None:
        """Tokens left in the subscription this month, over all usable plans; None
        when there is no subscription to count."""
        data = self._request("GET", "/subscriptions")
        left = [
            item.get("tokens_remaining")
            for subscription in data.get("subscriptions", [])
            for item in subscription.get("items", [])
            if item.get("is_usable") and item.get("tokens_remaining") is not None
        ]
        return sum(left) if left else None

    # --- voices and templates ---

    def elevenlabs_voices(self, search: str = "", page: int = 0, page_size: int = 20, **filters) -> dict:
        """A page of ElevenLabs' public voice library: {voices, total, page, has_more}.
        Pages count from 0 here, unlike the rest of the API."""
        params = {"page": page, "page_size": page_size, **{k: v for k, v in filters.items() if v}}
        if search:
            params["search"] = search
        return self._request("GET", "/voices/elevenlabs/library", params=params)

    def config_options(self, service: str) -> dict:
        return self._request("GET", "/templates/config-options", params={"service": service})

    def templates(self, service: str, limit: int = 90) -> list[dict]:
        """The account's own templates for a service, newest first."""
        found: list[dict] = []
        page = 1
        while len(found) < limit:
            data = self._request("GET", "/templates", params={"page": page, "per_page": 30, "service": service})
            found += data.get("items", [])
            if page >= (data.get("last_page") or 1):
                break
            page += 1
        return found

    def create_template(self, service: str, name: str, config: dict) -> dict:
        return self._request("POST", "/templates", json={"service_key": service, "name": name[:255], "config": config})

    def update_template(self, template_id: str, service: str, name: str, config: dict) -> dict:
        return self._request(
            "PUT", f"/templates/{template_id}", json={"service_key": service, "name": name[:255], "config": config}
        )

    # --- orders ---

    def preview(self, template_id: str, text: str, wait_seconds: float = 60) -> dict:
        """How the text would be cut into chunks and what it would cost, without
        ordering anything (free). Long texts are worked out in the background, so
        the answer is polled for."""
        data = self._request("POST", "/orders/chunks/preview", json={"template_id": template_id, "input_text": text})
        deadline = time.monotonic() + wait_seconds
        while data.get("status") == "processing" and time.monotonic() < deadline:
            time.sleep(2)
            data = self._request("GET", f"/orders/chunks/preview/{data['preview_id']}")
        return data

    def create_order(self, template_id: str, text: str, name: str | None = None) -> dict:
        payload = {"template_id": template_id, "input_text": text}
        if name:
            payload["name"] = name[:120]
        return self._request("POST", "/orders", json=payload)

    def create_music_order(self, prompt: str, variants: int = 1, length_ms: int = 30_000, instrumental: bool = True) -> dict:
        """Orders `variants` (1-4) different tracks for one description. Billed per
        track whatever its length; they come back as separate files."""
        task = {"prompt": prompt, "n_variants": variants, "music_length_ms": length_ms, "force_instrumental": instrumental}
        return self._request("POST", "/orders", json={"task_type": "music", "task_data": task})

    def order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}")

    def order_items(self, order_id: str) -> list[dict]:
        return self._request("GET", f"/orders/{order_id}/items")

    def item_text(self, order_id: str, item_id: str) -> str:
        return (self._request("GET", f"/orders/{order_id}/items/{item_id}/text") or {}).get("text") or ""

    def retry_item(self, order_id: str, item_id: str, text: str | None = None) -> dict:
        """Voices a failed or policy-flagged chunk again, free within the length
        already paid for. A chunk the content policy rejected needs other wording."""
        return self._request("POST", f"/orders/{order_id}/items/{item_id}/retry", json={"text": text} if text else {})

    def regenerate_item(self, order_id: str, item_id: str, text: str) -> dict:
        """Voices a finished chunk again with this text. Paid, by the new length."""
        return self._request("POST", f"/orders/{order_id}/items/{item_id}/regenerate", json={"text": text})

    def retry_failed(self, order_id: str) -> dict:
        """Queues every technically failed chunk of the order again; the ones the
        content policy rejected come back under `skipped`."""
        return self._request("POST", f"/orders/{order_id}/items/retry-failed")

    # --- files ---

    def file_url(self, path: str) -> str:
        """A temporary link to a result file, by its path from the order."""
        return self._request("POST", "/storage/url", json={"path": path})["url"]

    def download(self, path: str, target: Path) -> None:
        url = self.file_url(path)
        partial = target.with_name(target.name + ".part")
        try:
            with self._files.stream("GET", url) as response:
                response.raise_for_status()
                with partial.open("wb") as out:
                    for block in response.iter_bytes():
                        out.write(block)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise LumeanError(f"Could not download {Path(path).name} from Lumean: {exc}") from exc
        partial.replace(target)
