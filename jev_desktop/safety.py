"""Stop switch (Ctrl+Alt+Q or cursor in the top-left corner) and confirmation before risky actions."""

import ctypes
import re
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY, WM_QUIT = 0x1, 0x2, 0x4000, 0x0312, 0x0012
HOTKEY = "Ctrl+Alt+Q"

RISKY_WORDS = re.compile(
    r"\b(delete|remove|erase|discard|don'?t save|send|submit|post|publish|share|forward|reply|pay|buy|"
    r"purchase|order|checkout|check out|install|uninstall|format|shut ?down|restart|reboot|sign ?out|"
    r"log ?out|empty|overwrite|replace all|reset|permanently|transfer|confirm|accept|agree|allow|grant|"
    r"yes|run|recycle|call|approve|end task|kill)\b",
    re.IGNORECASE,
)
SAFE_ENTER = re.compile(r"search|address|find|filter|query|go to|url|omnibox", re.IGNORECASE)
TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "PuTTY", "VirtualConsoleClass"}
TERMINAL_TITLES = re.compile(r"powershell|command prompt|cmd\.exe|terminal|bash|wsl", re.IGNORECASE)


class Stopped(Exception):
    """The user pressed the stop key or moved the cursor to the corner."""


class StopSwitch:
    def __init__(self):
        self.event = threading.Event()
        self.reason = None
        self.registered = threading.Event()
        self.thread_id = None
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        self.registered.wait(1)

    def _listen(self):
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.hotkey = bool(user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("Q")))
        self.registered.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                self.trigger(f"{HOTKEY} pressed")
        if self.hotkey:
            user32.UnregisterHotKey(None, 1)

    def trigger(self, reason):
        if not self.event.is_set():
            self.reason = reason
            self.event.set()

    def check(self):
        point = wintypes.POINT()
        # The primary monitor's corner is (0, 0); other monitors can have negative coordinates.
        if user32.GetCursorPos(ctypes.byref(point)) and 0 <= point.x <= 2 and 0 <= point.y <= 2:
            self.trigger(f"cursor moved to the top-left corner ({point.x}, {point.y})")
        if self.event.is_set():
            raise Stopped(f"Stopped: {self.reason}.")

    def close(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


def is_terminal(app, title):
    return app in TERMINAL_CLASSES or bool(TERMINAL_TITLES.search(title or ""))


def risk(action, page, text=None):
    """Why an action needs confirmation, or None. Pure function of the observed state (R-005)."""
    kind = action["kind"]
    if kind in {"wait", "scroll_up", "scroll_down"}:
        return None
    if kind == "window":
        return None
    if is_terminal(page["app"], page["title"]):
        return "input to a terminal window"
    if kind == "key":
        if action["key"] != "k1":
            return None
        focused = page.get("focused") or {}
        where = " ".join(str(focused.get(k, "")) for k in ("label", "automation_id"))
        return None if SAFE_ENTER.search(where) else f"Enter in '{focused.get('label') or page['title']}'"
    if text is not None and ("\n" in text or "\r" in text):
        return "text contains a line break (can submit)"
    if kind == "fill" and action.get("value") and action.get("role") != "Document":
        if not SAFE_ENTER.search(action.get("label", "")):
            return f"replaces the existing text in '{action['label']}'"
    match = RISKY_WORDS.search(action.get("label", ""))
    if kind == "click" and match:
        return f"clicking '{action['label']}'"
    return None


def confirm(mode, reason, description):
    """Ask with a topmost Yes/No dialog (default No). mode: 'risky', 'all', or 'none'."""
    if mode == "none" or (mode == "risky" and not reason):
        return True
    body = f"jev-desktop wants to:\n\n{description}\n\nReason for asking: {reason or 'confirm every action'}"
    # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
    flags = 0x4 | 0x30 | 0x100 | 0x1000 | 0x10000 | 0x40000
    return user32.MessageBoxW(None, body, "jev-desktop: allow this action?", flags) == 6  # IDYES
