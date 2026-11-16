"""
One-click local start for demos.

  python start.py        (or double-click start.bat)

What it does, in order:
  1. Creates/updates the database (idempotent).
  2. Starts the Mini App server (uvicorn) in its own window.
  3. Starts a cloudflared quick tunnel, waits for the public https URL,
     and writes it into .env as MINI_APP_URL (so /app buttons work).
  4. Starts the bot in its own window (it reads the fresh MINI_APP_URL).
Close this window (or Ctrl+C) to stop everything.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ENV_FILE = HERE / ".env"
NEW_WINDOW = subprocess.CREATE_NEW_CONSOLE


def set_env_value(key, value):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    print("[1/4] Database")
    subprocess.run([sys.executable, "db.py"], cwd=HERE, check=True)

    print("[2/4] Mini App server (uvicorn) -> new window")
    uvicorn = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", "8000"], cwd=HERE, creationflags=NEW_WINDOW)

    print("[3/4] Tunnel (cloudflared) -> waiting for public URL ...")
    tunnel = subprocess.Popen(["cloudflared", "tunnel", "--url", "http://localhost:8000"],
                              cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = None
    for line in tunnel.stdout:
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            break
    if url is None:
        print("ERROR: cloudflared did not give a URL. Is it installed and online?")
        uvicorn.terminate()
        sys.exit(1)
    set_env_value("MINI_APP_URL", url)
    print(f"      Mini App URL: {url}  (written to .env)")

    print("[4/4] Bot -> new window")
    time.sleep(1)
    bot = subprocess.Popen([sys.executable, "bot.py"], cwd=HERE, creationflags=NEW_WINDOW)

    print("\nAll running. Open the bot in Telegram and send /start. Close this window or press Ctrl+C to stop everything.\n")
    try:
        for line in tunnel.stdout:       # keep draining cloudflared output; exits if the tunnel dies
            pass
    except KeyboardInterrupt:
        pass
    finally:
        for p in (bot, uvicorn, tunnel):
            p.terminate()
        print("Stopped.")


if __name__ == "__main__":
    main()
