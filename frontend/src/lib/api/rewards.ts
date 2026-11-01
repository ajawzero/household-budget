// Rewards (format 1.3, docs/plans/rewards-inputs.md section 6): the chat's
// newest monthly run with the app's inputs on top. Types come from the
// generated schema.ts. index.ts re-exports this file. Points are integer
// micro-units (1 point = 1,000,000). Balances go up as strings, like
// "5200" or "37.42".

import { query, request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

export type RewardsResponse = S["RewardsOut"];
export type RewardTiles = S["RewardTiles"];
export type RewardDeadline = S["Deadline"];
export type RewardProgram = S["ProgramOut"];
export type RewardTimeshareYear = S["TimeshareYear"];
export type RewardValueRow = S["ValueRow"];
export type RewardRedemption = S["RedemptionOut"];
export type RewardBenefit = S["BenefitOut"];
export type RewardCycle = S["CycleOut"];
export type RewardCycleState = RewardCycle["state"];
export type RewardCap = S["CapOut"];
export type RewardPerk = S["PerkOut"];
export type RewardMilestone = S["MilestoneOut"];
export type RewardMonthSpend = S["MonthSpend"];
export type RewardRoute = S["RouteOut"];
export type RewardFees = S["FeesOut"];
export type RewardFeeRow = S["FeeRow"];
export type CreditsEnding = S["CreditsEnding"];
export type RewardsCheck = S["RewardsCheck"];
export type RewardGap = S["RewardGap"];
export type RewardNotice = S["NoticeOut"];
export type RewardOrphan = S["OrphanOut"];
export type BalanceEntryIn = S["BalanceEntryIn"];
export type BalancesSaved = S["BalancesSavedOut"];
export type BalanceHistory = S["BalanceHistoryOut"];
export type BalanceEntry = S["BalanceEntryOut"];
export type ProgramIn = S["ProgramIn"];
export type RewardHiddenBenefit = S["HiddenBenefitOut"];
export type BenefitIn = S["BenefitIn"];
export type BenefitCycleIn = S["BenefitCycleIn"];
export type BenefitSetIn = S["BenefitSetIn"];
export type BenefitPatchIn = S["BenefitPatchIn"];
/** The benefit fields an edit can change. Key, card, kind, cadence and cycles never change. */
export type BenefitField = S["ResolveIn"]["field"];
export type ResolveChoice = S["ResolveIn"]["choice"];
export type RewardCycleRow = S["CycleRowOut"];
export type RewardCycleSource = NonNullable<RewardCycle["source"]>;
export type ReviewCounts = S["ReviewCountsOut"];
export type RewardsReview = S["RewardsReviewOut"];
export type ReviewRow = S["ReviewRowOut"];
export type ReviewGuess = S["GuessOut"];
export type NeedsLook = S["NeedsLookOut"];
export type RewardTerms = S["TermsOut"];
export type TermPreview = S["TermPreviewOut"];
export type MarkIn = S["MarkIn"];
export type CreditIn = S["CreditIn"];
export type CreditChoice = S["CreditChoiceOut"];
export type CreditReason = NonNullable<CreditIn["reason"]>;
export type BenefitFile = S["BenefitFileOut"];
export type BenefitFilePreview = S["BenefitFilePreviewOut"];
export type BenefitFileSaved = S["BenefitFileSavedOut"];
export type FileProblem = S["FileProblemOut"];
export type FileAdd = S["FileAddOut"];
export type FileChange = S["FileChangeOut"];
export type FileEnd = S["FileEndOut"];
/** merge: only the benefits that change. replace: every benefit on the card. */
export type FileMode = BenefitFile["mode"];
/**
 * A benefit file as read in the browser, with the drawer's card on top, and
 * its mode and start once changed there. The API checks the rest and never
 * echoes a value.
 */
export type BenefitFileBody = Record<string, unknown> & { account_key: string; mode: FileMode; starts_on?: string };
export type BenefitFileApplyBody = BenefitFileBody & { base_sig: string };

export function getRewards(): Promise<RewardsResponse> {
  return request("GET", "/api/rewards");
}

/** Any signed-in user. 1 to 50 entries, one per program per day. */
export function addBalances(entries: BalanceEntryIn[]): Promise<BalancesSaved> {
  return request("POST", "/api/rewards/balances", { body: { entries } });
}

/** Any signed-in user. Gives back the program's history without the entry. */
export function deleteBalance(id: number): Promise<BalanceHistory> {
  return request("DELETE", `/api/rewards/balances/${id}`);
}

export function getBalanceHistory(programKey: string): Promise<BalanceHistory> {
  return request("GET", `/api/rewards/programs/${encodeURIComponent(programKey)}/balances`);
}

/** Admin only. The key is made from the name ("app-..."). */
export function addProgram(body: ProgramIn): Promise<RewardProgram> {
  return request("POST", "/api/rewards/programs", { body });
}

/**
 * Admin only. An app program goes with its balances, unless the chat sends
 * that key too ("Use the chat's"). With a unit ("Delete balances" in Needs a
 * look), only the key's balances kept but not shown in that unit go. A 409
 * when none are left in it: the page changed since it loaded.
 */
export function deleteProgram(key: string, unit?: S["BalanceEntryOut"]["unit"]): Promise<void> {
  return request("DELETE", `/api/rewards/programs/${encodeURIComponent(key)}${query({ unit })}`);
}

/** Admin only. Moves a key's balances to another program. An app program is then deleted. */
export function moveBalances(key: string, toKey: string): Promise<RewardProgram> {
  return request("POST", `/api/rewards/programs/${encodeURIComponent(key)}/move`, { body: { to_key: toKey } });
}

/** Admin only. An app program and the chat's program with the same name both stay. */
export function keepBothPrograms(key: string, otherKey: string): Promise<RewardProgram> {
  return request("POST", `/api/rewards/programs/${encodeURIComponent(key)}/keep-both`, { body: { other_key: otherKey } });
}

/**
 * Admin only. A benefit added in the app, key "app-..." from the name.
 * Calendar cadences get this year's cycles from the app. Multi-year and
 * one-time credits send theirs.
 */
export function addBenefit(body: BenefitIn): Promise<RewardBenefit> {
  return request("POST", "/api/rewards/benefits", { body });
}

/**
 * Admin only. `set` changes fields (null clears one), `reset` goes back to
 * the chat's, `hidden` hides or shows again. `amount_from` says when a new
 * amount starts. Gives back the benefit, hidden or not.
 */
export function patchBenefit(key: string, body: BenefitPatchIn): Promise<RewardBenefit> {
  return request("PATCH", `/api/rewards/benefits/${encodeURIComponent(key)}`, { body });
}

/** Admin only. The chat changed an edited field: keep mine, or use the chat's. */
export function resolveBenefit(key: string, field: BenefitField, choice: ResolveChoice): Promise<RewardBenefit> {
  return request("POST", `/api/rewards/benefits/${encodeURIComponent(key)}/resolve`, { body: { field, choice } });
}

/** Admin only. Use the chat's for every field. Hide stays. */
export function resetBenefit(key: string): Promise<RewardBenefit> {
  return request("DELETE", `/api/rewards/benefits/${encodeURIComponent(key)}/edits`);
}

/**
 * Admin only. A benefit added in the app, or everything kept on a key the
 * chat no longer sends. When the chat sends that key too, the chat's is shown.
 */
export function deleteBenefit(key: string): Promise<void> {
  return request("DELETE", `/api/rewards/benefits/${encodeURIComponent(key)}`);
}

/** Admin only. Moves what a key holds to another benefit on that card, of that kind. An app benefit is then deleted. */
export function moveBenefit(key: string, toKey: string): Promise<RewardBenefit> {
  return request("POST", `/api/rewards/benefits/${encodeURIComponent(key)}/move`, { body: { to_key: toKey } });
}

/** Admin only. An app benefit and the chat's benefit like it both stay. */
export function keepBothBenefits(key: string, otherKey: string): Promise<RewardBenefit> {
  return request("POST", `/api/rewards/benefits/${encodeURIComponent(key)}/keep-both`, { body: { other_key: otherKey } });
}

/**
 * Card credits to review, the returns and trip groups, and what needs a look
 * (lost choices, rows no cycle holds, the chat's used with no row). Any
 * signed-in user.
 */
export function getReview(): Promise<RewardsReview> {
  return request("GET", "/api/rewards/review");
}

/** A credit's saved, suggested and seen match words. Any signed-in user. */
export function getTerms(key: string): Promise<RewardTerms> {
  return request("GET", `/api/rewards/benefits/${encodeURIComponent(key)}/terms`);
}

/** Admin only. Every match word for the credit (0 to 10). They replace the saved ones. */
export function saveTerms(key: string, terms: string[]): Promise<RewardTerms> {
  return request("PUT", `/api/rewards/benefits/${encodeURIComponent(key)}/terms`, { body: { terms } });
}

/** Admin only. What a match word would do, with nothing saved. The word goes in the body, never the URL. */
export function previewTerm(key: string, term: string): Promise<TermPreview> {
  return request("POST", "/api/rewards/terms/preview", { body: { benefit_key: key, term } });
}

/** Any signed-in user. "Mark used" sends the amount, "Mark not used" sends "0.00". Gives back the benefit. */
export function setMark(key: string, start: string, body: MarkIn): Promise<RewardBenefit> {
  return request("PUT", `/api/rewards/benefits/${encodeURIComponent(key)}/cycles/${encodeURIComponent(start)}/mark`, { body });
}

/** Any signed-in user. The cycle goes back to the rows or the chat. Gives back the benefit. */
export function clearMark(key: string, start: string): Promise<RewardBenefit> {
  return request("DELETE", `/api/rewards/benefits/${encodeURIComponent(key)}/cycles/${encodeURIComponent(start)}/mark`);
}

/** Any signed-in user. "Count it", "Different benefit" or "Not a benefit" on one card credit row. */
export function setCredit(txnKey: string, body: CreditIn): Promise<CreditChoice> {
  return request("PUT", `/api/rewards/credits/${encodeURIComponent(txnKey)}`, { body });
}

/** Any signed-in user. Clears the choice on a row, or a choice that lost its row. */
export function clearCredit(txnKey: string): Promise<void> {
  return request("DELETE", `/api/rewards/credits/${encodeURIComponent(txnKey)}`);
}

/** Any signed-in user. A card's shown benefits in file form, hidden ones left out. Sent back unchanged, it previews "No changes". */
export function getBenefitTemplate(accountKey: string): Promise<BenefitFile> {
  return request("GET", `/api/rewards/benefit-files/template${query({ account_key: accountKey })}`);
}

/** Admin only. What a card's benefit file would do, with nothing saved. At most 256 KB. */
export function previewBenefitFile(body: BenefitFileBody): Promise<BenefitFilePreview> {
  return request("POST", "/api/rewards/benefit-files/preview", { body });
}

/**
 * Admin only. The checked file again plus its preview's base_sig, saved all
 * or nothing. A 409 when the benefits changed since the check, a 422 when the
 * file has problems.
 */
export function applyBenefitFile(body: BenefitFileApplyBody): Promise<BenefitFileSaved> {
  return request("POST", "/api/rewards/benefit-files/apply", { body });
}

/** Query keys for the calls above. Every key starts with "rewards", so one prefix makes them all stale. */
export const rewardKeys = {
  page: ["rewards"] as const,
  balances: (programKey: string) => ["rewards", "balances", programKey] as const,
  review: ["rewards", "review"] as const,
  terms: (key: string) => ["rewards", "terms", key] as const,
  /** The word stays in memory only. It goes to the API in the body. */
  preview: (key: string, term: string) => ["rewards", "preview", key, term] as const,
};
