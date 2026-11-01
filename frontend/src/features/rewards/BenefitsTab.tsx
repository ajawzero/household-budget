import { CalendarCheck, Flag, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import type { RewardBenefit, RewardCap, RewardPerk, RewardsResponse } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { formatDate, formatMoney, formatShortDate } from "../../lib/format";
import { EditedBadge } from "../transactions/controls";
import { AddBenefit } from "./AddBenefit";
import { useReview } from "./api";
import { BenefitDrawer, type Gone, OtherBenefitDrawer } from "./BenefitDrawer";
import { BenefitNotices } from "./BenefitNotices";
import { FileUpdate } from "./FileDrawer";
import { HiddenGroup } from "./HiddenGroup";
import { benefitGroups, benefitLine, CYCLE_TEXT, CYCLE_TONE, cycleNotes, GROUP_TITLE, holderColors, matches, pctOf } from "./model";
import { CardPending, MemberHint } from "./parts";
import type { Role } from "./ProgramNotices";
import { ReviewCard, ReviewCredits } from "./ReviewCredits";

type OnEdit = ((key: string) => void) | undefined;

/** Who opens a benefit: an admin edits any one, a member opens a credit for its cycles. */
interface Opener {
  onOpen: (key: string) => void;
  isAdmin: boolean;
}

export function BenefitsTab({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const me = useMe();
  const role: Role = me.data ? (me.data.role === "admin" ? "admin" : "member") : null;
  const isAdmin = role === "admin";
  const [params, setParams] = useSearchParams();
  // ?review=credits opens the review, so Checks and the This month strip can link here.
  const reviewing = params.get("review") === "credits";
  const review = useReview(Boolean(data && data.source_run_id !== null));
  const [editKey, setEditKey] = useState<string | null>(null);
  const [focusNext, setFocusNext] = useState<Gone | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const hiddenRef = useRef<HTMLDivElement>(null);
  const editing = data?.benefits.find((b) => b.key === editKey);
  const cap = data?.caps.find((c) => c.key === editKey);
  const perk = data?.perks.find((p) => p.key === editKey);
  const other = cap
    ? { key: cap.key, name: cap.name, kind: "earning_cap" as const, account_name: cap.account_name, origin: cap.origin }
    : perk
      ? { key: perk.key, name: perk.name, kind: "status_perk" as const, account_name: perk.account_name, origin: perk.origin }
      : undefined;
  // A benefit that left the page (hidden, or gone in a refetch) closes its drawer for good.
  useEffect(() => {
    if (editKey !== null && data && !editing && !other) setEditKey(null);
  }, [editKey, data, editing, other]);
  // Its pencil is gone too, so focus goes to what stays: Hidden after a hide,
  // the list's heading after a delete. A tick later, after the drawer hands
  // focus back.
  useEffect(() => {
    if (focusNext === null || editKey !== null) return;
    const timer = setTimeout(() => {
      if (focusNext === "hidden") {
        hiddenRef.current?.querySelector<HTMLElement>("button")?.focus();
      } else {
        const heading = listRef.current?.querySelector<HTMLElement>("h2");
        if (heading) {
          heading.tabIndex = -1;
          heading.focus();
        }
      }
      setFocusNext(null);
    }, 0);
    return () => clearTimeout(timer);
  }, [focusNext, editKey]);
  function gone(why: Gone) {
    setFocusNext(why);
    setEditKey(null);
  }
  const close = (open: boolean) => !open && setEditKey(null);
  const onEdit = isAdmin ? setEditKey : undefined;
  const opener: Opener | undefined = role === null ? undefined : { onOpen: setEditKey, isAdmin };
  function setReviewing(open: boolean) {
    setParams(
      (p) => {
        if (open) p.set("review", "credits");
        else p.delete("review");
        return p;
      },
      { replace: true },
    );
  }
  // From the review to a benefit's drawer: one drawer at a time.
  function openBenefit(key: string) {
    setReviewing(false);
    setEditKey(key);
  }
  return (
    <>
      {data && <BenefitNotices data={data} role={role} />}
      {data && <ReviewCard review={review.data} onOpen={() => setReviewing(true)} />}
      <div ref={listRef} className="contents">
        <UseOrLoseCard benefits={data?.benefits} loading={loading} error={error} opener={opener} />
      </div>
      <CapsCard caps={data?.caps} loading={loading} error={error} onEdit={onEdit} />
      {data && data.perks.length > 0 && <PerksCard perks={data.perks} onEdit={onEdit} />}
      <div ref={hiddenRef} className="contents">
        {data && <HiddenGroup hidden={data.hidden} isAdmin={isAdmin} />}
      </div>
      {data && role !== null && (
        // At the bottom of the tab. Full width and stacked on phones.
        <div className="flex flex-col gap-2 md:flex-row md:justify-end">
          {isAdmin ? (
            <>
              <AddBenefit programs={data.programs} />
              <FileUpdate today={data.today} />
            </>
          ) : (
            <MemberHint>Only an admin can change benefits.</MemberHint>
          )}
        </div>
      )}
      {editing && data && (isAdmin || editing.kind === "credit") && (
        <BenefitDrawer benefit={editing} today={data.today} open onOpenChange={close} onGone={gone} isAdmin={isAdmin} />
      )}
      {other && isAdmin && <OtherBenefitDrawer item={other} open onOpenChange={close} onGone={gone} />}
      {data && data.source_run_id !== null && (
        <ReviewCredits data={data} review={review} open={reviewing} onOpenChange={setReviewing} isAdmin={isAdmin} onOpenBenefit={openBenefit} />
      )}
    </>
  );
}

/** The pencil on a benefit, for an admin. 44px on phones. */
function EditButton({ b, onEdit }: { b: { key: string; name: string }; onEdit: (key: string) => void }) {
  return (
    <Button size="icon" variant="ghost" className="-my-2 -mr-2 md:-my-1" aria-label={`Edit ${b.name}`} onClick={() => onEdit(b.key)}>
      <Pencil size={16} aria-hidden="true" />
    </Button>
  );
}

/** An admin opens any benefit, a member a credit only (for its cycles). */
function opens(b: RewardBenefit, opener: Opener | undefined): opener is Opener {
  return opener !== undefined && (opener.isAdmin || b.kind === "credit");
}

/** The pencil for an admin, or for a member a credit's cycles button. Nothing else for a member. */
function OpenButton({ b, opener }: { b: RewardBenefit; opener: Opener | undefined }) {
  if (!opens(b, opener)) return null;
  if (opener.isAdmin) return <EditButton b={b} onEdit={opener.onOpen} />;
  return (
    <Button size="icon" variant="ghost" className="-my-2 -mr-2 md:-my-1" aria-label={`Cycles of ${b.name}`} onClick={() => opener.onOpen(b.key)}>
      <CalendarCheck size={16} aria-hidden="true" />
    </Button>
  );
}

/** "Use-or-lose benefits": open cycles by end date, then auto-loads, then used this cycle. */
function UseOrLoseCard({
  benefits,
  loading,
  error,
  opener,
}: {
  benefits: RewardBenefit[] | undefined;
  loading: boolean;
  error: string | null;
  opener: Opener | undefined;
}) {
  const [showPast, setShowPast] = useState(false);
  const groups = benefitGroups(benefits ?? []);
  const past = (benefits ?? []).filter((b) => b.past_cycles.length > 0 || b.group === "ended");
  return (
    <Card>
      <CardHeader title="Use-or-lose benefits" subtitle="Card credits and free nights, soonest deadline first." />
      {!benefits ? (
        <CardPending loading={loading} error={error} rows={4} />
      ) : groups.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No current benefits.</p>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => (
            <section key={g.group} aria-label={GROUP_TITLE[g.group]}>
              <h3 className="m-0 mb-1 text-sm font-medium text-fg-secondary">{GROUP_TITLE[g.group]}</h3>
              <ul className="m-0 flex list-none flex-col p-0">
                {g.items.map((b) => (
                  <BenefitRow key={b.key} b={b} opener={opener} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {past.length > 0 && (
        <div className="mt-3 border-t border-line pt-2">
          <button
            type="button"
            className="inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
            aria-expanded={showPast}
            onClick={() => setShowPast((v) => !v)}
          >
            {showPast ? "Hide past cycles" : "Show past cycles"}
          </button>
          {showPast && <PastCycles benefits={past} opener={opener} />}
        </div>
      )}
    </Card>
  );
}

function BenefitRow({ b, opener }: { b: RewardBenefit; opener: Opener | undefined }) {
  const c = b.cycle;
  const state = c && b.group !== "upcoming";
  const notes = state ? cycleNotes(c, matches(b)) : [];
  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 border-t border-line py-3 first:border-t-0">
      <div className="min-w-0 flex-1">
        <p className="m-0 flex flex-wrap items-center gap-2 font-medium text-fg">
          <span>{b.name}</span>
          {b.auto && <Badge>Auto</Badge>}
          {b.kind === "free_night" && <Badge>Free night</Badge>}
          {b.origin === "app" && <Badge>Added in the app</Badge>}
          {b.edited_fields.length > 0 && <EditedBadge />}
        </p>
        <p className="m-0 text-sm text-fg-secondary">
          {b.account_name}
          {b.program_name && ` · ${b.program_name}`}
        </p>
        <p className="m-0 text-sm text-fg">{benefitLine(b)}</p>
        {notes.length > 0 && <p className="m-0 text-sm text-fg-secondary">{notes.join(" · ")}</p>}
        {b.enroll_flag && b.enroll_by && (
          <p className="m-0 mt-1 flex items-center gap-1.5 text-sm text-warn">
            <Flag size={14} aria-hidden="true" />
            Not enrolled. Enroll by {formatDate(b.enroll_by)}.
          </p>
        )}
        {b.how_to_use && <p className="m-0 text-sm text-fg-secondary">{b.how_to_use}</p>}
        {b.note && <p className="m-0 text-sm text-fg-secondary">{b.note}</p>}
      </div>
      {(state || opens(b, opener)) && (
        <span className="flex shrink-0 items-start gap-1">
          {state && (
            <Badge tone={CYCLE_TONE[c.state]} className="mt-0.5">
              {CYCLE_TEXT[c.state]}
            </Badge>
          )}
          <OpenButton b={b} opener={opener} />
        </span>
      )}
    </li>
  );
}

/**
 * Every cycle before today, per benefit. Expired cycles are gray, never red.
 * An ended benefit has no row above, so its pencil (or a member's cycles
 * button) is here.
 */
function PastCycles({ benefits, opener }: { benefits: RewardBenefit[]; opener: Opener | undefined }) {
  return (
    <div className="mt-2 flex flex-col gap-3">
      {benefits.map((b) => (
        <section key={b.key} aria-label={`${b.name} past cycles`}>
          <div className="flex items-center justify-between gap-2">
            <h4 className="m-0 text-sm font-medium text-fg">{b.name}</h4>
            {b.group === "ended" && <OpenButton b={b} opener={opener} />}
          </div>
          {b.past_cycles.length === 0 ? (
            <p className="m-0 text-sm text-fg-secondary">Ended {b.ends_on ? formatShortDate(b.ends_on) : ""}</p>
          ) : (
            <ul className="m-0 flex list-none flex-col p-0">
              {b.past_cycles.map((c) => (
                <li key={c.start} className="flex items-center justify-between gap-3 py-1 text-sm">
                  <span className="text-fg-secondary">
                    {formatShortDate(c.start)} to {formatShortDate(c.end)}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="tabular text-fg">
                      {formatMoney(c.used_cents, { cents: true })} of {formatMoney(c.amount_cents, { cents: true })}
                    </span>
                    <Badge tone={CYCLE_TONE[c.state]}>{CYCLE_TEXT[c.state]}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

interface CapRow {
  key: string;
  name: string;
  spent: number;
  cap: number;
  left: number;
  chat: number | null;
  chatAsOf: string | null;
}

const CAP_COLUMNS: ChartColumn<CapRow>[] = [
  { header: "Cap", cell: (r) => r.name },
  { header: "Spent (rows)", cell: (r) => formatMoney(r.spent), numeric: true },
  { header: "Cap", cell: (r) => formatMoney(r.cap), numeric: true },
  { header: "Left", cell: (r) => formatMoney(r.left), numeric: true },
  {
    header: "The chat's number",
    cell: (r) => (r.chat === null ? "None" : `${formatMoney(r.chat)}${r.chatAsOf ? ` as of ${formatShortDate(r.chatAsOf)}` : ""}`),
    numeric: true,
  },
];

/** Earning caps: year-to-date spend from rows vs the cap, split by cardholder. An admin can hide one. */
function CapsCard({ caps, loading, error, onEdit }: { caps: RewardCap[] | undefined; loading: boolean; error: string | null; onEdit: OnEdit }) {
  const rows: CapRow[] | undefined = caps?.map((c) => ({
    key: c.key,
    name: `${c.name}, ${c.account_name ?? c.account_key}`,
    spent: c.spent_cents,
    cap: c.cap_cents,
    left: c.left_cents,
    chat: c.reported_spent_cents,
    chatAsOf: c.reported_as_of,
  }));
  const first = caps?.[0];
  return (
    <ChartCard<CapRow>
      title="Earning caps"
      subtitle="Spend this year from the card's rows in the cap's categories."
      ariaLabel={first ? `${first.name}: ${formatMoney(first.spent_cents)} of ${formatMoney(first.cap_cents)} spent.` : "No earning caps."}
      data={rows}
      columns={CAP_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption="Earning caps, the app's rows and the chat's number"
      loading={loading}
      error={error}
      quietError
      emptyText="No earning caps."
      height={80}
    >
      <ul className="m-0 flex list-none flex-col gap-5 p-0">
        {(caps ?? []).map((c) => (
          <CapBar key={c.key} c={c} onEdit={onEdit} />
        ))}
      </ul>
    </ChartCard>
  );
}

function CapBar({ c, onEdit }: { c: RewardCap; onEdit: OnEdit }) {
  const colors = holderColors(c.by_cardholder.map((h) => h.person_key));
  const earn = c.earn_before && c.earn_after ? `${c.earn_before} up to the cap, then ${c.earn_after}` : null;
  return (
    <li>
      <div className="flex items-start justify-between gap-2">
        <p className="m-0 flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-2">
          <span className="font-medium text-fg">
            {c.name} <span className="font-normal text-fg-secondary">· {c.account_name}</span>
          </span>
          <span className="text-sm text-fg">
            {formatMoney(c.spent_cents)} of {formatMoney(c.cap_cents)}
          </span>
        </p>
        {onEdit && <EditButton b={c} onEdit={onEdit} />}
      </div>
      <p className="m-0 text-sm text-fg-secondary">
        {c.categories.map((x) => x.name).join(", ")}
        {earn && ` · ${earn}`}
      </p>
      <div
        className="mt-2 flex h-3 gap-[2px] overflow-hidden rounded-full bg-subtle"
        aria-hidden="true"
      >
        {c.by_cardholder.map((h) => (
          <span key={h.person_key ?? "none"} style={{ width: `${pctOf(h.cents, c.cap_cents)}%`, background: colors.get(h.person_key) }} />
        ))}
      </div>
      <ul className="m-0 mt-1.5 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm">
        {c.by_cardholder.map((h) => (
          <li key={h.person_key ?? "none"} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: colors.get(h.person_key) }} />
            <span className="text-fg-secondary">{h.name ?? "No cardholder"}</span>
            <span className="tabular text-fg">{formatMoney(h.cents)}</span>
          </li>
        ))}
        <li className="text-fg-secondary">{formatMoney(c.left_cents)} left</li>
      </ul>
      {c.rows_from && c.rows_from > `${c.year}-01-01` && (
        <p className="m-0 mt-1 text-sm text-fg-secondary">The card's rows start {formatShortDate(c.rows_from)}.</p>
      )}
      {c.reported_differs && c.reported_spent_cents !== null && (
        <p className="m-0 mt-1 text-sm text-fg-secondary">
          The chat counts {formatMoney(c.reported_spent_cents)}
          {c.reported_as_of && ` through ${formatShortDate(c.reported_as_of)}`}.
        </p>
      )}
    </li>
  );
}

function PerksCard({ perks, onEdit }: { perks: RewardPerk[]; onEdit: OnEdit }) {
  return (
    <Card>
      <CardHeader title="Status perks" />
      <ul className="m-0 flex list-none flex-col p-0">
        {perks.map((p) => (
          <li key={p.key} className="flex flex-wrap items-start justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
            <div className="min-w-0">
              <p className="m-0 font-medium text-fg">{p.name}</p>
              <p className="m-0 text-sm text-fg-secondary">
                {p.program_name}
                {p.account_name && ` · through the ${p.account_name}`}
              </p>
              {p.note && <p className="m-0 text-sm text-fg-secondary">{p.note}</p>}
            </div>
            {(p.status_tier || onEdit) && (
              <span className="flex max-w-full shrink-0 items-start gap-1">
                {/* Badge look, but a long tier from the chat wraps instead of widening the page. */}
                {p.status_tier && (
                  <span className="mt-0.5 inline-flex min-h-5 min-w-0 items-center break-words rounded-[10px] bg-accent-tint px-2 py-0.5 text-xs font-medium text-accent-text">
                    {p.status_tier}
                  </span>
                )}
                {onEdit && <EditButton b={p} onEdit={onEdit} />}
              </span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
