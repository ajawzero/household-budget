import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * Renders Claude's text (PLAN 7.7): paragraphs, lists, bold and inline code
 * only. Everything else stays plain text: no links, no images, no HTML. It
 * builds React elements, never HTML strings, so markup in a merchant name or
 * an answer shows as typed.
 */
export function SafeText({ text, className }: { text: string; className?: string }) {
  return <div className={cn("flex flex-col gap-2 break-words", className)}>{blocks(text)}</div>;
}

/** One line of text with bold and inline code. */
export function SafeInline({ text }: { text: string }) {
  return <>{inline(text)}</>;
}

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*(\d{1,3})[.)]\s+/;

/** A numbered list keeps its first number, so "2." after a paragraph stays 2. */
type Block = { kind: "p"; lines: string[] } | { kind: "ul" | "ol"; items: string[]; start: number };

function parse(text: string): Block[] {
  const out: Block[] = [];
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.replace(/^\s*#{1,6}\s+/, ""); // a heading reads as a plain line
    const last = out[out.length - 1];
    if (line.trim() === "") {
      if (last) out.push({ kind: "p", lines: [] });
      continue;
    }
    const kind = BULLET.test(line) ? "ul" : NUMBERED.test(line) ? "ol" : null;
    if (kind) {
      const item = line.replace(kind === "ul" ? BULLET : NUMBERED, "");
      if (last?.kind === kind) last.items.push(item);
      else out.push({ kind, items: [item], start: Number(NUMBERED.exec(line)?.[1] ?? 1) });
    } else if (last?.kind === "p") {
      last.lines.push(line);
    } else {
      out.push({ kind: "p", lines: [line] });
    }
  }
  return out.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.items.length > 0));
}

function blocks(text: string): ReactNode[] {
  return parse(text).map((b, i) => {
    if (b.kind === "p") {
      return (
        <p key={i} className="m-0">
          {b.lines.map((l, j) => (
            <span key={j}>
              {j > 0 && <br />}
              {inline(l)}
            </span>
          ))}
        </p>
      );
    }
    const List = b.kind;
    return (
      <List
        key={i}
        start={b.kind === "ol" && b.start !== 1 ? b.start : undefined}
        className={cn("m-0 flex flex-col gap-1 pl-5", b.kind === "ol" ? "list-decimal" : "list-disc")}
      >
        {b.items.map((item, j) => (
          <li key={j}>{inline(item)}</li>
        ))}
      </List>
    );
  });
}

/** `code` first (nothing inside it is styled), then **bold**. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const parts = text.split(/(`[^`\n]+`)/g);
  parts.forEach((part, i) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
      out.push(
        <code key={`c${i}`} className="rounded bg-subtle px-1 font-mono text-[0.9em]">
          {part.slice(1, -1)}
        </code>,
      );
      return;
    }
    part.split(/(\*\*[^*\n]+\*\*)/g).forEach((piece, j) => {
      if (piece.length > 4 && piece.startsWith("**") && piece.endsWith("**")) {
        out.push(
          <strong key={`b${i}-${j}`} className="font-medium text-fg">
            {piece.slice(2, -2)}
          </strong>,
        );
      } else if (piece) {
        out.push(piece);
      }
    });
  });
  return out;
}
