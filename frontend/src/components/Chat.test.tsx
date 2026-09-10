import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { sampleChatHistory } from "@/lib/fixtures";
import { installFakeApi, installFakeStream, renderTerminal, type FakeApi } from "@/test/harness";
import { Chat } from "./Chat";

let api: FakeApi;

beforeEach(() => {
  installFakeStream();
  api = installFakeApi({ messages: sampleChatHistory });
});

const noop = () => {};

describe("Chat", () => {
  it("restores the conversation on mount", async () => {
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByText("Buy 5 shares of NVDA");

    const turns = screen.getAllByTestId("chat-message");
    expect(turns.map((turn) => turn.dataset.role)).toEqual(["user", "assistant"]);
    expect(api.calls.some((call) => call.path === "/api/chat/history")).toBe(true);
  });

  it("renders an executed action inline as a green chip", async () => {
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByText("Buy 5 shares of NVDA");

    const chip = screen.getByTestId("chat-action");
    expect(chip).toHaveAttribute("data-status", "ok");
    expect(chip).toHaveAttribute("data-type", "trade");
    expect(chip).toHaveTextContent("Bought 5 NVDA @ $121.40");
  });

  it("renders a failed action verbatim, without rephrasing it", async () => {
    const detail = "Insufficient cash: need $50,000.00, have $9,393.00";
    installFakeApi({
      messages: [
        {
          id: "m3",
          role: "assistant",
          content: "That order is too large.",
          actions: [
            {
              type: "trade",
              status: "error",
              detail,
              data: { ticker: "TSLA", side: "buy", quantity: 200 },
            },
          ],
          created_at: "2026-09-09T14:33:00.000000Z",
        },
      ],
    });
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);

    const chip = await screen.findByTestId("chat-action");
    expect(chip).toHaveAttribute("data-status", "error");
    expect(chip).toHaveTextContent(detail);
  });

  it("shows a loading indicator while the assistant is thinking, then the reply", async () => {
    const user = userEvent.setup();
    installFakeApi({ messages: [] });

    // Hold the chat response open so the pending state is observable.
    let release: (value: Response) => void = () => {};
    const held = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const realFetch = globalThis.fetch;
    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/chat" ? held : (realFetch as typeof fetch)(input, init),
    ) as unknown as typeof fetch;

    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByTestId("chat-empty");

    await user.type(screen.getByTestId("chat-input"), "How am I doing?");
    await user.click(screen.getByTestId("chat-send"));

    // The question appears immediately; the answer is still in flight.
    expect(await screen.findByTestId("chat-loading")).toHaveTextContent("Thinking");
    expect(screen.getByTestId("chat-input")).toBeDisabled();

    release(
      new Response(
        JSON.stringify({ message: "Up 1.2% today.", actions: [], created_at: "2026-09-09T14:34:00.000000Z" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
    expect(screen.getByText("Up 1.2% today.")).toBeInTheDocument();
    globalThis.fetch = realFetch;
  });

  it("sends the message and clears the field", async () => {
    const user = userEvent.setup();
    installFakeApi({ messages: [] });
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByTestId("chat-empty");

    const input = screen.getByTestId("chat-input");
    await user.type(input, "Buy 5 NVDA");
    await user.click(screen.getByTestId("chat-send"));

    await waitFor(() => expect(input).toHaveValue(""));
    expect(
      (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.some(
        (call) => call[0] === "/api/chat",
      ),
    ).toBe(true);
  });

  it("refuses to send an empty message", async () => {
    installFakeApi({ messages: [] });
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByTestId("chat-empty");
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });

  it("explains itself when the assistant is unreachable", async () => {
    const user = userEvent.setup();
    const local = installFakeApi({ messages: [] });
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByTestId("chat-empty");

    local.failNext("/api/chat", "LLM_ERROR", "The model did not respond.", 502);
    await user.type(screen.getByTestId("chat-input"), "hello");
    await user.click(screen.getByTestId("chat-send"));

    expect(await screen.findByText("The model did not respond.")).toBeInTheDocument();
  });

  it("fills the composer from a suggestion", async () => {
    const user = userEvent.setup();
    installFakeApi({ messages: [] });
    renderTerminal(<Chat collapsed={false} onToggle={noop} />);
    await screen.findByTestId("chat-empty");

    const [first] = screen.getAllByTestId("chat-suggestion");
    await user.click(first);
    expect(screen.getByTestId("chat-input")).toHaveValue(first.textContent);
  });

  it("collapses to a rail that can reopen it", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    renderTerminal(<Chat collapsed onToggle={onToggle} />);

    const panel = screen.getByTestId("chat-panel");
    expect(panel).toHaveAttribute("data-collapsed", "true");
    expect(within(panel).queryByTestId("chat-input")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("chat-toggle"));
    expect(onToggle).toHaveBeenCalledOnce();
  });
});
