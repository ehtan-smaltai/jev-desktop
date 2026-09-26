"""jevd: run a goal on the Windows desktop, or inspect what the agent can see."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def load_env(path):
    """KEY=value lines; existing environment variables win. Values are never printed."""
    path = Path(path)
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())
    return True


def interactive():
    """True only for a real console. On Windows, isatty() is also True for the NUL device."""
    try:
        import ctypes
        import msvcrt

        mode = ctypes.c_uint32()
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


def print_observation(page):
    print(f"Window: {page['title']}  [{page['app']}]  observed in {page['observe_ms']} ms")
    for action in page["actions"]:
        state = "".join(f" {k}={action[k]}" for k in ("checked", "selected", "expanded") if k in action)
        value = f"  = {action['value'][:40]!r}" if action["value"] else ""
        print(f"  {action['kind']:<5} {action['role']:<12} {action['label'][:60]}{value}{state}")
    print("Windows:")
    for window in page["windows"]:
        print(f"  {window['id']:<4} {'*' if window['foreground'] else ' '} {window['title'][:70]}")
    print("Keys: " + ", ".join(page["keys"].values()))
    if page["focused"]:
        print(f"Focused: {page['focused']}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if argv[:1] == ["setup"]:
        from .onboarding import run_setup

        run_setup()
        return 0
    parser = argparse.ArgumentParser(prog="jevd", description=__doc__, epilog="First time? Run: jevd setup")
    parser.add_argument("goal", nargs="?", help="What to do, in plain language.")
    parser.add_argument("--observe", action="store_true", help="Print what the agent sees and exit. No model calls.")
    parser.add_argument("--dry-run", action="store_true", help="Observe and decide one step, but do not act.")
    parser.add_argument("--confirm", choices=["risky", "all", "none"], default="risky",
                        help="Which actions need your Yes first (default: risky).")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--delay", type=float, default=0,
                        help="Seconds to wait before starting, so you can bring a window to the front.")
    parser.add_argument("--env-file", help="Key file (default: ./.env if present, else the one `jevd setup` wrote).")
    args = parser.parse_args(argv)
    from .onboarding import config_path, run_setup

    env_file = args.env_file or (".env" if Path(".env").is_file() else config_path())
    load_env(env_file)
    if args.delay:
        time.sleep(args.delay)

    if args.observe:
        from .desktop import Desktop

        desktop = Desktop(exclude=())
        desktop.excluded -= {desktop.launch_window}
        print_observation(desktop.observe())
        return 0
    if not args.goal:
        parser.error("give a goal, or use --observe")
    if not os.environ.get("TYPESAFE_API_KEY"):
        if not interactive():
            parser.error(f"TYPESAFE_API_KEY is not set (looked in {env_file}). Run: jevd setup")
        print("No API keys yet. Let's set them up first.\n")
        load_env(run_setup())

    from .agent import Agent
    from .safety import HOTKEY

    agent = Agent(args.goal, confirm_mode=args.confirm, max_steps=args.max_steps)
    if not agent.stop.hotkey:
        print(f"Warning: could not register {HOTKEY}; use the top-left corner to stop.", file=sys.stderr)
    print(f"Goal: {agent.goal}\nStop any time: {HOTKEY}, or move the mouse to the top-left corner.")
    if args.dry_run:
        status = agent.step(execute=False)
        agent.stop.close()
    else:
        status = agent.run()
    runs = config_path().parent / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    trace = runs / time.strftime("%Y%m%d-%H%M%S.json")
    trace.write_text(json.dumps(agent.trace, indent=2, default=str), encoding="utf-8")
    print(f"Status: {status}. Trace: {trace}")
    return 0 if status in {"done", "dry-run"} else 1


if __name__ == "__main__":
    sys.exit(main())
