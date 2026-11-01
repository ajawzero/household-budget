import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Plane } from "lucide-react";
import { Link } from "react-router";
import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";
import { Money } from "../../components/money/Money";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import type { CategoriesResponse } from "../../lib/api";
import { formatDate, formatTimestamp } from "../../lib/format";
import { getEvents, invalidateAfterEdit, patchTransaction, type TxnPatch, type TxnRow, txnKeys } from "./api";
import { CategorySelect, EditedBadge, INPUT, SwitchRow } from "./controls";
import { categoryIndex, displayName, eventBy, eventText, parseTags, personLabel } from "./model";
import { RulePrompt } from "./RulePrompt";
import { ReviewBadge } from "./TxnList";

interface TxnDrawerProps {
  row: TxnRow | null;
  categories: CategoriesResponse | undefined;
  onClose: () => void;
  /** The server's row after an edit. */
  onUpdated: (row: TxnRow) => void;
}

type Section = "category" | "toggles" | "details";

/**
 * Transaction detail (PLAN 8.2): statement text, category, the four
 * switches, merchant, notes and tags, and the activity timeline. Category and
 * switches save at once. Merchant, notes and tags save with one button.
 */
export function TxnDrawer({ row, categories, onClose, onUpdated }: TxnDrawerProps) {
  return (
    <Drawer
      open={row !== null}
      onOpenChange={(o) => !o && onClose()}
      title={row ? displayName(row) : "Transaction"}
      widthClass="md:w-[520px]"
    >
      {row && <DrawerBody key={row.txn_key} row={row} categories={categories} onUpdated={onUpdated} />}
    </Drawer>
  );
}

