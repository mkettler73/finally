"""The system prompt. Wording is free to change; these guarantees are not."""

from __future__ import annotations

from app.llm.prompt import SYSTEM_PROMPT, build_system_message


class TestSystemPrompt:
    def test_names_the_assistant_and_the_simulated_stakes(self):
        assert "FinAlly" in SYSTEM_PROMPT
        assert "simulated" in SYSTEM_PROMPT

    def test_describes_every_field_of_the_structured_output(self):
        for field in ("message", "trades", "watchlist_changes"):
            assert f"`{field}`" in SYSTEM_PROMPT

    def test_states_the_execution_order(self):
        assert "Trades are executed first, then watchlist changes" in SYSTEM_PROMPT

    def test_warns_against_inventing_numbers(self):
        assert "never invent a price" in SYSTEM_PROMPT

    def test_tells_the_model_that_trades_fill_immediately(self):
        assert "no confirmation step" in SYSTEM_PROMPT


class TestBuildSystemMessage:
    def test_appends_the_context_under_a_labelled_heading(self):
        message = build_system_message("Cash: $10,000.00")
        assert message.startswith(SYSTEM_PROMPT)
        assert "=== CONTEXT (live, as of this message) ===" in message
        assert message.endswith("Cash: $10,000.00")

    def test_an_empty_context_still_produces_a_usable_prompt(self):
        assert SYSTEM_PROMPT in build_system_message("")
