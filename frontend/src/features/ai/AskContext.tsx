import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { type AskEvent, aiKeys, askStream, getThread, type ThreadOut } from "./api";

/** One question and its answer, as the panel shows it. */
export interface Turn {
  id: number;
  question: string;
  answer: string;
  /** Tool status labels, in order ("Adding up spending"). */
  tools: string[];
  /** Shown above the question, like "New conversation…". */
  startNote: string | null;
  /** Shown under the answer: a cut-off, a refusal, a stop. */
  notice: string | null;
  error: string | null;
  /** The error may say AI is off or paused (a 409, or an off or pause error event). The panel's footer says that too. */
  offError: boolean;
  /** Admin only. */
  costMicro: number | null;
  streaming: boolean;
}

/** Same text as the server's (app/ai/ask.py NEW_THREAD_TEXT). */
export const NEW_THREAD_TEXT = "New conversation. Claude can't see the questions above.";
export const STOPPED_TEXT = "Stopped. Your next question starts a new conversation.";
export const STOPPED_EARLY_TEXT = "The answer stopped early. Ask again.";

interface AskState {
  open: boolean;
  /** Opening remembers what had focus, so closing can go back to it. */
  setOpen: (open: boolean) => void;
  /** Where focus goes when the panel closes. */
  opener: () => HTMLElement | null;
  view: "chat" | "history";
  setView: (view: "chat" | "history") => void;
  turns: Turn[];
  threadId: string | null;
  /** The conversation on screen takes no more questions; the next starts a new one. */
  ended: boolean;
  /** Admin only: what a thread opened from history cost. */
  threadCostMicro: number | null;
  busy: boolean;
  /** The unsent question. Kept while the panel closes or shows history. */
  draft: string;
  setDraft: (text: string) => void;
  ask: (question: string) => Promise<void>;
  stop: () => void;
  newThread: () => void;
  openThread: (thread: ThreadOut) => void;
  loadThread: (threadId: string) => Promise<void>;
}

const AskContext = createContext<AskState | null>(null);

/** The Ask panel's state. It lives above the pages, so a conversation survives navigation. */
export function useAsk(): AskState {
  const ctx = useContext(AskContext);
  if (!ctx) throw new Error("useAsk needs AskProvider");
  return ctx;
}

let nextId = 1;

