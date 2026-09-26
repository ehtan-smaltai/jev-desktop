import pytest

from jev_desktop import model
from jev_desktop.cli import load_env
from jev_desktop.desktop import classify, escape_keys, rect_of
from jev_desktop.safety import Stopped, StopSwitch, confirm, risk


def page(**extra):
    base = {
        "window": 100, "title": "Untitled - Notepad", "app": "Notepad", "rect": (0, 0, 800, 600), "text": "",
        "focused": None, "fingerprint": "f",
        "actions": [
            {"id": "click:0", "kind": "click", "node": 0, "role": "Button", "label": "Save", "name": "Save",
             "value": "", "rect": (1, 1, 9, 9), "window": 100},
            {"id": "fill:1", "kind": "fill", "node": 1, "role": "Document", "label": "Text editor",
             "name": "Text editor", "value": "", "rect": (0, 20, 800, 600), "window": 100},
            {"id": "click:2", "kind": "click", "node": 2, "role": "ComboBox", "label": "Font", "name": "Font",
             "value": "Arial", "rect": (1, 1, 9, 9), "window": 100, "expanded": False},
            {"id": "fill:2", "kind": "fill", "node": 2, "role": "ComboBox", "label": "Font", "name": "Font",
             "value": "Arial", "rect": (1, 1, 9, 9), "window": 100},
        ],
        "windows": [
            {"id": "w1", "hwnd": 100, "title": "Untitled - Notepad", "app": "Notepad", "minimized": False,
             "foreground": True},
            {"id": "w2", "hwnd": 200, "title": "Downloads", "app": "CabinetWClass", "minimized": True,
             "foreground": False},
        ],
        "keys": {"k1": "Enter", "k5": "Windows key"},
    }
    base.update(extra)
    return base


def answer(choice, ids):
    probabilities = {i: (1.0 if i == choice else 0.0) for i in ids}
    return {"choice": choice, "probabilities": probabilities, "confidence": 0.9}


def test_action_space_indexes_each_control_once_and_groups_targets():
    elements, targets, controls = model.action_space(page())
    assert [e["index"] for e in elements] == ["1", "2", "3"]
    assert elements[2]["operations"] == ["CLICK", "TYPE_TEXT"]
    assert set(targets["CLICK"]) == {"1", "3"} and set(targets["TYPE_TEXT"]) == {"2", "3"}
    assert set(targets["FOCUS_WINDOW"]) == {"w2"}  # The foreground window is not a switch target.
    assert set(targets["PRESS_KEY"]) == {"k1", "k5"}
    assert {"WAIT", "SCROLL_UP", "SCROLL_DOWN"} <= set(controls)


def test_no_foreground_window_offers_no_scroll_and_no_element_targets():
    _, targets, controls = model.action_space(page(window=None, actions=[], rect=None))
    assert "CLICK" not in targets and "SCROLL_UP" not in controls
    assert "PRESS_KEY" in targets


def test_choose_maps_operation_and_target(monkeypatch):
    def fake(url, key, body):
        ops = body["questions"]["operation"]["criteria"]
        clicks = body["questions"]["type_text_target"]["criteria"]
        return {"answers": {"operation": answer("TYPE_TEXT", ops), "type_text_target": answer("2", clicks)},
                "model": "jev"}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", fake)
    decision = model.choose(page(), "type hi", [])
    assert decision["operation"] == "TYPE_TEXT" and decision["action"]["id"] == "fill:1"


def test_choose_rejects_invalid_response(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *a: {"answers": {"operation": {"choice": "CLICK"}}})
    with pytest.raises(ValueError, match="no action executed"):
        model.choose(page(), "goal", [])


