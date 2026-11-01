import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Check, ChevronLeft, History, LoaderCircle, Sparkles, Square, SquarePen, X } from "lucide-react";
import { type FormEvent, type KeyboardEvent, type RefObject, useEffect, useRef, useState } from "react";
import { Badge } from "../../components/ui/badge";
import { OVERLAY_MOTION, sheetMotion } from "../../components/ui/drawer";
import { ErrorBanner } from "../../components/ui/error-banner";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatTimestamp } from "../../lib/format";
import { type AiStatus, aiKeys, formatMicroUsd, getThreads, MAX_QUESTION, useAiStatus } from "./api";
import { STOPPED_TEXT, type Turn, useAsk } from "./AskContext";
import { SafeText } from "./SafeText";

/** Shown on an empty panel. Each one uses a different tool. */
export const STARTERS = [
  "How much did we spend on dining last month?",
  "Which bills are due in the next two weeks?",
  "How has our net worth changed this year?",
];

export const ENDED_TEXT = "This conversation has ended. Your next question starts a new one.";

const ICON_BUTTON =
  "inline-flex h-11 w-11 items-center justify-center rounded-lg text-fg-secondary hover:bg-active disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent md:h-9 md:w-9";

const wide = () => window.matchMedia("(min-width: 768px)").matches;

/** Why Ask cannot take a question now, in the panel's words. A pause uses the server's reason. */
export function offText(status: AiStatus | undefined): string {
  if (status?.reason === "spend_limit") {
    const until = status.blocked_until ? ` until ${formatTimestamp(status.blocked_until)}` : "";
    return `Ask is paused${until}. ${status.blocked_reason ?? "The Claude spend limit was reached."}`;
  }
  if (status?.reason === "no_key") return "Ask is off: no API key is set on the server.";
  return "Ask is off right now. An admin can turn it on in Settings.";
}

/**
 * Ask (PLAN 7.1, 8.2): a 400px panel on the right, full screen on phones.
 * Answers stream in. Status chips show which tools Claude is using, because
 * its text between tool calls does not stream. Admins see what each answer cost.
 * If AI goes off while the panel is open, the panel stays and says so.
 */