export function AskProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [open, setOpenState] = useState(false);
  const [view, setView] = useState<"chat" | "history">("chat");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [threadCostMicro, setThreadCostMicro] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const abort = useRef<AbortController | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const setOpen = useCallback((next: boolean) => {
    if (next) openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpenState(next);
  }, []);

  const opener = useCallback(() => {
    const el = openerRef.current;
    if (el?.isConnected) return el;
    // Opened from the phone menu, which is gone now: the menu button, else the Ask button.
    return document.querySelector<HTMLElement>('[aria-label="Open menu"], button[aria-label="Ask"]');
  }, []);

  const update = useCallback((id: number, change: (t: Turn) => Partial<Turn>) => {
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...change(t) } : t)));
  }, []);

  const ask = useCallback(
    async (question: string) => {
      if (busy) return;
      const id = nextId++;
      const controller = new AbortController();
      abort.current = controller;
      // After an ended conversation the next question starts a new thread.
      const sendThread = ended ? null : threadId;
      const startNote = ended && turns.length > 0 ? NEW_THREAD_TEXT : null;
      setBusy(true);
      setView("chat");
      setDraft("");
      setTurns((ts) => [
        ...ts,
        { id, question, answer: "", tools: [], startNote, notice: null, error: null, offError: false, costMicro: null, streaming: true },
      ]);
      let started = false;
      let finished = false;
      const onEvent = (e: AskEvent) => {
        if (e.type === "done" || e.type === "error") finished = true;
        switch (e.type) {
          case "thread":
            started = true;
            setThreadId(e.thread_id);
            setEnded(false);
            if (e.new) setThreadCostMicro(null);
            if (e.notice) update(id, () => ({ startNote: e.notice }));
            break;
          case "status":
            update(id, (t) => (t.tools[t.tools.length - 1] === e.label ? {} : { tools: [...t.tools, e.label] }));
            break;
          case "text":
            update(id, (t) => ({ answer: t.answer + e.text }));
            break;
          case "replace":
            // The filter changed text already shown: show its whole output instead.
            update(id, () => ({ answer: e.text }));
            break;
          case "done":
            setEnded(e.thread_closed);
            update(id, () => ({ answer: e.text, notice: e.notice, costMicro: e.cost_micro_usd, streaming: false }));
            break;
          case "error": {
            // The server closes a thread whose turn did not finish.
            const off = e.kind === "unavailable" || e.kind === "spend_limit";
            setEnded(true);
            update(id, () => ({ error: e.message, offError: off, streaming: false }));
            if (off) void qc.invalidateQueries({ queryKey: aiKeys.status });
            break;
          }
        }
      };
      try {
        await askStream(question, sendThread, onEvent, controller.signal);
        // The stream closed with no done or error event: the turn did not finish.
        if (!finished) {
          setEnded(true);
          update(id, () => ({ error: STOPPED_EARLY_TEXT, streaming: false }));
        }
      } catch (err) {
        // Another conversation was opened meanwhile: this turn is no longer on screen.
        if (abort.current !== controller) return;
        if (err instanceof DOMException && err.name === "AbortError") {
          setEnded(true);
          update(id, () => ({ notice: STOPPED_TEXT }));
        } else {
          const message = err instanceof ApiError ? err.message : "Claude did not answer just now. Try again in a minute.";
          update(id, () => ({ error: message, offError: err instanceof ApiError && err.status === 409 }));
          if (started) setEnded(true);
          // Refused before it started: nothing was stored. Give the question back to edit.
          else setDraft((d) => d || question);
          // A 409 can mean AI went off or paused: refresh the status, so the panel says so.
          if (err instanceof ApiError && err.status === 409) void qc.invalidateQueries({ queryKey: aiKeys.status });
        }
      } finally {
        update(id, (t) => (t.streaming ? { streaming: false } : {}));
        if (abort.current === controller) abort.current = null;
        setBusy(false);
        void qc.invalidateQueries({ queryKey: aiKeys.threads });
        void qc.invalidateQueries({ queryKey: aiKeys.usage });
      }
    },
    [busy, ended, threadId, turns.length, update, qc],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  /** Stops a running turn without touching what is on screen: it is about to be replaced. */
  const drop = useCallback(() => {
    const old = abort.current;
    abort.current = null;
    old?.abort();
  }, []);

  const newThread = useCallback(() => {
    drop();
    setTurns([]);
    setThreadId(null);
    setEnded(false);
    setThreadCostMicro(null);
    setView("chat");
  }, [drop]);

  const openThread = useCallback((thread: ThreadOut) => {
    drop();
    setTurns(
      thread.turns.map((t) => ({
        id: nextId++,
        question: t.question,
        answer: t.answer,
        tools: t.tools,
        startNote: null,
        notice: t.notice,
        error: null,
        offError: false,
        costMicro: null,
        streaming: false,
      })),
    );
    setThreadId(thread.thread_id);
    setEnded(thread.closed);
    setThreadCostMicro(thread.cost_micro_usd ?? null);
    setView("chat");
  }, [drop]);

  const loadThread = useCallback(
    async (id: string) => {
      const thread = await qc.fetchQuery({ queryKey: aiKeys.thread(id), queryFn: () => getThread(id), staleTime: 0 });
      openThread(thread);
    },
    [qc, openThread],
  );

  const value = useMemo<AskState>(
    () => ({
      open,
      setOpen,
      opener,
      view,
      setView,
      turns,
      threadId,
      ended,
      threadCostMicro,
      busy,
      draft,
      setDraft,
      ask,
      stop,
      newThread,
      openThread,
      loadThread,
    }),
    [open, setOpen, opener, view, turns, threadId, ended, threadCostMicro, busy, draft, ask, stop, newThread, openThread, loadThread],
  );
  return <AskContext.Provider value={value}>{children}</AskContext.Provider>;
}
