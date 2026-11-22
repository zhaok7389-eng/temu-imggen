from PIL import Image

from imggen.pipeline import compose_steps


def test_three_portrait_steps_fill_the_square_without_reference_tile(tmp_path):
    colors = ["red", "green", "blue"]
    files = []
    for i, color in enumerate(colors):
        image = Image.new("RGB", (100, 300), color)
        p = tmp_path / f"{i}.png"
        image.save(p)
        files.append(p)
    out = tmp_path / "steps.png"
    compose_steps(files, ["", "", ""], out, size=400)
    with Image.open(out) as result:
        assert result.size == (400, 400)
        for x, color in [(80, (255, 0, 0)), (200, (0, 128, 0)), (350, (0, 0, 255))]:
            assert result.getpixel((x, 0)) == color
            assert result.getpixel((x, 200)) == color
            assert result.getpixel((x, 399)) == color


def test_generation_requests_native_portraits(tmp_path):
    import io
    from types import SimpleNamespace
    from imggen.config import load_config
    from imggen.pipeline import App

    cfg = dict(load_config(), workspace=str(tmp_path / 'ws'), demo_mode=True)
    core = App(cfg)
    buf = io.BytesIO()
    Image.new('RGB', (100, 100), 'white').save(buf, 'PNG')
    sku = core.store.create_sku('Test', [('source.png', buf.getvalue())])
    core.confirm_label(sku, {'lines': [], 'allowed_claims': []})
    core.save_plan(sku, 'A', {'style': {}, 'slots': {'5': {'steps': [
        {'caption': '', 'action': 'apply'} for _ in range(3)]}}})
    requests = []

    def generate(req):
        requests.append(req)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (512, 1536), 'gray').save(req.out_path)

    core.check = lambda *args: {'flags': []}
    core._one_slot(SimpleNamespace(run=generate), sku, 'A', 5, '')
    assert len(requests) == 3
    assert all(r.size == '512x1536' for r in requests)
    assert all('Native 1:3' in r.prompt for r in requests)
    assert core.store.current(sku, 'A', 5) is not None
