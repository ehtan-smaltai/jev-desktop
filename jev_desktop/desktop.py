"""Windows desktop observation (one cached UI Automation query) and bounded input."""

import ctypes
import hashlib
import time
import warnings
from ctypes import wintypes

try:  # Physical pixels everywhere, so UIA rectangles and mouse coordinates agree.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except OSError:
    pass

import comtypes.client

from .redact import redact

UIA = comtypes.client.GetModule("UIAutomationCore.dll")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from pywinauto import keyboard, mouse

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
kernel32.GetConsoleWindow.restype = wintypes.HWND

GA_ROOT = 2
MAX_ELEMENTS = 200
MAX_TEXT = 6000
MAX_WINDOWS = 25

ROLES = {
    getattr(UIA, name): name[4:-len("ControlTypeId")]
    for name in dir(UIA)
    if name.startswith("UIA_") and name.endswith("ControlTypeId")
}
CLICKABLE = {
    "Button", "CheckBox", "ComboBox", "DataItem", "Hyperlink", "ListItem", "MenuItem",
    "RadioButton", "SplitButton", "TabItem", "TreeItem",
}
TEXT_ROLES = {"Text", "Header", "HeaderItem", "StatusBar", "TitleBar"}
KEYS = {
    "k1": ("Enter", "{ENTER}"),
    "k2": ("Escape", "{ESC}"),
    "k3": ("Tab", "{TAB}"),
    "k4": ("Shift+Tab", "+{TAB}"),
    "k5": ("Windows key (opens Start menu and its search box)", "{VK_LWIN}"),
    "k6": ("Arrow Down", "{DOWN}"),
    "k7": ("Arrow Up", "{UP}"),
}

P = UIA  # property ids
CACHED = [
    P.UIA_NamePropertyId, P.UIA_ControlTypePropertyId, P.UIA_BoundingRectanglePropertyId,
    P.UIA_IsEnabledPropertyId, P.UIA_IsPasswordPropertyId, P.UIA_AutomationIdPropertyId,
    P.UIA_HasKeyboardFocusPropertyId, P.UIA_IsValuePatternAvailablePropertyId, P.UIA_ValueValuePropertyId,
    P.UIA_ValueIsReadOnlyPropertyId, P.UIA_IsInvokePatternAvailablePropertyId,
    P.UIA_IsTogglePatternAvailablePropertyId, P.UIA_ToggleToggleStatePropertyId,
    P.UIA_IsSelectionItemPatternAvailablePropertyId, P.UIA_SelectionItemIsSelectedPropertyId,
    P.UIA_IsExpandCollapsePatternAvailablePropertyId, P.UIA_ExpandCollapseExpandCollapseStatePropertyId,
]


class StalePage(Exception):
    """The screen changed between observation and input. Nothing was executed."""


