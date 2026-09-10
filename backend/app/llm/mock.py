"""Deterministic mock LLM, used when `LLM_MOCK=true`.

This module contains no network client and imports nothing that can reach the
internet - that is the whole point. `MockChatClient` is a drop-in replacement
for `LiveChatClient`, selected in `client.build_chat_client()`, so mock mode
cannot fall through to a real call even if an API key happens to be present.

The exact strings produced here are a contract with the Integration Tester and
are documented verbatim in `planning/LLM_NOTES.md`. Changing one is a breaking
change to the E2E suite.
"""

from __future__ import annotations

import re

from .context import PortfolioContext
from .models import ChatResponse, ChatTrade, ChatWatchlistChange

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z.\-]{0,9}")

# Words that look like tickers but never are. Only consulted by the last-resort
# branch of ticker detection.
STOPWORDS = frozenset(
    {
        "a",
        "add",
        "all",
        "an",
        "and",
        "any",
        "are",
        "at",
        "buy",
        "can",
        "cash",
        "dollars",
        "do",
        "for",
        "from",
        "get",
        "give",
        "how",
        "i",
        "in",
        "is",
        "it",
        "like",
        "list",
        "long",
        "lot",
        "market",
        "me",
        "more",
        "much",
        "my",
        "now",
        "of",
        "on",
        "order",
        "please",
        "portfolio",
        "position",
        "positions",
        "price",
        "remove",
        "sell",
        "share",
        "shares",
        "short",
        "some",
        "stock",
        "stocks",
        "than",
        "that",
        "the",
        "this",
        "to",
        "unwatch",
        "up",
        "want",
        "watch",
        "watchlist",
        "with",
        "worth",
        "would",
        "you",
        "your",
    }
)


def format_quantity(quantity: float) -> str:
    """`5.0` renders as `5`, `2.5` as `2.5` - quantities read as the user typed them."""
    if quantity == int(quantity):
        return str(int(quantity))
    return f"{quantity:g}"


def _has_word(message: str, word: str) -> bool:
    """Whole-word match, so `watch` does not fire on `unwatch` or `watchlist`."""
    return re.search(rf"\b{re.escape(word)}\b", message, re.IGNORECASE) is not None


def _find_quantity(message: str) -> float | None:
    match = NUMBER_RE.search(message)
    return float(match.group()) if match else None


def _find_ticker(message: str, known_tickers: list[str]) -> str | None:
    """Resolve a ticker from free text, most trustworthy signal first."""
    known = {ticker.upper(): ticker for ticker in known_tickers}
    words = [(match.start(), match.group()) for match in WORD_RE.finditer(message)]

    # 1. A symbol the user already holds or watches.
    for _, word in words:
        if word.upper() in known:
            return known[word.upper()]

    # 2. An UPPERCASE token - how people usually write a symbol they don't own yet.
    for _, word in words:
        if word.isupper() and 1 <= len(word) <= 5 and word.lower() not in STOPWORDS:
            return word

    # 3. Last resort: the first word that isn't obvious English filler.
    for _, word in words:
        if 1 <= len(word) <= 5 and word.lower() not in STOPWORDS:
            return word.upper()

    return None


class MockChatClient:
    """Keyword-driven stand-in for the real model (API_CONTRACT section 6)."""

    is_mock = True

    def complete(
        self,
        *,
        user_message: str,
        context: PortfolioContext,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        del history  # A deterministic mock has no use for conversation history.
        return build_mock_response(user_message, context)


def build_mock_response(user_message: str, context: PortfolioContext) -> ChatResponse:
    """Map a user message onto a fixed structured response.

    Branch order is buy, sell, remove/unwatch, watch/add, fallback. Removal is
    checked before addition so that "remove NVDA and add PYPL"-style messages
    resolve to the destructive intent the user named first in the table, and so
    a stray "add" in a removal sentence cannot invert the action.
    """
    ticker = _find_ticker(user_message, context.known_tickers)
    quantity = _find_quantity(user_message)

    if _has_word(user_message, "buy") and ticker and quantity:
        return ChatResponse(
            message=f"Buying {format_quantity(quantity)} {ticker} at the market price now.",
            trades=[ChatTrade(ticker=ticker, side="buy", quantity=quantity)],
        )

    if _has_word(user_message, "sell") and ticker and quantity:
        return ChatResponse(
            message=f"Selling {format_quantity(quantity)} {ticker} at the market price now.",
            trades=[ChatTrade(ticker=ticker, side="sell", quantity=quantity)],
        )

    if (_has_word(user_message, "remove") or _has_word(user_message, "unwatch")) and ticker:
        return ChatResponse(
            message=f"Removing {ticker} from your watchlist.",
            watchlist_changes=[ChatWatchlistChange(ticker=ticker, action="remove")],
        )

    if (_has_word(user_message, "watch") or _has_word(user_message, "add")) and ticker:
        return ChatResponse(
            message=f"Adding {ticker} to your watchlist.",
            watchlist_changes=[ChatWatchlistChange(ticker=ticker, action="add")],
        )

    count = context.position_count
    plural = "" if count == 1 else "s"
    return ChatResponse(
        message=(
            f"You are holding {count} position{plural} with "
            f"${context.cash_balance:,.2f} in cash. "
            "Ask me to buy or sell a ticker, or to add one to your watchlist."
        )
    )
