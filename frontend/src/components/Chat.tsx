"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { formatClock } from "@/lib/format";
import type { ChatAction, ChatMessage } from "@/lib/types";
import { useTerminal } from "@/state/terminal";

const SUGGESTIONS = [
  "How is my portfolio doing?",
  "Buy 5 shares of NVDA",
  "Add PYPL to my watchlist",
];

export function Chat({
  collapsed,
  onToggle,
}: {
  collapsed: boolean;
  onToggle: () => void;
}) {
  const { messages, chatPending, send, loaded } = useTerminal();
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, chatPending]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || chatPending) return;
    setDraft("");
    void send(text);
  };

  if (collapsed) {
    return (
      <aside
        className="flex flex-col items-center gap-3 bg-rail py-3"
        data-testid="chat-panel"
        data-collapsed="true"
      >
        <button
          type="button"
          onClick={onToggle}
          aria-label="Open the assistant"
          aria-expanded={false}
          className="text-ink-mute hover:text-accent"
          data-testid="chat-toggle"
        >
          <span aria-hidden className="text-[13px]">
            &laquo;
          </span>
        </button>
        <span
          className="panel-label"
          style={{ writingMode: "vertical-rl" }}
          aria-hidden
        >
          Assistant
        </span>
      </aside>
    );
  }

  return (
    <aside
      className="flex min-h-0 flex-col bg-panel"
      data-testid="chat-panel"
      data-collapsed="false"
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-rail px-3">
        <h2 className="panel-label">Assistant</h2>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse the assistant"
          aria-expanded
          className="ml-auto text-ink-mute hover:text-accent"
          data-testid="chat-toggle"
        >
          <span aria-hidden className="text-[13px]">
            &raquo;
          </span>
        </button>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3"
        data-testid="chat-messages"
      >
        {messages.length === 0 ? (
          <div data-testid="chat-empty">
            <p className="text-ink-dim">
              {loaded
                ? "Ask about your positions, or tell me what to trade. I can place orders and edit the watchlist for you."
                : "Restoring the conversation…"}
            </p>
            <ul className="mt-4 border-t border-line">
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion} className="border-b border-line">
                  <button
                    type="button"
                    onClick={() => setDraft(suggestion)}
                    className="group flex w-full items-center gap-2 py-1.5 text-left text-[12px] text-ink-dim transition-colors hover:text-ink"
                    data-testid="chat-suggestion"
                  >
                    <span
                      aria-hidden
                      className="h-3 w-px bg-transparent transition-colors group-hover:bg-accent"
                    />
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          messages.map((message) => <Turn key={message.id} message={message} />)
        )}

        {chatPending ? (
          <p className="flex items-center gap-2 text-ink-mute" data-testid="chat-loading">
            <span aria-hidden className="h-1.5 w-1.5 animate-pulse bg-accent" />
            Thinking…
          </p>
        ) : null}
      </div>

      <form
        onSubmit={onSubmit}
        className="shrink-0 border-t border-line bg-rail p-2"
        data-testid="chat-form"
      >
        <div className="flex gap-1.5">
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask or instruct"
            aria-label="Message the assistant"
            disabled={chatPending}
            className="min-w-0 flex-1 rounded-sm border border-line-strong bg-panel px-2 py-1.5 text-ink placeholder:text-ink-mute focus:border-accent focus:outline-none disabled:opacity-60"
            data-testid="chat-input"
          />
          <button
            type="submit"
            disabled={chatPending || draft.trim() === ""}
            className="rounded-sm bg-submit px-3 py-1.5 text-[12px] font-semibold tracking-wide text-white transition-colors hover:bg-submit-hot disabled:opacity-50"
            data-testid="chat-send"
          >
            Send
          </button>
        </div>
      </form>
    </aside>
  );
}

function Turn({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <article
      className={isUser ? "border-l-2 border-accent bg-raised px-2.5 py-1.5" : ""}
      data-testid="chat-message"
      data-role={message.role}
    >
      <p className={`whitespace-pre-wrap ${isUser ? "text-ink-dim" : "text-ink"}`}>
        {message.content}
      </p>

      {message.actions?.length ? (
        <ul className="mt-2 space-y-1">
          {message.actions.map((action, index) => (
            <li key={`${action.type}-${index}`}>
              <ActionChip action={action} />
            </li>
          ))}
        </ul>
      ) : null}

      {!isUser ? (
        <time
          className="num mt-1 block text-[10px] text-ink-mute"
          dateTime={message.created_at}
        >
          {formatClock(message.created_at)}
        </time>
      ) : null}
    </article>
  );
}

/**
 * An executed action, shown inline as confirmation. `detail` is the backend's
 * own wording — including its error messages — and is rendered verbatim.
 */
function ActionChip({ action }: { action: ChatAction }) {
  const ok = action.status === "ok";
  return (
    <span
      className={`flex items-start gap-1.5 rounded-sm border px-2 py-1 text-[11px] ${
        ok ? "border-up/40 bg-up/10 text-up" : "border-down/40 bg-down/10 text-down"
      }`}
      data-testid="chat-action"
      data-status={action.status}
      data-type={action.type}
    >
      <span aria-hidden className="mt-px text-[9px]">
        {ok ? "✓" : "✕"}
      </span>
      <span>{action.detail}</span>
    </span>
  );
}
