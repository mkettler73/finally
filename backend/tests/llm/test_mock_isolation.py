"""Proof that mock mode cannot reach the network.

An in-process assertion is weak here: another test may already have imported
`litellm`, and a `sys.modules` check would then pass for the wrong reason. So
this runs a clean interpreter, drives a full mock-mode chat turn, and asserts
that no HTTP stack was ever loaded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import json, sys, types

# Minimal stand-ins for the modules owned by the other backend agents.
db = types.ModuleType("app.db")
db.get_cash_balance = lambda user_id="default": 10000.0
db.list_positions = lambda user_id="default": []
db.list_watchlist = lambda user_id="default": []
db.list_snapshots = lambda limit=500, user_id="default": []
sys.modules["app.db"] = db

import os
os.environ["LLM_MOCK"] = "true"
os.environ.pop("OPENROUTER_API_KEY", None)

from app.llm import build_chat_client, build_context
from app.market import PriceCache

client = build_chat_client()
response = client.complete(
    user_message="Buy 5 NVDA",
    context=build_context(PriceCache()),
)

print(json.dumps({
    "type": type(client).__name__,
    "message": response.message,
    "trades": [t.model_dump() for t in response.trades],
    "litellm_loaded": "litellm" in sys.modules,
    "httpx_loaded": "httpx" in sys.modules,
}))
"""


def test_mock_mode_loads_no_http_stack_and_still_trades():
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr

    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["type"] == "MockChatClient"
    assert result["message"] == "Buying 5 NVDA at the market price now."
    assert result["trades"] == [{"ticker": "NVDA", "side": "buy", "quantity": 5.0}]
    assert result["litellm_loaded"] is False
    assert result["httpx_loaded"] is False