def test_choose_rejects_target_outside_offered_set(monkeypatch):
    def fake(url, key, body):
        ops = body["questions"]["operation"]["criteria"]
        bad = {"choice": "99", "probabilities": {"99": 1.0}, "confidence": 1.0}
        return {"answers": {"operation": answer("CLICK", ops), "click_target": bad}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", fake)
    with pytest.raises(ValueError):
        model.choose(page(), "goal", [])


def test_text_helper_output_is_strict():
    assert model.parse_text('{"text": "hello"}') == "hello"
    assert model.parse_text(model.strip_fence('```json\n{"text": "hi"}\n```')) == "hi"
    for bad in ('{"text": null}', '{"text": "a", "extra": 1}', "hello", '{"text": ""}'):
        with pytest.raises(ValueError):
            model.parse_text(bad)


def test_password_fields_are_never_fill_targets():
    props = dict(name="Password", value_pattern=True, read_only=False, password=True, invoke=False, toggle=False,
                 selection=False, expand=False)
    assert "fill" not in classify("Edit", props)
    assert classify("Edit", {**props, "password": False}) == ["fill"]


def test_risk_rules():
    p = page()
    save, editor = p["actions"][0], p["actions"][1]
    delete = {**save, "label": "Delete"}
    enter = {"kind": "key", "key": "k1", "label": "Press Enter", "window": 100}
    assert risk(delete, p) and risk({**save, "label": "Send now"}, p)
    assert risk(save, p) is None
    assert risk(editor, p, "hello") is None
    assert risk(editor, p, "line1\nline2")
    assert risk(enter, p)  # Enter in a document can submit or run things.
    assert risk(enter, page(focused={"label": "Search box", "automation_id": "SearchTextBox"})) is None
    assert risk(editor, page(app="CASCADIA_HOSTING_WINDOW_CLASS", title="Windows PowerShell"), "dir")
    assert risk({"kind": "window", "label": "Switch"}, p) is None


def test_confirm_modes(monkeypatch):
    asked = []
    monkeypatch.setattr("jev_desktop.safety.user32.MessageBoxW", lambda *a: asked.append(a) or 7)  # IDNO
    assert confirm("risky", None, "x") is True and not asked
    assert confirm("risky", "clicking Delete", "x") is False and len(asked) == 1
    assert confirm("all", None, "x") is False and len(asked) == 2
    assert confirm("none", "anything", "x") is True and len(asked) == 2


def test_stop_switch_raises_once_triggered():
    switch = StopSwitch()
    try:
        switch.trigger("test")
        with pytest.raises(Stopped, match="test"):
            switch.check()
    finally:
        switch.close()


def test_escape_keys_types_literal_text():
    assert escape_keys("a+b (c) {d} ^%~") == "a{+}b {(}c{)} {{}d{}} {^}{%}{~}"
    assert escape_keys("one\r\ntwo") == "one{ENTER}two"


def test_rect_of_handles_cached_and_current_forms():
    class Rect:
        left, top, right, bottom = 1, 2, 3, 4

    assert rect_of((10.0, 20.0, 5.0, 5.0)) == (10, 20, 15, 25)
    assert rect_of(Rect()) == (1, 2, 3, 4)


def test_load_env_keeps_existing_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("﻿A_KEY=from_file\n# comment\nB_KEY=\nC_KEY=x=y\n", encoding="utf-8")
    monkeypatch.setenv("A_KEY", "existing")
    monkeypatch.delenv("B_KEY", raising=False)
    monkeypatch.delenv("C_KEY", raising=False)
    assert load_env(env)
    import os

    assert os.environ["A_KEY"] == "existing" and "B_KEY" not in os.environ and os.environ["C_KEY"] == "x=y"
    assert not load_env(tmp_path / "missing")


def test_text_helper_falls_back_past_rate_limited_models(monkeypatch):
    calls = []

    def fake(url, key, body, attempts=3):
        calls.append((body["model"], attempts))
        if body["model"] == "busy":
            raise model.ProviderError(429)
        return {"choices": [{"message": {"content": '{"text": "Notepad"}'}}]}

    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "busy, ok")
    monkeypatch.setattr(model, "post_json", fake)
    text, helper = model.field_text({})
    assert text == "Notepad" and helper["model"] == "ok" and helper["failed"] == ["busy: HTTP 429"]
    assert calls == [("busy", 1), ("ok", 3)]


def test_text_helper_reports_when_every_model_fails(monkeypatch):
    def fake(url, key, body, attempts=3):
        raise model.ProviderError(429)

    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL", "a,b")
    monkeypatch.setattr(model, "post_json", fake)
    with pytest.raises(RuntimeError, match="nothing typed"):
        model.field_text({})


def test_redact_removes_keys_before_they_reach_a_model():
    from jev_desktop.redact import redact

    screen = (
        "TYPESAFE_API_KEY=apikey_0000fake0000fake0000fake0000\n"
        "Open Router: sk-or-v1-fake0000fake0000fake0000fake0000fake\n"
        "password: hunter2\n"
        "token abcdef0123456789abcdef0123456789abcdef\n"
        "normal words stay, like Notepad and 2026."
    )
    clean = redact(screen)
    for secret in ("apikey_0000fake", "sk-or-v1-fake", "hunter2", "abcdef0123456789abcdef"):
        assert secret not in clean
    assert "normal words stay, like Notepad and 2026." in clean
    assert clean.startswith("TYPESAFE_API_KEY=[REDACTED]")


def test_replacing_existing_field_text_needs_confirmation_but_documents_append():
    p = page()
    field = {"kind": "fill", "role": "Edit", "label": "File name", "value": "report.docx"}
    assert risk(field, p, "new.docx")
    assert risk({**field, "value": ""}, p, "new.docx") is None
    assert risk({**field, "label": "Search box"}, p, "notepad") is None
    assert risk({**field, "role": "Document", "value": "existing text"}, p, "more") is None


def test_setup_checks_keys_and_writes_config(tmp_path, monkeypatch):
    from jev_desktop import onboarding

    answers = iter(["bad-typesafe", "good-typesafe", "good-openrouter"])
    monkeypatch.setitem(onboarding.CHECKS, "TYPESAFE_API_KEY", lambda k: None if k.startswith("good") else "HTTP 401")
    monkeypatch.setitem(onboarding.CHECKS, "TEXT_MODEL_API_KEY", lambda k: None)
    said = []
    path = onboarding.run_setup(tmp_path / "jev" / ".env", ask=lambda prompt: next(answers), say=said.append)
    values = onboarding.read_env(path)
    assert values["TYPESAFE_API_KEY"] == "good-typesafe" and values["TEXT_MODEL_API_KEY"] == "good-openrouter"
    assert "," in values["TEXT_MODEL"] and not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert any("did not work" in line for line in said)
    assert not any("good-" in line or "bad-" in line for line in said)  # Keys are never echoed.

    # Running again with Enter keeps the stored keys.
    onboarding.run_setup(path, ask=lambda prompt: "", say=said.append)
    assert onboarding.read_env(path)["TYPESAFE_API_KEY"] == "good-typesafe"
