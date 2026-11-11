"""Generation wrapper with a fake OpenAI client: output file, ledger, retry, budget."""
import base64, io
from types import SimpleNamespace
import pytest
from PIL import Image
from imggen.config import load_config
from imggen.gen.ledger import Ledger
from imggen.gen.generate import Generator, GenRequest, BudgetExceeded


def _png():
    b = io.BytesIO(); Image.new("RGB", (8, 8), "orange").save(b, "PNG"); return b.getvalue()


class FakeErr(Exception):
    def __init__(self, code): super().__init__("rate limited"); self.status_code = code


class FakeImages:
    def __init__(self, fail_first=0): self.calls, self.fail_first = 0, fail_first
    def edit(self, **kw):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise FakeErr(429)
        usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 1656, "output_tokens": 439,
                                "input_tokens_details": {"text_tokens": 135, "image_tokens": 1521}})
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_png()).decode())], usage=usage)


def _req(tmp_path):
    src = tmp_path / "p.png"; src.write_bytes(_png())
    return GenRequest("01", "A", 1, 1, "prompt", [src], tmp_path / "out" / "1_1.png")


def test_generate_writes_image_and_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    cfg = load_config(); led = Ledger(tmp_path / "ledger.csv")
    fake = SimpleNamespace(images=FakeImages(fail_first=2))
    out = Generator(cfg, led, client=fake).run(_req(tmp_path))
    assert out.exists() and fake.images.calls == 3
    # same token counts as the 11/11 playground test -> $0.026013
    assert abs(led.spent_today() - 0.026013) < 1e-6


def test_budget_guard(tmp_path):
    cfg = dict(load_config(), budget_usd_per_day=0.02); led = Ledger(tmp_path / "ledger.csv")
    g = Generator(cfg, led, client=SimpleNamespace(images=FakeImages()))
    g.run(_req(tmp_path))
    with pytest.raises(BudgetExceeded):
        g.run(_req(tmp_path))
