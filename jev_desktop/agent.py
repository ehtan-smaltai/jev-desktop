"""The agent loop: observe, one Jev decision, optional text, safety gate, input."""

import time

from .desktop import Desktop, StalePage
from .model import choose, field_context, field_text
from .questions import MAX_STEPS
from .safety import Stopped, StopSwitch, confirm, risk

SETTLE_S = 0.2  # Pause after input before the next observation.
MAX_STALE = 3  # Consecutive refused actions before the run stops, so a stuck check cannot burn API calls.


def describe(decision, text):
    action = decision["action"]
    line = f"{decision['operation']}: {action['label']}"
    return f'{line}  <- "{text}"' if text is not None else line


class Agent:
    def __init__(self, goal, *, confirm_mode="risky", max_steps=MAX_STEPS, log=print):
        if not goal.strip():
            raise ValueError("Supply a goal")
        self.goal = goal.strip()
        self.confirm_mode = confirm_mode
        self.max_steps = max_steps
        self.log = log
        self.stop = StopSwitch()
        self.desktop = Desktop()
        self.history = []
        self.stale = 0
        self.trace = {"goal": self.goal, "steps": [], "status": "running"}

    def step(self, execute=True):
        """One observe/decide/act cycle. Returns the status: running, done, blocked, declined."""
        self.stop.check()
        page = self.desktop.observe()
        if self.history and self.history[-1]["page_changed"] is None:
            self.history[-1]["page_changed"] = page["fingerprint"] != self.history[-1]["fingerprint"]
        decision = choose(page, self.goal, self.history)
        self.stop.check()
        action, operation = decision["action"], decision["operation"]
        record = {
            "step": len(self.history) + 1, "window": page["title"], "operation": operation,
            "action": action["label"], "kind": action["kind"], "confidence": decision["confidence"],
            "observe_ms": page["observe_ms"], "decide_ms": decision["latency_ms"],
            "elements": len(page["actions"]),
        }
        if operation in {"DONE", "BLOCKED"}:
            self.log(f"[{record['step']:>2}] {operation}  (observe {page['observe_ms']} ms, decide "
                     f"{decision['latency_ms']} ms)")
            self.trace["steps"].append(record)
            return operation.lower()
        text = None
        if action["kind"] == "fill":
            text, helper = field_text(field_context(self.goal, action, page, self.history))
            record.update(text=text, text_ms=helper["latency_ms"])
            self.stop.check()
        summary = describe(decision, text)
        self.log(f"[{record['step']:>2}] {summary}  (observe {page['observe_ms']} ms, decide "
                 f"{decision['latency_ms']} ms{', text ' + str(record['text_ms']) + ' ms' if text else ''})")
        if not execute:
            self.trace["steps"].append(record)
            return "dry-run"
        reason = risk(action, page, text)
        if not confirm(self.confirm_mode, reason, summary):
            self.log("     declined: stopping.")
            self.trace["steps"].append({**record, "declined": reason})
            return "declined"
        self.stop.check()
        started = time.perf_counter()
        try:
            self.desktop.act(action, text=text, stop=self.stop)
        except StalePage as error:
            self.stale += 1
            self.trace["steps"].append({**record, "stale": str(error)})
            if self.stale >= MAX_STALE:
                self.log(f"     not executed: {error} Giving up after {MAX_STALE} tries.")
                return "blocked"
            self.log(f"     not executed: {error} Observing again.")
            return "running"
        self.stale = 0
        record["act_ms"] = round((time.perf_counter() - started) * 1000)
        self.trace["steps"].append(record)
        self.history.append({"action": action["label"], "kind": action["kind"], "text": text,
                             "fingerprint": page["fingerprint"], "page_changed": None})
        time.sleep(1.0 if action["kind"] == "wait" else SETTLE_S)
        recent = self.history[-4:-1]
        if len(recent) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in recent):
            self.log("     three actions changed nothing: stopping.")
            return "blocked"
        return "running"

    def run(self):
        started = time.perf_counter()
        status = "running"
        try:
            while status == "running":
                if len(self.history) >= self.max_steps:
                    status = "blocked"
                    self.log(f"Reached the {self.max_steps}-step limit.")
                    break
                status = self.step()
        except Stopped as stop:
            status = "stopped"
            self.log(str(stop))
        except (RuntimeError, ValueError) as error:  # Model/provider failures: nothing was executed.
            status = "error"
            self.log(f"Error: {error}")
            self.trace["error"] = str(error)
        finally:
            self.stop.close()
        self.trace.update(status=status, total_ms=round((time.perf_counter() - started) * 1000))
        return status