export function AskPanel() {
  const s = useAsk();
  const status = useAiStatus();
  const input = useRef<HTMLTextAreaElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const available = status.data?.available === true;
  // The footer says why Ask is off or paused, so a turn's error does not say it again.
  const offShown = !available && !s.busy && status.data !== undefined;
  const lastView = useRef(s.view);

  // Moving between the conversation and the list puts focus somewhere useful.
  useEffect(() => {
    if (lastView.current === s.view) return;
    lastView.current = s.view;
    if (!s.open) return;
    if (s.view === "history") back.current?.focus();
    else if (wide()) input.current?.focus();
  }, [s.view, s.open]);

  function onOpenAutoFocus(event: Event) {
    event.preventDefault();
    // Phones: focus the panel, so the keyboard does not cover the starters.
    if (wide() && input.current) input.current.focus();
    else if (event.currentTarget instanceof HTMLElement) event.currentTarget.focus();
  }

  function onCloseAutoFocus(event: Event) {
    event.preventDefault();
    s.opener()?.focus();
  }

  return (
    <Dialog.Root open={s.open} onOpenChange={s.setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className={cn("fixed inset-0 z-40 bg-overlay", OVERLAY_MOTION)} />
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={onOpenAutoFocus}
          onCloseAutoFocus={onCloseAutoFocus}
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full flex-col bg-card shadow-pop outline-none md:w-[400px]",
            sheetMotion("right"),
          )}
        >
          <div className="flex items-center gap-1 border-b border-line px-3 py-2 pt-[max(8px,env(safe-area-inset-top))]">
            {s.view === "history" ? (
              <button ref={back} type="button" className={ICON_BUTTON} aria-label="Back to the conversation" onClick={() => s.setView("chat")}>
                <ChevronLeft size={20} aria-hidden="true" />
              </button>
            ) : (
              <Sparkles size={20} aria-hidden="true" className="mx-2.5 shrink-0 text-accent-text" />
            )}
            <Dialog.Title className="m-0 min-w-0 flex-1 text-lg font-medium text-fg">
              {s.view === "history" ? "Past conversations" : "Ask"}
            </Dialog.Title>
            {s.view === "chat" && (
              <>
                <button type="button" className={ICON_BUTTON} aria-label="Past conversations" title="Past conversations" onClick={() => s.setView("history")}>
                  <History size={18} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className={ICON_BUTTON}
                  aria-label="New conversation"
                  title="New conversation"
                  disabled={s.turns.length === 0 && !s.threadId}
                  onClick={() => {
                    s.newThread();
                    input.current?.focus();
                  }}
                >
                  <SquarePen size={18} aria-hidden="true" />
                </button>
              </>
            )}
            <Dialog.Close className={ICON_BUTTON} aria-label="Close">
              <X size={20} aria-hidden="true" />
            </Dialog.Close>
          </div>
          {s.view === "history" ? (
            <ThreadList retentionDays={status.data?.thread_retention_days ?? 90} />
          ) : (
            <>
              <Conversation
                canAsk={available}
                offShown={offShown}
                onStarter={(q) => {
                  void s.ask(q);
                  if (wide()) input.current?.focus();
                }}
              />
              {available || s.busy ? (
                <AskForm inputRef={input} />
              ) : (
                <p role="status" className="m-0 border-t border-line px-5 pb-[max(16px,env(safe-area-inset-bottom))] pt-4 text-sm text-fg-secondary">
                  {status.data ? offText(status.data) : "Checking whether Ask is on…"}
                </p>
              )}
            </>
          )}
          {/* Always mounted, so the first announcement is not missed. */}
          <p className="sr-only" aria-live="polite">
            {announcement(s.turns[s.turns.length - 1])}
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** What a screen reader hears: once when Claude starts, then the answer or the notice. */
function announcement(last: Turn | undefined): string {
  if (!last) return "";
  if (last.streaming) return "Claude is working on it.";
  if (last.error) return ""; // the error has role="alert"
  const answer = last.answer.replace(/\*\*|`/g, "");
  return [answer && `Answer: ${answer}`, last.notice].filter(Boolean).join(" ");
}

function Conversation({
  canAsk,
  offShown,
  onStarter,
}: {
  canAsk: boolean;
  offShown: boolean;
  onStarter: (question: string) => void;
}) {
  const s = useAsk();
  const isAdmin = useMe().data?.role === "admin";
  const scroller = useRef<HTMLDivElement>(null);
  // True while the reader is at the bottom. Scrolling up stops the auto-scroll.
  const pinned = useRef(true);
  const last = s.turns[s.turns.length - 1];

  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [last?.answer, last?.tools.length, last?.streaming, s.turns.length]);

  function onScroll() {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  if (s.turns.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 overflow-y-auto px-5 py-6 text-center">
        <Sparkles size={28} aria-hidden="true" className="text-accent-text" />
        <div>
          <p className="m-0 text-base font-medium text-fg">Ask about your money</p>
          <p className="m-0 mt-1 text-sm text-fg-secondary">
            Answers come from the app's accounts, budgets and transactions. Claude can only read.
          </p>
        </div>
        {canAsk && (
          <ul className="m-0 flex w-full list-none flex-col gap-2 p-0">
            {STARTERS.map((q) => (
              <li key={q}>
                <button
                  type="button"
                  onClick={() => {
                    pinned.current = true;
                    onStarter(q);
                  }}
                  className="min-h-11 w-full rounded-lg border border-line bg-card px-3 py-2 text-left text-sm text-fg hover:bg-hover"
                >
                  {q}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <>
      {/* Focusable, so arrow and Page keys scroll back through long answers. */}
      <div
        ref={scroller}
        onScroll={onScroll}
        tabIndex={0}
        role="region"
        aria-label="Conversation"
        className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 py-4"
      >
        {s.turns.map((t) => (
          <TurnView key={t.id} turn={t} isAdmin={isAdmin} offShown={offShown} />
        ))}
        {/* A stopped turn's notice already says the next question starts anew. */}
        {s.ended && !s.busy && last?.notice !== STOPPED_TEXT && (
          <p className="m-0 text-center text-xs text-fg-secondary">{ENDED_TEXT}</p>
        )}
        {isAdmin && s.threadCostMicro !== null && !s.busy && (
          <p className="m-0 text-xs text-fg-secondary">This conversation cost {formatMicroUsd(s.threadCostMicro)}.</p>
        )}
      </div>
    </>
  );
}

function TurnView({ turn, isAdmin, offShown }: { turn: Turn; isAdmin: boolean; offShown: boolean }) {
  const waiting = turn.streaming && !turn.answer;
  return (
    <article className="flex animate-rise-in flex-col gap-2">
      {turn.startNote && <p className="m-0 text-center text-xs text-fg-secondary">{turn.startNote}</p>}
      <p className="m-0 ml-8 self-end whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-active px-3.5 py-2 text-sm text-fg">
        <span className="sr-only">You asked: </span>
        {turn.question}
      </p>
      {turn.tools.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label="Steps">
          {turn.tools.map((label, i) => {
            const working = turn.streaming && !turn.answer && i === turn.tools.length - 1;
            return (
              <li key={`${label}-${i}`}>
                <Badge
                  tone="neutral"
                  icon={
                    working ? (
                      <LoaderCircle size={12} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                    ) : (
                      <Check size={12} aria-hidden="true" />
                    )
                  }
                >
                  {label}
                </Badge>
              </li>
            );
          })}
        </ul>
      )}
      {waiting && turn.tools.length === 0 && (
        // A soft light passes over the text while Claude works (plain text under reduced motion).
        <p className="m-0 text-sm text-fg-secondary">
          <span className="text-shimmer">Thinking…</span>
        </p>
      )}
      {turn.answer && <SafeText text={turn.answer} className="text-sm leading-relaxed text-fg" />}
      {turn.notice && <p className="m-0 text-sm text-fg-secondary">{turn.notice}</p>}
      {turn.error && !(turn.offError && offShown) && (
        <p role="alert" className="m-0 text-sm text-bad">
          {turn.error}
        </p>
      )}
      {isAdmin && turn.costMicro !== null && (
        <p className="m-0 text-xs text-fg-secondary">Cost {formatMicroUsd(turn.costMicro)}</p>
      )}
    </article>
  );
}

function AskForm({ inputRef }: { inputRef: RefObject<HTMLTextAreaElement | null> }) {
  const s = useAsk();
  const text = s.draft;
  const trimmed = text.trim();
  const tooLong = text.length > MAX_QUESTION;
  // Stop and Send are one button. When a turn ends, an empty box disables Send,
  // and a focused button that turns disabled drops focus out of the panel.
  const action = useRef<HTMLButtonElement>(null);
  const wasBusy = useRef(s.busy);

  useEffect(() => {
    const ended = wasBusy.current && !s.busy;
    wasBusy.current = s.busy;
    if (!ended) return;
    const el = document.activeElement;
    if (!((el === action.current && action.current?.disabled) || el === document.body)) return;
    // Phones: focus the panel, so the keyboard does not pop up.
    if (wide()) inputRef.current?.focus();
    else inputRef.current?.closest<HTMLElement>('[role="dialog"]')?.focus();
  }, [s.busy, inputRef]);

  // Grow with the text, up to about six lines.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text, inputRef]);

  function send(event?: FormEvent) {
    event?.preventDefault();
    if (!trimmed || tooLong || s.busy) return;
    void s.ask(trimmed);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // 229: the Enter that confirms an IME word, which some browsers do not flag as composing.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) send(event);
  }

  return (
    <form onSubmit={send} className="border-t border-line px-3 pb-[max(12px,env(safe-area-inset-bottom))] pt-3">
      <div className="flex items-end gap-2 rounded-xl border border-line bg-page px-3 py-1.5 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-[var(--accent)]">
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <textarea
          id="ask-input"
          ref={inputRef}
          rows={1}
          value={text}
          onChange={(e) => s.setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          enterKeyHint="send"
          placeholder="Ask about spending, bills, balances…"
          aria-describedby="ask-hint"
          className="max-h-40 min-h-11 flex-1 resize-none border-0 bg-transparent py-2.5 text-base text-fg outline-none placeholder:text-fg-secondary md:min-h-9 md:py-1.5 md:text-sm"
        />
        {s.busy ? (
          <button ref={action} type="button" onClick={s.stop} aria-label="Stop" className={cn(ICON_BUTTON, "text-fg")}>
            <Square size={16} aria-hidden="true" fill="currentColor" />
          </button>
        ) : (
          <button
            ref={action}
            type="submit"
            aria-label="Send"
            disabled={!trimmed || tooLong}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary text-on-primary hover:bg-primary-hover disabled:opacity-40 md:h-9 md:w-9"
          >
            <ArrowUp size={18} aria-hidden="true" />
          </button>
        )}
      </div>
      <p id="ask-hint" className={cn("m-0 mt-1.5 px-1 text-xs", tooLong ? "text-bad" : "text-fg-secondary")}>
        {tooLong
          ? `Too long: ${text.length.toLocaleString("en-US")} of ${MAX_QUESTION.toLocaleString("en-US")} characters.`
          : "Numbers come from the app. Check the ones that matter."}
      </p>
    </form>
  );
}

function ThreadList({ retentionDays }: { retentionDays: number }) {
  const s = useAsk();
  const q = useQuery({ queryKey: aiKeys.threads, queryFn: getThreads });
  const [loadError, setLoadError] = useState<string | null>(null);
  const threads = q.data?.threads ?? [];

  async function open(id: string) {
    setLoadError(null);
    try {
      await s.loadThread(id);
    } catch {
      setLoadError("Could not open that conversation.");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 py-3">
      {q.isError && <ErrorBanner message="Could not load past conversations." onRetry={() => void q.refetch()} retrying={q.isFetching} />}
      {loadError && (
        <p role="alert" className="m-0 mb-2 px-2 text-sm text-bad">
          {loadError}
        </p>
      )}
      {q.isPending && <p className="m-0 px-2 text-sm text-fg-secondary">Loading…</p>}
      {q.data && threads.length === 0 && <p className="m-0 px-2 py-6 text-center text-sm text-fg-secondary">No conversations yet.</p>}
      <ul className="m-0 list-none p-0">
        {threads.map((t) => {
          const current = t.thread_id === s.threadId;
          return (
            <li key={t.thread_id}>
              <button
                type="button"
                aria-current={current ? "true" : undefined}
                onClick={() => void open(t.thread_id)}
                className={cn(
                  "flex min-h-14 w-full flex-col items-start gap-0.5 rounded-lg px-2 py-2 text-left hover:bg-hover",
                  current && "bg-active",
                )}
              >
                <span className="line-clamp-2 text-sm text-fg">{t.title}</span>
                <span className="text-xs text-fg-secondary">
                  {formatTimestamp(t.updated_at)} · {t.turn_count === 1 ? "1 question" : `${t.turn_count} questions`}
                  {t.closed && " · ended"}
                  {current && " · open now"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="m-0 mt-auto px-2 pt-4 text-xs text-fg-secondary">
        Only you see your conversations. They are deleted {retentionDays} days after they start.
      </p>
    </div>
  );
}