function DrawerBody({ row, categories, onUpdated }: { row: TxnRow; categories: CategoriesResponse | undefined; onUpdated: (row: TxnRow) => void }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<Section | null>(null);
  const [error, setError] = useState<{ section: Section; message: string } | null>(null);
  const [rulePrompt, setRulePrompt] = useState<string | null>(null);
  const [saved, setSaved] = useState<Section | null>(null);
  const index = categoryIndex(categories);
  const categoryId = useId();
  const overridden = new Set(row.overridden);

  async function save(changes: TxnPatch, section: Section): Promise<TxnRow | null> {
    setBusy(section);
    setError(null);
    setSaved(null);
    try {
      const updated = await patchTransaction(row.txn_key, changes);
      onUpdated(updated);
      setSaved(section);
      void invalidateAfterEdit(queryClient);
      return updated;
    } catch (err) {
      setError({ section, message: err instanceof Error ? err.message : "Could not save. Try again." });
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function changeCategory(key: string) {
    if (!key || key === row.category_key) return;
    const updated = await save({ category_key: key }, "category");
    if (updated && updated.category_key === key) setRulePrompt(key);
  }

  const promptInfo = rulePrompt ? index.get(rulePrompt) : undefined;
  const sourceText =
    row.category_source === "override" ? null : row.category_source === "rule" ? "Set by a rule" : "From the import";

  return (
    <div className="flex flex-col gap-6 pb-4">
      <div>
        <p className="m-0 text-2xl font-semibold">
          <Money cents={row.amount_cents} withCents flow proportional />
        </p>
        <p className="m-0 mt-1 text-sm text-fg-secondary">
          {[formatDate(row.txn_date), row.account_name, personLabel(row)].filter(Boolean).join(" · ")}
        </p>
        {row.post_date && row.post_date !== row.txn_date && (
          <p className="m-0 text-xs text-fg-secondary">Posted {formatDate(row.post_date)}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {row.needs_review && <ReviewBadge />}
          {row.has_override && <EditedBadge />}
          {row.pre_history && <span className="text-xs text-fg-secondary">Before history starts</span>}
          {row.trip_key && <TripChip tripKey={row.trip_key} name={row.trip_name} />}
        </div>
      </div>

      <Block title="Statement text">
        <p className="m-0 break-words font-mono text-sm text-fg">{row.description}</p>
        {row.imported_merchant && row.imported_merchant !== row.merchant && (
          <p className="m-0 mt-1 text-sm text-fg-secondary">Imported merchant: {row.imported_merchant}</p>
        )}
        {row.import_notes && <p className="m-0 mt-1 text-sm text-fg-secondary">Note from the import: {row.import_notes}</p>}
      </Block>

      <Block title="Category" labelFor={categoryId}>
        <CategorySelect
          id={categoryId}
          value={row.category_key}
          categories={categories}
          onChange={(k) => void changeCategory(k)}
          disabled={busy === "category"}
        />
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-secondary">
          {sourceText ? <span>{sourceText}</span> : <EditedBadge />}
          {overridden.has("category_key") && (
            <button
              type="button"
              className="min-h-11 text-accent-text underline md:min-h-0"
              onClick={() => {
                setRulePrompt(null);
                void save({ category_key: null }, "category");
              }}
            >
              Use imported{" "}<span className="sr-only">category</span>
            </button>
          )}
          {saved === "category" && <span role="status">Saved.</span>}
        </div>
        <SectionError error={error} section="category" />
        {rulePrompt && promptInfo && (
          <div className="mt-3">
            <RulePrompt
              row={row}
              categoryKey={rulePrompt}
              categoryName={promptInfo.name}
              categoryEmoji={promptInfo.emoji}
              onDone={() => setRulePrompt(null)}
            />
          </div>
        )}
      </Block>

      <Block title="Treat as">
        <SwitchRow
          label="Transfer"
          hint="Money moving between our accounts. Left out of spending and income."
          checked={row.is_transfer}
          disabled={busy === "toggles"}
          onChange={(v) => void save({ is_transfer: v }, "toggles")}
          edited={overridden.has("is_transfer")}
          onReset={() => void save({ is_transfer: null }, "toggles")}
        />
        <SwitchRow
          label="Reimbursable"
          hint="Someone will pay this back."
          checked={row.is_reimbursable}
          disabled={busy === "toggles"}
          onChange={(v) => void save({ is_reimbursable: v }, "toggles")}
          edited={overridden.has("is_reimbursable")}
          onReset={() => void save({ is_reimbursable: null }, "toggles")}
        />
        <SwitchRow
          label="Exclude from budget"
          checked={row.exclude_from_budget}
          disabled={busy === "toggles"}
          onChange={(v) => void save({ exclude_from_budget: v }, "toggles")}
          edited={overridden.has("exclude_from_budget")}
          onReset={() => void save({ exclude_from_budget: null }, "toggles")}
        />
        <SwitchRow
          label="Hide"
          hint="Hidden rows leave the list unless a filter asks for them."
          checked={row.hidden}
          disabled={busy === "toggles"}
          onChange={(v) => void save({ hidden: v }, "toggles")}
          edited={overridden.has("hidden")}
          onReset={() => void save({ hidden: null }, "toggles")}
        />
        <SectionError error={error} section="toggles" />
      </Block>

      <DetailsForm row={row} busy={busy === "details"} saved={saved === "details"} onSave={(c) => void save(c, "details")}>
        <SectionError error={error} section="details" />
      </DetailsForm>

      <Activity row={row} index={index} />
    </div>
  );
}

function Block({ title, labelFor, children }: { title: string; labelFor?: string; children: ReactNode }) {
  const Tag = labelFor ? "label" : "h3";
  return (
    <section>
      <Tag htmlFor={labelFor} className="mb-2 block text-xs font-medium uppercase tracking-wider text-fg-secondary">
        {title}
      </Tag>
      {children}
    </section>
  );
}

function SectionError({ error, section }: { error: { section: Section; message: string } | null; section: Section }) {
  if (!error || error.section !== section) return null;
  return (
    <p role="alert" className="m-0 mt-2 text-sm text-bad">
      {error.message}
    </p>
  );
}

function DetailsForm({
  row,
  busy,
  saved,
  onSave,
  children,
}: {
  row: TxnRow;
  busy: boolean;
  saved: boolean;
  onSave: (changes: TxnPatch) => void;
  children: ReactNode;
}) {
  const [merchant, setMerchant] = useState(row.merchant ?? "");
  const [notes, setNotes] = useState(row.notes ?? "");
  const [tags, setTags] = useState(row.tags.join(", "));
  const ids = { merchant: useId(), notes: useId(), tags: useId(), tagsHint: useId() };

  // A save (or a reset) brings new values from the server. A switch change keeps the draft.
  const savedTags = row.tags.join(", ");
  useEffect(() => {
    setMerchant(row.merchant ?? "");
    setNotes(row.notes ?? "");
    setTags(savedTags);
  }, [row.merchant, row.notes, savedTags]);

  const changes: TxnPatch = {};
  if (merchant.trim() !== (row.merchant ?? "")) changes.merchant = merchant.trim() || null;
  if (notes.trim() !== (row.notes ?? "")) changes.notes = notes.trim() || null;
  const tagList = parseTags(tags);
  if (tagList.join("\n") !== row.tags.join("\n")) changes.tags = tagList.length ? tagList : null;
  const dirty = Object.keys(changes).length > 0;
  const tooManyTags = tagList.length > 10 || tagList.some((t) => t.length > 32);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (dirty && !tooManyTags) onSave(changes);
  }

  return (
    <form onSubmit={submit} aria-label="Merchant, notes and tags">
      <h3 className="m-0 mb-2 text-xs font-medium uppercase tracking-wider text-fg-secondary">Details</h3>
      <div className="flex flex-col gap-3">
        <div>
          <label htmlFor={ids.merchant} className="mb-1 block text-sm text-fg">
            Merchant
          </label>
          <input
            id={ids.merchant}
            className={INPUT}
            value={merchant}
            maxLength={64}
            placeholder={row.imported_merchant ?? "No merchant"}
            onChange={(e) => setMerchant(e.target.value)}
          />
          {row.overridden.includes("merchant") && (
            <button
              type="button"
              className="mt-1 min-h-11 text-xs text-accent-text underline md:min-h-0"
              onClick={() => onSave({ merchant: null })}
            >
              Use imported{" "}<span className="sr-only">merchant</span>
            </button>
          )}
        </div>
        <div>
          <label htmlFor={ids.notes} className="mb-1 block text-sm text-fg">
            Notes
          </label>
          <textarea
            id={ids.notes}
            className={`${INPUT} h-auto min-h-20 py-2`}
            value={notes}
            maxLength={500}
            rows={3}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor={ids.tags} className="mb-1 block text-sm text-fg">
            Tags
          </label>
          <input
            id={ids.tags}
            className={INPUT}
            value={tags}
            aria-describedby={ids.tagsHint}
            aria-invalid={tooManyTags || undefined}
            onChange={(e) => setTags(e.target.value)}
          />
          {tooManyTags ? (
            <p id={ids.tagsHint} className="m-0 mt-1 flex items-start gap-1 text-xs text-bad">
              <CircleAlert size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
              {tagList.length > 10 ? `Too many tags: ${tagList.length} of 10.` : "A tag is over 32 letters."}
            </p>
          ) : (
            <p id={ids.tagsHint} className="m-0 mt-1 text-xs text-fg-secondary">
              Separate tags with commas. Up to 10, each up to 32 letters.
            </p>
          )}
          {tagList.length > 0 && (
            <ul className="m-0 mt-2 flex list-none flex-wrap gap-1 p-0" aria-label="Tags">
              {tagList.map((t) => (
                <li key={t} className="rounded-full bg-subtle px-2 py-0.5 text-xs text-fg">
                  {t}
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="m-0 text-xs text-fg-secondary">Do not type account numbers, phone numbers, emails or addresses.</p>
        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" disabled={!dirty || busy || tooManyTags}>
            {busy ? "Saving" : "Save"}
          </Button>
          {saved && !dirty && (
            <span role="status" className="text-sm text-fg-secondary">
              Saved.
            </span>
          )}
        </div>
        {children}
      </div>
    </form>
  );
}

function Activity({ row, index }: { row: TxnRow; index: ReturnType<typeof categoryIndex> }) {
  const events = useQuery({ queryKey: txnKeys.events(row.txn_key), queryFn: () => getEvents(row.txn_key) });
  const name = (key: string) => {
    const c = index.get(key);
    return c ? (c.emoji ? `${c.emoji} ${c.name}` : c.name) : key;
  };
  return (
    <section>
      <h3 className="m-0 mb-2 text-xs font-medium uppercase tracking-wider text-fg-secondary">Activity</h3>
      {events.isPending && <p className="m-0 text-sm text-fg-secondary">Loading…</p>}
      {events.isError && <p className="m-0 text-sm text-bad">{events.error.message}</p>}
      {events.data && (
        <ol className="m-0 list-none border-l border-line p-0 pl-4">
          {events.data.events.map((e, i) => {
            const by = eventBy(e);
            return (
              <li key={e.id ?? `synth-${i}`} className="relative pb-3 last:pb-0">
                <span aria-hidden="true" className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-line-strong" />
                <p className="m-0 text-sm text-fg">{eventText(e, name)}</p>
                <p className="m-0 text-xs text-fg-secondary">{[formatTimestamp(e.at), by].filter(Boolean).join(" · ")}</p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

/** The row's trip. Opens it on Travel; a trip the app does not have shows its key. */
function TripChip({ tripKey, name }: { tripKey: string; name: string | null }) {
  const cls = "inline-flex min-h-11 items-center gap-1 rounded-full border border-line px-2.5 text-xs md:min-h-6";
  if (!name) {
    return (
      <span className={cls + " text-fg-secondary"} title="No trip with this key in Travel">
        <Plane size={12} aria-hidden="true" />
        Trip {tripKey} (not in Travel)
      </span>
    );
  }
  return (
    <Link to={`/travel?trip=${encodeURIComponent(tripKey)}`} aria-label={`Trip: ${name}`} className={cls + " text-fg no-underline hover:bg-hover"}>
      <Plane size={12} aria-hidden="true" />
      {name}
    </Link>
  );
}
