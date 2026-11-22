from types import SimpleNamespace

from imggen.llm import list_text_models, pick_text_model


def client_with_models(*names):
    return SimpleNamespace(models=SimpleNamespace(list=lambda: [
        SimpleNamespace(id=name, created=i) for i, name in enumerate(names)
    ]))


def test_auto_avoids_specialist_models_and_prefers_vision_chat():
    client = client_with_models("gpt-4.1", "gpt-5", "gpt-live-1.1", "gpt-3.5-turbo-instruct", "gpt-image-2")
    assert list_text_models(client) == ["gpt-5", "gpt-4.1"]
    assert pick_text_model({"text_model": "auto"}, client) == "gpt-4.1"


def test_explicit_model_is_preserved():
    assert pick_text_model({"text_model": "custom-model"}, client_with_models()) == "custom-model"