def window_title(hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def window_class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def cloaked(hwnd):
    value = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def root(hwnd):
    return user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None


def top_windows(excluded):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        title = window_title(hwnd)
        ex_style = user32.GetWindowLongW(hwnd, -20)
        if (
            user32.IsWindowVisible(hwnd)
            and title
            and hwnd not in excluded
            and not ex_style & 0x80  # WS_EX_TOOLWINDOW
            and not cloaked(hwnd)
            and title != "Program Manager"
        ):
            found.append({"hwnd": hwnd, "title": redact(title), "app": window_class(hwnd),
                          "minimized": bool(user32.IsIconic(hwnd))})
        return len(found) < MAX_WINDOWS

    user32.EnumWindows(visit, 0)
    return found


def rect_of(value):
    """UIA returns cached rectangles as (left, top, width, height) and current ones as RECT."""
    if hasattr(value, "left"):
        return (value.left, value.top, value.right, value.bottom)
    left, top, width, height = value
    return (round(left), round(top), round(left + width), round(top + height))


def centre(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def classify(role, props):
    """Return the operations an observed control supports: 'fill', 'click', or both."""
    kinds = []
    editable = props["value_pattern"] and not props["read_only"] and not props["password"]
    if editable and role in {"Edit", "ComboBox", "Document"}:
        kinds.append("fill")
    if role in CLICKABLE or (
        props["name"] and (props["invoke"] or props["toggle"] or props["selection"] or props["expand"])
    ):
        if role != "Edit":
            kinds.append("click")
    return kinds


class Desktop:
    def __init__(self, exclude=()):
        self.uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        self.cache = self.uia.CreateCacheRequest()
        for prop in CACHED:
            self.cache.AddProperty(prop)
        self.visible = self.uia.CreatePropertyCondition(P.UIA_IsOffscreenPropertyId, False)
        # Never observe or drive the agent's own console, nor the window it was launched from.
        self.launch_window = root(user32.GetForegroundWindow())
        self.excluded = {h for h in (kernel32.GetConsoleWindow(), self.launch_window, *exclude) if h}

    def observe(self):
        started = time.perf_counter()
        hwnd = root(user32.GetForegroundWindow())
        usable = hwnd and hwnd not in self.excluded
        actions, texts, focused = [], [], None
        if usable:
            window = self.uia.ElementFromHandle(hwnd)
            found = window.FindAllBuildCache(UIA.TreeScope_Descendants, self.visible, self.cache)
            for i in range(found.Length):
                node = found.GetElement(i)
                get = node.GetCachedPropertyValue
                role = ROLES.get(get(P.UIA_ControlTypePropertyId), "Custom")
                rect = rect_of(get(P.UIA_BoundingRectanglePropertyId))
                props = {
                    "name": (get(P.UIA_NamePropertyId) or "").strip(),
                    "value_pattern": bool(get(P.UIA_IsValuePatternAvailablePropertyId)),
                    "read_only": bool(get(P.UIA_ValueIsReadOnlyPropertyId)),
                    "password": bool(get(P.UIA_IsPasswordPropertyId)),
                    "invoke": bool(get(P.UIA_IsInvokePatternAvailablePropertyId)),
                    "toggle": bool(get(P.UIA_IsTogglePatternAvailablePropertyId)),
                    "selection": bool(get(P.UIA_IsSelectionItemPatternAvailablePropertyId)),
                    "expand": bool(get(P.UIA_IsExpandCollapsePatternAvailablePropertyId)),
                }
                value = get(P.UIA_ValueValuePropertyId) if props["value_pattern"] else ""
                value = value if isinstance(value, str) else ""
                if get(P.UIA_HasKeyboardFocusPropertyId):
                    focused = {"label": props["name"], "role": role,
                               "automation_id": get(P.UIA_AutomationIdPropertyId) or ""}
                if role in TEXT_ROLES and props["name"]:
                    texts.append(redact(props["name"]))
                elif role == "Document" and value:
                    texts.append(redact(value[:3000]))
                if not get(P.UIA_IsEnabledPropertyId) or rect[2] <= rect[0] or rect[3] <= rect[1]:
                    continue
                label = props["name"] or get(P.UIA_AutomationIdPropertyId) or ""
                for kind in classify(role, props):
                    if kind == "click" and not label:
                        continue
                    if len(actions) >= MAX_ELEMENTS:
                        break
                    action = {
                        "id": f"{kind}:{i}", "kind": kind, "node": i, "role": role,
                        "label": redact(label) or "text field", "name": props["name"], "rect": rect,
                        "value": redact(value[:200]), "window": hwnd, "element": node,
                    }
                    if props["toggle"]:
                        action["checked"] = get(P.UIA_ToggleToggleStatePropertyId) == 1
                    if props["selection"]:
                        action["selected"] = bool(get(P.UIA_SelectionItemIsSelectedPropertyId))
                    if props["expand"]:
                        action["expanded"] = get(P.UIA_ExpandCollapseExpandCollapseStatePropertyId) == 1
                    actions.append(action)
        windows = top_windows(self.excluded)
        for number, window in enumerate(windows, 1):
            window["id"] = f"w{number}"
            window["foreground"] = window["hwnd"] == hwnd
        text = "\n".join(dict.fromkeys(texts))[:MAX_TEXT]
        page = {
            "window": hwnd if usable else None,
            "title": redact(window_title(hwnd)) if usable else "(no usable foreground window)",
            "app": window_class(hwnd) if usable else "",
            "rect": self.window_rect(hwnd) if usable else None,
            "text": text,
            "focused": focused,
            "actions": actions,
            "windows": windows,
            "keys": {key: label for key, (label, _) in KEYS.items()},
        }
        page["fingerprint"] = hashlib.sha1(
            repr((page["window"], page["title"], [(a["kind"], a["label"], a["value"], a["rect"]) for a in actions],
                  [(w["title"], w["foreground"]) for w in windows])).encode()
        ).hexdigest()[:16]
        page["observe_ms"] = round((time.perf_counter() - started) * 1000)
        return page

    # ---- input -------------------------------------------------------------------------------

    def focus(self, hwnd):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if root(user32.GetForegroundWindow()) == hwnd:
            return
        current = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        mine = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(mine, current, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(mine, current, False)
        for _ in range(20):
            if root(user32.GetForegroundWindow()) == hwnd:
                return
            time.sleep(0.02)
        raise StalePage("Could not bring the target window to the front.")

    def check_target(self, action):
        """Re-read the observed control immediately before input (R-006)."""
        if not user32.IsWindow(action["window"]):
            raise StalePage("The target window closed.")
        element = action["element"]
        try:
            name = (element.CurrentName or "").strip()
            rect = rect_of(element.CurrentBoundingRectangle)
            enabled = element.CurrentIsEnabled
        except comtypes.COMError:
            raise StalePage("The target control no longer exists.") from None
        if name != action["name"] or not enabled:
            raise StalePage("The target control changed.")
        if any(abs(a - b) > 4 for a, b in zip(rect, action["rect"], strict=True)):
            raise StalePage("The target control moved.")
        x, y = centre(rect)
        if root(user32.WindowFromPoint(wintypes.POINT(x, y))) != action["window"] and not self.under_point(
            element, x, y
        ):
            raise StalePage("The target control is covered by another window.")
        return x, y

    def keyboard_focus(self, action, set_focus=True):
        """Give the observed field keyboard focus without a click; True only if UIA confirms it has focus.

        Typing needs focus, not a click. This also avoids overlays that sit on top of a field, such as the
        Windows 11 Start menu's search button.
        """
        element = action["element"]
        try:
            if (element.CurrentName or "").strip() != action["name"]:
                raise StalePage("The target field changed.")
            if set_focus:
                element.SetFocus()
            focused = self.uia.GetFocusedElement()
            return bool(focused and self.uia.CompareElements(focused, element))
        except comtypes.COMError:
            return False

    def under_point(self, element, x, y):
        """Is `element` (or a control inside it) what a click at (x, y) would hit?

        Shell surfaces such as the Start menu host controls in child windows of other processes, so the window
        test alone gives false alarms there; the UIA hit test is the ground truth.
        """
        try:
            hit = self.uia.ElementFromPoint(UIA.tagPOINT(x, y))
            walker = self.uia.ControlViewWalker
            for _ in range(8):
                if not hit:
                    return False
                if self.uia.CompareElements(hit, element):
                    return True
                hit = walker.GetParentElement(hit)
        except comtypes.COMError:
            return False
        return False

    def act(self, action, text=None, stop=None):
        kind = action["kind"]
        if kind == "window":
            self.focus(action["hwnd"])
        elif kind == "key":
            if action["window"]:
                self.focus(action["window"])
            keyboard.send_keys(KEYS[action["key"]][1], pause=0)
        elif kind == "click":
            self.focus(action["window"])
            mouse.click(coords=self.check_target(action))
        elif kind == "fill":
            self.focus(action["window"])
            if not self.keyboard_focus(action):
                # Fall back to a real click, which re-checks that nothing covers the field.
                mouse.click(coords=self.check_target(action))
                time.sleep(0.05)
                if not self.keyboard_focus(action, set_focus=False):
                    raise StalePage("Could not put the keyboard focus in the target field.")
            # Documents keep their content: append at the end. Single fields are replaced.
            keyboard.send_keys("^{END}" if action["role"] == "Document" else "^a{DEL}", pause=0)
            self.type(text, stop)
        elif kind in {"scroll_up", "scroll_down"}:
            self.focus(action["window"])
            left, top, right, bottom = action["rect"]
            mouse.scroll(coords=((left + right) // 2, (top + bottom) // 2),
                         wheel_dist=5 if kind == "scroll_up" else -5)
        elif kind == "wait":
            time.sleep(0.8)
        else:
            raise ValueError(f"Unknown action kind {kind}")

    @staticmethod
    def type(text, stop=None):
        # Chunks let the stop key interrupt long text between chunks.
        for start in range(0, len(text), 20):
            if stop:
                stop.check()
            keyboard.send_keys(escape_keys(text[start:start + 20]), pause=0.004, with_spaces=True, with_tabs=True)

    def window_rect(self, hwnd):
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)


def escape_keys(text):
    """Literal text for pywinauto.send_keys: escape its modifier/grouping syntax, newlines become Enter."""
    special = set("+^%~(){}[]")
    out = "".join("{" + ch + "}" if ch in special else ch for ch in text.replace("\r\n", "\n"))
    return out.replace("\n", "{ENTER}")
