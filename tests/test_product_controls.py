import io

from PIL import Image
from fastapi.testclient import TestClient

from app import create_app
from imggen.config import load_config
from imggen.store import write_json


def setup_product(tmp_path):
    cfg = load_config()
    cfg.update(demo_mode=True, workspace=str(tmp_path / "workspace"))
    app = create_app(cfg)
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(data, "PNG")
    sid = app.state.core.store.create_sku("Example", [("source.png", data.getvalue())])
    return app, TestClient(app), sid


def test_delete_is_reversible_and_does_not_reuse_product_identity(tmp_path):
    app, client, sid = setup_product(tmp_path)
    store = app.state.core.store
    source = store.sources(sid)[0]
    original = source.read_bytes()
    assert f'/sku/{sid}/delete' in client.get('/library').text
    response = client.post(f'/sku/{sid}/delete')
    assert response.status_code == 200 and '撤销删除' in response.text
    assert sid not in store.list_skus()
    assert source.read_bytes() == original
    assert '没有这个 SKU' in client.get(f'/sku/{sid}').text
    assert store.create_sku('Example', [('again.png', original)]) != sid
    assert client.post(f'/sku/{sid}/restore').status_code == 200
    assert sid in store.list_skus()


def test_active_job_prevents_deletion(tmp_path):
    app, client, sid = setup_product(tmp_path)
    app.state.core.jobs.jobs['active'] = {'sku': sid, 'state': 'running', 'started': 1}
    response = client.post(f'/sku/{sid}/delete', follow_redirects=False)
    assert response.status_code == 303
    assert not app.state.core.store.sku(sid).get('deleted_at')


def test_long_label_uses_collapsible_scroll_regions(tmp_path):
    app, client, sid = setup_product(tmp_path)
    draft = {'lines': [{'text': f'Line {i}', 'size': 'small'} for i in range(120)]}
    write_json(app.state.core.store.sku_dir(sid) / 'label_draft.json', draft)
    html = client.get(f'/sku/{sid}').text
    assert '包装文字 · 120 行' in html
    assert 'label-form-body' in html and 'label-lines-scroll' in html
    assert 'max-height:420px;max-height:min(46vh,420px);overflow-y:auto' in html
    assert 'max-height:280px;max-height:min(30vh,280px);overflow-y:auto' in html
    assert '<details class="label-section"><summary>商品信息、卖点与用法' in html
    assert '</details></div><div class="label-form-actions">' in html
    assert 'Line 119' in html


def test_saved_plan_rechecks_long_approved_copy_and_wraps_it(tmp_path):
    from imggen.copy_layout import compose_copy_panel, copy_font, wrap_copy
    from imggen.plan import validate

    app, client, sid = setup_product(tmp_path)
    core = app.state.core
    heading = "INKEY® COLLAGEN, RETINOL &HYALURONIC ACID"
    ingredient = "COLLAGEN, RETINOL &HYALURONIC ACID"
    label = {"product_name": heading, "ingredient_names": [ingredient]}
    core.confirm_label(sid, label)
    plan = {
        "variant": "A", "style": {},
        "slots": {
            "2": {"name": "卖点图", "copy_layout": "benefits", "headline": heading},
            "3": {"name": "成分图", "copy_layout": "ingredients", "ingredient_labels": [ingredient]},
        },
        "issues": [f"图位2：“{heading}” 超过 40 个字符",
                   f"图位3：“{ingredient}” 超过 28 个字符"],
    }
    write_json(core.store.vdir(sid, "A") / "plan.json", plan)
    page = client.get(f"/sku/{sid}").text
    assert "超过 40 个字符" not in page and "超过 28 个字符" not in page
    assert "文案校验已通过" in page
    assert validate(plan, label, []) == []
    font = copy_font(57)
    lines = wrap_copy(heading, font, 928)
    assert len(lines) > 1 and " ".join(lines) == heading
    assert all(font.getlength(line) <= 928 for line in lines)

    sample = tmp_path / "layout.png"
    Image.new("RGB", (1024, 1024), "#ead6c2").save(sample)
    compose_copy_panel(sample, heading, [ingredient])
    with Image.open(sample) as im:
        assert im.size == (1024, 1024)
        assert im.getpixel((0, 0)) == (234, 214, 194)
        assert im.getpixel((0, 1023)) != im.getpixel((0, 0))
