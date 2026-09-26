"""TypeSafe Jev makes choices; a small OpenAI-compatible model writes field text.

post_json, validate_choice and the text helper are adapted from browser-use/jev-ultrafast (MIT).
"""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"


class ProviderError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"Model provider returned HTTP {status}; no action executed.")
        self.status = status


def post_json(url, key, body, attempts=3):
    for attempt in range(attempts):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < attempts - 1:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise ProviderError(response.status_code)
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(page):
    """Indexed elements plus, per operation, the targets that operation may use."""
    elements, indices, targets = [], {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT"}
    for action in page["actions"]:
        node = action["node"]
        if node not in indices:
            indices[node] = str(len(elements) + 1)
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=indices[node], label=action["label"], operations=[])
            elements.append(element)
        index = indices[node]
        operation = operations[action["kind"]]
        elements[int(index) - 1]["operations"].append(operation)
        targets.setdefault(operation, {})[index] = action
    windows = {
        w["id"]: {"kind": "window", "id": w["id"], "hwnd": w["hwnd"], "label": f"Switch to window '{w['title']}'",
                  "title": w["title"], "app": w["app"], "minimized": w["minimized"], "foreground": w["foreground"]}
        for w in page["windows"] if not w["foreground"]
    }
    if windows:
        targets["FOCUS_WINDOW"] = windows
    targets["PRESS_KEY"] = {
        key: {"kind": "key", "id": key, "key": key, "label": f"Press {label}", "window": page["window"]}
        for key, label in page["keys"].items()
    }
    controls = {"WAIT": {"kind": "wait", "id": "WAIT", "label": "Wait for the app to finish loading or opening."}}
    if page["window"]:
        for direction in ("up", "down"):
            controls[f"SCROLL_{direction.upper()}"] = {
                "kind": f"scroll_{direction}", "id": f"SCROLL_{direction.upper()}", "window": page["window"],
                "rect": page.get("rect") or (0, 0, 0, 0), "label": f"Scroll the current window {direction}.",
            }
    return elements, targets, controls


LABELS = {
    "CLICK": "Click a button, menu item, list item, tab, link, checkbox, or other control in the current window.",
    "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
    "FOCUS_WINDOW": "Bring another open window to the front.",
    "PRESS_KEY": "Press a key such as Enter, Escape, Tab, or the Windows key (opens Start with its search box).",
}


def describe(target):
    keep = ("role", "value", "checked", "selected", "expanded", "app", "minimized")
    return {"element": target["label"], **{k: target[k] for k in keep if k in target and target[k] not in ("", None)}}


def choose(page, goal, history):
    elements, targets, controls = action_space(page)
    operations = {key: LABELS[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {index: describe(target) for index, target in candidates.items()},
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "window": {"title": page["title"], "app": page["app"], "text": page["text"],
                       "focused_control": page.get("focused")},
            "elements": elements,
            "open_windows": [{"id": w["id"], "title": w["title"], "foreground": w["foreground"]}
                             for w in page["windows"]],
            "recent_actions": [{k: h.get(k) for k in ("action", "kind", "text", "page_changed")}
                               for h in history[-10:]],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(TYPESAFE_URL, os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target_answer, target = None, None
    if operation in targets:
        # Unused target heads cannot cause an action. Validate only the head the operation selects.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        action = targets[operation][target]
    elif operation in controls:
        action = controls[operation]
    else:
        action = {"kind": operation.lower(), "id": operation, "label": operation}
    return {
        "operation": operation,
        "target": target,
        "action": action,
        "confidence": operation_answer["confidence"],
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "model": result.get("model"),
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {"label": action.get("label"), "role": action.get("role"), "current_value": action.get("value")},
        "window": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def parse_text(content):
    """The helper must return exactly {"text": "<non-empty string>"}; anything else types nothing."""
    try:
        output = json.loads(content)
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value


DEFAULT_TEXT_MODELS = "nvidia/nemotron-3-super-120b-a12b:free,dots-studio/dots-3-note-preview:free"


def field_text(context):
    """Ask each configured text model in turn; a rate-limited or failing model falls through to the next."""
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; nothing typed.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    models = [m.strip() for m in os.environ.get("TEXT_MODEL", DEFAULT_TEXT_MODELS).split(",") if m.strip()]
    extra = {"reasoning": {"enabled": False}} if os.environ.get("TEXT_MODEL_REASONING") == "none" else {}
    started = time.perf_counter()
    failures = []
    for number, model in enumerate(models, 1):
        body = {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **extra,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {"role": "user", "content": json.dumps(context)},
            ],
        }
        try:
            # With fallbacks left, skip the backoff retries: the next model is faster than waiting.
            result = post_json(base + "/chat/completions", key, body, attempts=1 if number < len(models) else 3)
            content = result["choices"][0]["message"]["content"]
        except ProviderError as error:
            failures.append(f"{model}: HTTP {error.status}")
            continue
        except (KeyError, IndexError, TypeError):
            failures.append(f"{model}: no message")
            continue
        return parse_text(strip_fence(content)), {
            "model": model, "latency_ms": round((time.perf_counter() - started) * 1000), "failed": failures
        }
    raise RuntimeError("No text model answered (" + "; ".join(failures) + "); nothing typed.")


def strip_fence(content):
    """Some free models wrap JSON in a ```json fence despite JSON mode."""
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    return content
