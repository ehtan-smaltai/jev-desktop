# jev-desktop

**Tell your Windows PC what to do in plain language.** jev-desktop reads the controls on screen, lets
[TypeSafe's Jev](https://docs.typesafe.ai/introduction) pick the next action, and uses a small LLM only when
something has to be typed.

```text
> jevd "Open Notepad and type: hello from jev"
[ 1] PRESS_KEY: Press Windows key         (observe 3 ms, decide 395 ms)
[ 2] TYPE_TEXT: Search box  <- "Notepad"  (observe 18 ms, decide 267 ms, text 851 ms)
[ 3] PRESS_KEY: Press Enter               (observe 133 ms, decide 297 ms)
[ 4] WAIT
[ 5] TYPE_TEXT: Text editor <- "hello from jev"
[ 6] DONE
```

> **Experimental.** It moves your real mouse and keyboard. Read [Safety](#safety) before your first run.

## Quick start

1. Get two API keys:
   - **TypeSafe**: [docs.typesafe.ai](https://docs.typesafe.ai/introduction). Makes every decision.
   - **OpenRouter**: [openrouter.ai/keys](https://openrouter.ai/keys). Writes typed text; free models work.
2. Paste this into PowerShell:

   ```powershell
   irm https://raw.githubusercontent.com/ehtan-smaltai/jev-desktop/main/install.ps1 | iex
   ```

   It installs [uv](https://docs.astral.sh/uv/) if needed, installs the `jevd` command, and asks for your two
   keys. It checks the keys work and stores them in `%APPDATA%\jev-desktop\.env`.
3. Run a goal:

   ```powershell
   jevd "Open Notepad and type: hello from jev"
   ```

That's it. Run `jevd setup` again any time to change keys.

## Commands

```powershell
jevd "goal in plain language"     # run it
jevd --dry-run "Open Settings"    # show the first decision without acting
jevd --confirm all "..."          # ask before every action
jevd --observe --delay 3          # list what it can see in the window you switch to (no model calls)
jevd setup                        # set or change API keys
```

Each run saves a timing trace to `%APPDATA%\jev-desktop\runs\`.

## How it works

Every step:

1. **Observe.** One cached UI Automation query reads the foreground window's controls (buttons, fields, list
   items, tabs, checkboxes) with their names, values and states. It also lists the open windows and a few keys
   (Enter, Escape, Tab, the Windows key).
2. **Decide.** One TypeSafe request answers two questions at once: which operation (`CLICK`, `TYPE_TEXT`,
   `PRESS_KEY`, `FOCUS_WINDOW`, `SCROLL_UP/DOWN`, `WAIT`, `DONE`, `BLOCKED`) and which numbered target.
3. **Write text, only if needed.** For `TYPE_TEXT`, a small OpenAI-compatible model returns strict JSON
   `{"text": ...}`. `TEXT_MODEL` is a fallback list, so a rate-limited free model is skipped immediately.
4. **Act.** The target is re-read first. It must still exist with the same name and position, and a click must
   land on it. Otherwise nothing happens and it observes again.

Model output is only ever a choice from the offered list, or text to type. It never becomes coordinates,
commands or code.

## Safety

- **Stop:** `Ctrl+Alt+Q`, or move the mouse to the top-left corner of your main screen.
- **Asks first** (Yes/No dialog, default No) before: clicking anything labelled delete, send, submit, pay,
  install, sign out and similar; pressing Enter outside a search or address box; any input into a terminal;
  typing a line break; replacing text already in a field.
- **Never** types into password fields, and never drives its own terminal window.
- **Documents are appended to, never cleared.**
- **Your screen goes to the model providers.** The foreground window's text and control labels are sent to
  TypeSafe and, for typing steps, to the text model. API keys, tokens and `password=`-style values are redacted
  first, but everything else is sent. Close sensitive documents first. Free OpenRouter models may log prompts.
- Consider a separate Windows account or a VM for anything you care about.

## Limits

- Apps must expose UI Automation. Most Win32, WinUI, WPF, Office and Chromium apps do; games and canvas apps
  don't.
- No drag and drop, and no right-click menus yet.
- Windows running as administrator can't receive its input.
- `DONE` is the model's judgement; it is not independently verified.

## Development

```powershell
git clone https://github.com/ehtan-smaltai/jev-desktop
cd jev-desktop
uv sync
uv run pytest
uv run ruff check .
uv run jevd --observe
```

| File | Job |
| --- | --- |
| [agent.py](jev_desktop/agent.py) | The loop: observe, decide, text, safety gate, act |
| [desktop.py](jev_desktop/desktop.py) | UI Automation snapshot, re-validation, mouse and keyboard |
| [model.py](jev_desktop/model.py) | Operation/target questions, text helper with fallbacks |
| [safety.py](jev_desktop/safety.py) | Stop key, corner stop, risk rules, confirmation |
| [redact.py](jev_desktop/redact.py) | Secret redaction before any model call |
| [onboarding.py](jev_desktop/onboarding.py) | `jevd setup` |

Issues and pull requests welcome.

## Credits

Built on the design of [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT), which
applies the same idea to web pages. Parts of the model client and prompts are adapted from it.
