"""Image generation via OpenAI images/edits, with retries, budget guard and ledger.

The API key is read by the OpenAI SDK from OPENAI_API_KEY; it is never logged.
"""
from __future__ import annotations
import base64, time
from dataclasses import dataclass
from pathlib import Path
from ..credentials import OPENAI_BASE_URL, safe_error
from .ledger import Ledger, cost_usd


class BudgetExceeded(RuntimeError):
    pass


class Blocked(RuntimeError):
    """Content moderation refusal: do not retry the same prompt."""


@dataclass
class GenRequest:
    sku_id: str
    variant: str
    slot: int
    attempt: int
    prompt: str
    images: list[Path]            # product photo first, optional style reference after
    out_path: Path
    model: str = "gpt-image-2.5-flare"
    quality: str = "medium"
    size: str = "1024x1024"


def _usage(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    u = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    det = u.get("input_tokens_details") or {}
    return {"text_in": det.get("text_tokens", 0), "image_in": det.get("image_tokens", 0),
            "image_out": u.get("output_tokens", 0)}


class Generator:
    def __init__(self, cfg: dict, ledger: Ledger, client=None, gate=None, priority: bool = False):
        self.cfg, self.ledger = cfg, ledger
        self.gate, self.priority = gate, priority
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=OPENAI_BASE_URL)  # reads OPENAI_API_KEY from the environment
        self.client = client

    def _check_budget(self):
        if self.ledger.spent_today() >= float(self.cfg["budget_usd_per_day"]):
            raise BudgetExceeded("daily budget reached: %s" % self.cfg["budget_usd_per_day"])

    def run(self, req: GenRequest, max_retries: int = 4) -> Path:
        self._check_budget()
        prices = self.cfg["pricing_usd_per_million_tokens"]
        base = dict(sku_id=req.sku_id, variant=req.variant, slot=req.slot, attempt=req.attempt,
                    model=req.model, quality=req.quality)
        delay = 1.0
        for i in range(max_retries + 1):
            if self.gate is not None:
                self.gate.acquire(self.priority)
            t = time.time()
            files = [open(p, "rb") for p in req.images]
            try:
                resp = self.client.images.edit(model=req.model, image=files, prompt=req.prompt,
                                               size=req.size, quality=req.quality, n=1)
            except Exception as e:  # noqa: BLE001 - classify below
                msg = safe_error(e)
                status = getattr(e, "status_code", None)
                blocked = "moderation" in msg.lower() or "safety" in msg.lower()
                self.ledger.add(**base, latency_s=round(time.time() - t, 1),
                                status="blocked" if blocked else f"error_{status}", error=msg[:300])
                if blocked:
                    raise Blocked(msg) from e
                if status == 429 and self.gate is not None:
                    self.gate.cool_down(delay * 2)
                if status in (429, 500, 502, 503, 504) or status is None:
                    if i < max_retries:
                        time.sleep(delay); delay = min(delay * 2, 8)
                        continue
                raise
            finally:
                for f in files:
                    f.close()
            usage = _usage(resp)
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            req.out_path.write_bytes(base64.b64decode(resp.data[0].b64_json))
            self.ledger.add(**base, **usage, cost_usd=cost_usd(usage, prices),
                            latency_s=round(time.time() - t, 1), status="ok")
            return req.out_path
        raise RuntimeError("unreachable")
