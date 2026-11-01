import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { type FormEvent, type Ref, useEffect, useId, useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import { ApiError, type BenefitFileBody, type BenefitFilePreview, type FileMode, getAccounts, queryKeys } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { cardOptions } from "./AddBenefit";
import { useApplyFile, useBenefitTemplate, usePreviewFile } from "./api";
import { FILE_MAX_BYTES, FILE_TOO_BIG, fileBody, fileChangeText, fileSavedText, KIND_TEXT, unchangedText } from "./model";
import { FormStatus } from "./parts";
import { failText } from "./ProgramNotices";

/** 16px text so iOS does not zoom. Tall enough for a short list. */
const TEXTAREA_CLASS =
  "min-h-40 w-full min-w-0 rounded-lg border border-line bg-card px-3 py-2 font-mono text-base text-fg placeholder:text-fg-faint";

const MODES: { key: FileMode; label: string; hint: string }[] = [
  { key: "merge", label: "Only the benefits that change", hint: "Benefits the file leaves out stay as they are." },
  { key: "replace", label: "Every benefit on the card", hint: "Benefits the file leaves out end the day before the start date." },
];

const MODE_TEXT: Record<FileMode, string> = { merge: MODES[0]!.label, replace: MODES[1]!.label };

const READ_FAILED = "Could not read the file. Pick it again.";

/** Apply refused a stale, broken or too big file. The preview no longer holds, so it goes. */
const RECHECK = [409, 413, 422];

/**
 * Saves data as a JSON file the browser downloads. The file is built in the
 * page, so it never sits on the server.
 */
export function saveJson(data: unknown, name: string): void {
  const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  // A tick later, once the download has its copy.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * "Update from a file" on the Benefits tab: one card's benefits from a JSON
 * file, checked first, then saved all or nothing (docs/plans/rewards-inputs.md
 * section 5). Admin only.
 */
export function FileUpdate({ today }: { today: string }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  return (
    <div className="flex flex-col md:items-end">
      <Button onClick={() => setOpen(true)} className="w-full justify-center md:w-auto">
        Update from a file
      </Button>
      <FormStatus problem={null} saved={saved} />
      <Drawer
        open={open}
        onOpenChange={setOpen}
        title="Update from a file"
        description="Change one card's benefits from a JSON file. Nothing is saved until you apply it."
      >
        <FileForm
          today={today}
          onSaved={(text) => {
            setSaved(text);
            setOpen(false);
          }}
        />
      </Drawer>
    </div>
  );
}

interface Checked {
  body: BenefitFileBody;
  preview: BenefitFilePreview;
}

/** A preview Apply can save: no problems, a base_sig, and something to change. */
function clean(c: Checked | null): c is Checked & { preview: { base_sig: string } } {
  return c !== null && c.preview.problems.length === 0 && c.preview.base_sig !== null && !c.preview.no_changes;
}

function FileForm({ today, onSaved }: { today: string; onSaved: (text: string) => void }) {
  const id = useId();
  // Mounted only while the drawer is open, so the cards load then.
  const accounts = useQuery({ queryKey: queryKeys.accounts(), queryFn: () => getAccounts() });
  const cards = cardOptions(accounts.data);
  const [card, setCard] = useState("");
  const [paste, setPaste] = useState(false);
  const [text, setText] = useState("");
  // The start date and mode show today and merge until a check fills in the
  // file's own. Once changed here, they win over the file's.
  const [startsOn, setStartsOn] = useState(today);
  const [startsSet, setStartsSet] = useState(false);
  const [mode, setMode] = useState<FileMode>("merge");
  const [modeSet, setModeSet] = useState(false);
  const [listNote, setListNote] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [checked, setChecked] = useState<Checked | null>(null);
  const [reading, setReading] = useState(false);
  // Check file is off while it works, so focus goes back to it once it is on again.
  const [backToCheck, setBackToCheck] = useState(false);
  // Bumped on every change, so a check that was under way when the form changed is dropped.
  const round = useRef(0);
  // The file is read from the input itself at check time, never from a copy,
  // so what is checked is always what the field shows.
  const fileInput = useRef<HTMLInputElement>(null);
  const checkButton = useRef<HTMLButtonElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const template = useBenefitTemplate();
  const preview = usePreviewFile();
  const apply = useApplyFile();
  const busy = reading || preview.isPending || apply.isPending;

  useEffect(() => {
    if (checked) previewHeading.current?.focus();
  }, [checked]);

  useEffect(() => {
    if (backToCheck && !busy) {
      checkButton.current?.focus();
      setBackToCheck(false);
    }
  }, [backToCheck, busy]);

  /** Anything that changes what was checked turns Apply off until the next check. */
  function changed() {
    round.current += 1;
    setChecked(null);
    setProblem(null);
  }

  /** A problem after Check file or Apply: said in words, with focus back on Check file. */
  function refused(message: string) {
    setProblem(message);
    setBackToCheck(true);
  }

  function download() {
    if (card === "") {
      setListNote("Pick a card.");
      return;
    }
    setListNote(null);
    template.mutate(card, {
      onSuccess: (f) => saveJson(f, `${card}-benefits.json`),
      onError: (err) => setListNote(failText(err)),
    });
  }

  function pickFile(f: File | null) {
    changed();
    if (f && f.size > FILE_MAX_BYTES) setProblem(FILE_TOO_BIG);
  }

  /** The text to check, or the problem. A file over 256 KB is refused before it is read. */
  async function readText(): Promise<string | { problem: string }> {
    if (paste) {
      if (text.trim() === "") return { problem: "Paste the file's text." };
      return new Blob([text]).size > FILE_MAX_BYTES ? { problem: FILE_TOO_BIG } : text;
    }
    const file = fileInput.current?.files?.[0];
    if (!file) return { problem: "Pick a file." };
    if (file.size > FILE_MAX_BYTES) return { problem: FILE_TOO_BIG };
    try {
      return await file.text();
    } catch {
      // A file changed on disk after it was picked cannot be read. Emptied, the
      // field takes the same file again as a new pick.
      if (fileInput.current) fileInput.current.value = "";
      return { problem: READ_FAILED };
    }
  }

  async function onCheck(e: FormEvent) {
    e.preventDefault();
    changed();
    if (card === "") {
      setProblem("Pick a card.");
      return;
    }
    const at = round.current;
    setReading(true);
    const raw = await readText();
    setReading(false);
    if (at !== round.current) return;
    if (typeof raw !== "string") {
      refused(raw.problem);
      return;
    }
    const body = fileBody(raw, card, modeSet ? mode : null, startsSet ? startsOn : null);
    if (typeof body === "string") {
      refused(body);
      return;
    }
    preview.mutate(body, {
      onSuccess: (p) => {
        if (at !== round.current) return;
        // Show the start and mode the check used: the file's, unless changed here.
        if (!startsSet && p.starts_on) setStartsOn(p.starts_on);
        if (!modeSet && p.mode) setMode(p.mode);
        setChecked({ body, preview: p });
      },
      onError: (err) => {
        if (at === round.current) refused(failText(err));
      },
    });
  }

  function onApply() {
    if (!clean(checked)) return;
    const at = round.current;
    apply.mutate(
      { ...checked.body, base_sig: checked.preview.base_sig },
      {
        onSuccess: (out) => onSaved(fileSavedText(out.saved)),
        onError: (err) => {
          if (at !== round.current) return;
          if (err instanceof ApiError && RECHECK.includes(err.status)) changed();
          refused(failText(err));
        },
      },
    );
  }

  return (
    <form onSubmit={onCheck} noValidate className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-sm text-fg-secondary">
          Card
          <select
            className={INPUT_CLASS}
            value={card}
            onChange={(e) => {
              changed();
              setListNote(null);
              setCard(e.target.value);
            }}
            disabled={accounts.isPending}
          >
            <option value="">{accounts.isPending ? "Loading cards…" : "Pick a card"}</option>
            {cards.map((c) => (
              <option key={c.key} value={c.key}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {accounts.isError && <p className="m-0 text-sm text-bad">Could not load the cards. Close and try again.</p>}
        <div>
          <Button size="sm" className="w-full justify-center md:w-auto" onClick={download} disabled={template.isPending}>
            {template.isPending ? "Getting the list…" : "Download this card's list"}
          </Button>
          <p className="m-0 mt-1 text-sm text-fg-secondary">Start from it. Change what you need, then check the file.</p>
          <FormStatus problem={listNote} saved={null} />
        </div>
      </div>
      {paste ? (
        <label className="flex flex-col gap-1 text-sm text-fg-secondary">
          File text
          <textarea
            className={TEXTAREA_CLASS}
            spellCheck={false}
            autoComplete="off"
            value={text}
            onChange={(e) => {
              changed();
              setText(e.target.value);
            }}
          />
        </label>
      ) : (
        <label className="flex flex-col gap-1 text-sm text-fg-secondary">
          File
          <input
            ref={fileInput}
            className={`${INPUT_CLASS} py-2`}
            type="file"
            accept=".json,application/json"
            onClick={(e) => {
              // Emptied first, so picking the same file again (fixed on disk) is a new pick.
              e.currentTarget.value = "";
              changed();
            }}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
        </label>
      )}
      <Button
        size="sm"
        variant="ghost"
        className="-mt-2 self-start"
        onClick={() => {
          changed();
          setPaste((v) => !v);
        }}
      >
        {paste ? "Pick a file instead" : "Paste instead"}
      </Button>
      <div className="flex flex-col gap-1">
        <label className="flex flex-col gap-1 text-sm text-fg-secondary">
          Starts on
          <input
            className={INPUT_CLASS}
            type="date"
            value={startsOn}
            onChange={(e) => {
              changed();
              setStartsSet(true);
              setStartsOn(e.target.value);
            }}
            aria-describedby={`${id}-starts-hint`}
          />
        </label>
        <p id={`${id}-starts-hint`} className="m-0 text-sm text-fg-secondary">
          New amounts and new benefits start on this day.
        </p>
      </div>
      <fieldset className="m-0 border-0 p-0">
        <legend className="mb-1 p-0 text-sm text-fg-secondary">What the file holds</legend>
        {MODES.map((m) => (
          <div key={m.key} className="pb-1">
            <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm text-fg md:min-h-9">
              <input
                type="radio"
                name={`${id}-mode`}
                checked={mode === m.key}
                onChange={() => {
                  changed();
                  setModeSet(true);
                  setMode(m.key);
                }}
                aria-describedby={`${id}-${m.key}-hint`}
                className="h-4 w-4 shrink-0 accent-[var(--primary)]"
              />
              {m.label}
            </label>
            <p id={`${id}-${m.key}-hint`} className="m-0 pl-7 text-sm text-fg-secondary">
              {m.hint}
            </p>
          </div>
        ))}
        <p className="m-0 mt-2 text-sm text-fg-secondary">The file's start date and choice are used until you change them here.</p>
      </fieldset>
      <div className="flex flex-col gap-2 md:flex-row">
        <Button ref={checkButton} type="submit" className="w-full justify-center md:w-auto" disabled={busy}>
          {reading || preview.isPending ? "Checking…" : "Check file"}
        </Button>
        <Button variant="primary" className="w-full justify-center md:w-auto" disabled={!clean(checked) || busy} onClick={onApply}>
          {apply.isPending ? "Saving…" : "Apply"}
        </Button>
      </div>
      {!checked && <p className="m-0 -mt-2 text-sm text-fg-secondary">Apply turns on once the file checks out.</p>}
      <FormStatus problem={problem} saved={null} />
      {checked && <FilePreview preview={checked.preview} headingRef={previewHeading} />}
    </form>
  );
}

/**
 * What a checked file does: to add, to change, to end and what stays, or its
 * problems. Focus moves to its heading, so a screen reader reads it next.
 */
function FilePreview({ preview: p, headingRef }: { preview: BenefitFilePreview; headingRef: Ref<HTMLHeadingElement> }) {
  return (
    <section aria-label="What the file does" className="flex flex-col gap-3 border-t border-line pt-4">
      <h3 ref={headingRef} tabIndex={-1} className="m-0 text-base font-medium text-fg outline-none">
        What the file does
      </h3>
      {p.account_name && p.starts_on && (
        <p className="m-0 text-sm text-fg-secondary">
          {p.account_name}, from {formatDate(p.starts_on)}.{p.mode && ` ${MODE_TEXT[p.mode]}.`}
        </p>
      )}
      {p.problems.length > 0 ? (
        <div role="alert" className="flex flex-col gap-2">
          <p className="m-0 flex items-start gap-1.5 text-sm text-bad">
            <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            The file has problems. Fix them, then check it again.
          </p>
          <ul aria-label="Problems" className="m-0 flex list-none flex-col p-0">
            {p.problems.map((x, i) => (
              <li key={`${x.where}-${x.rule}-${i}`} className="border-t border-line py-2 text-sm first:border-t-0">
                <span className="block break-all font-mono text-fg">{x.where}</span>
                <span className="block text-fg-secondary">{x.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : p.no_changes ? (
        <p className="m-0 text-sm text-fg">No changes. The file matches this card's list.</p>
      ) : (
        <>
          <PreviewList
            title="To add"
            items={p.to_add.map((a) => ({
              key: a.key,
              name: a.name,
              detail: [KIND_TEXT[a.kind], a.terms && "with match words"].filter(Boolean).join(" · "),
            }))}
          />
          <PreviewList title="To change" items={p.to_change.map((c) => ({ key: c.key, name: c.name, detail: fileChangeText(c) }))} />
          <PreviewList title="To end" items={p.to_end.map((e) => ({ key: e.key, name: e.name, detail: `Ends ${formatDate(e.ends_on)}` }))} />
          {p.unchanged > 0 && <p className="m-0 text-sm text-fg-secondary">{unchangedText(p.unchanged)}</p>}
        </>
      )}
    </section>
  );
}

/** One preview list, two lines a row. Left out when empty. */
function PreviewList({ title, items }: { title: string; items: { key: string; name: string; detail: string }[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title}>
      <p className="eyebrow m-0">
        {title} ({items.length})
      </p>
      <ul className="m-0 flex list-none flex-col p-0">
        {items.map((r) => (
          <li key={r.key} className="border-t border-line py-2 first:border-t-0">
            <p className="m-0 text-sm font-medium text-fg">{r.name}</p>
            {r.detail && <p className="m-0 text-sm text-fg-secondary">{r.detail}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
