import os
import re

from fastapi.testclient import TestClient

import app as web
from imggen.config import load_config


def test_api_key_settings_validate_before_save_and_do_not_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(web, "keychain_available", lambda: True)
    checked, saved = [], []

    def validate(key):
        checked.append(key)
        if key == "not-valid":
            raise ValueError("密钥无效")

    monkeypatch.setattr(web, "validate_api_key", validate)
    monkeypatch.setattr(web, "save_keychain_key", saved.append)
    cfg = load_config()
    cfg.update(workspace=str(tmp_path), demo_mode=False)
    app = web.create_app(cfg)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    page = client.get("/settings").text
    token = re.search(r'name="token" value="([^"]+)"', page)[1]
    assert 'type="password"' in page
    target = "/settings/openai-key"
    key = "sk-test-no-real-credential"
    origin = {"Origin": "http://127.0.0.1:8765"}
    assert client.post(target, data={"key": key, "token": token},
                       headers={"Origin": "https://example.com"}).status_code == 403
    assert client.post(target, data={"key": key, "token": "wrong"}, headers=origin).status_code == 403
    assert not checked and not saved
    assert "密钥无效" in client.post(target, data={"key": "not-valid", "token": token}, headers=origin).text
    assert not saved
    result = client.post(target, data={"key": key, "token": token}, headers=origin)
    assert "API 密钥已验证并保存" in result.text
    assert saved == [key] and os.environ.get("OPENAI_API_KEY") == key
    assert key not in result.text and key not in str(result.url)
