"""
start.py — runs FastAPI (uvicorn) + Discord bot in one process.
This way everything fits on Render's free web service tier.
"""
import os
import threading
import uvicorn
from main import app
from bot import bot, DISCORD_TOKEN

def run_api():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    # API runs in background thread
    t = threading.Thread(target=run_api, daemon=True)
    t.start()
    print("[Aethris] API thread started")

    # Bot runs in main thread
    print("[Aethris] Starting Discord bot...")
    bot.run(DISCORD_TOKEN)
