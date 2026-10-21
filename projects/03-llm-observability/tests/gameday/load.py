"""The game day's background load (gameday.sh starts and stops it): one chat-default request every 2 s
through the gateway, with a team key made in memory, so the SLOs and the fallback alert see steady traffic.
Open-loop: each request runs on its own thread, so a slow gateway still gets one every 2 s. One at a time, it
got one a minute while the hosted model was blocked (60 s timeout), too few errors for the availability alert.
One line per request, when it ends: unix time, status, latency, attempted fallbacks. Env: GATEWAY_URL,
MASTER_KEY, P2_TESTS, GAMEDAY_MAX_S (default 7200: it stops by itself even if gameday.sh dies without stopping it).
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.environ["P2_TESTS"])
from client import chat, new_team_key

# $1 caps what fallbacks to the hosted model can cost during the game day; a refused call is a 429, not an error.
key = new_team_key("gameday-load", budget_usd=1.0, rpm=100)


def one():
    status, _, headers, seconds = chat(key, "chat-default", "Reply with the single word: ok")
    print(f"{time.time():.0f} status={status} ms={seconds * 1000:.0f} "
          f"fallbacks={headers.get('x-litellm-attempted-fallbacks', 'absent')}", flush=True)


start = time.monotonic()
for n in range(int(float(os.environ.get("GAMEDAY_MAX_S", "7200")) / 2)):
    time.sleep(max(0.0, start + 2 * n - time.monotonic()))
    threading.Thread(target=one).start()
