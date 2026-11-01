// Rewards inputs (docs/plans/rewards-inputs.md section 7): the mutations and
// what each one makes stale. The calls live in lib/api/rewards.ts.

import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addBalances,
  addBenefit,
  addProgram,
  applyBenefitFile,
  type BalanceEntryIn,
  type BalanceHistory,
  type BenefitField,
  type BenefitFileApplyBody,
  type BenefitFileBody,
  type BenefitIn,
  type BenefitPatchIn,
  clearCredit,
  clearMark,
  type CreditIn,
  deleteBalance,
  deleteBenefit,
  deleteProgram,
  getBalanceHistory,
  getBenefitTemplate,
  getReview,
  getTerms,
  keepBothBenefits,
  keepBothPrograms,
  moveBalances,
  moveBenefit,
  patchBenefit,
  previewBenefitFile,
  previewTerm,
  type ProgramIn,
  type ResolveChoice,
  resetBenefit,
  resolveBenefit,
  type RewardBenefit,
  type RewardsResponse,
  type RewardsReview,
  rewardKeys,
  saveTerms,
  setCredit,
  setMark,
} from "../../lib/api";
import { type BalanceUnit, withBenefit, withoutChatOnly, withoutRow } from "./model";

/**
 * Balances, programs and benefits feed the Rewards page (and its history,
 * the card credits review and match words, whose keys all start with
 * "rewards"), the Dashboard credits line, the Checks rewards block and trip
 * program names. Only the queries on screen refetch.
 */
const DEPENDENTS = ["rewards", "dashboard", "checks", "trips"] as const;

export function invalidateRewards(qc: QueryClient): void {
  for (const key of DEPENDENTS) void qc.invalidateQueries({ queryKey: [key] });
}

export function useBalanceHistory(programKey: string) {
  return useQuery({ queryKey: rewardKeys.balances(programKey), queryFn: () => getBalanceHistory(programKey) });
}

export function useAddBalances() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entries: BalanceEntryIn[]) => addBalances(entries),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useDeleteBalance(programKey: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteBalance(id),
    onSuccess: (history: BalanceHistory) => {
      qc.setQueryData(rewardKeys.balances(programKey), history);
      invalidateRewards(qc);
    },
  });
}

export function useAddProgram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProgramIn) => addProgram(body),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useDeleteProgram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => deleteProgram(key),
    onSuccess: () => invalidateRewards(qc),
  });
}

/** "Delete balances" on balances kept but not shown: only the ones in the unit the page listed. */
export function useDeleteBalances() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; unit: BalanceUnit }) => deleteProgram(v.key, v.unit),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useMoveBalances() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; toKey: string }) => moveBalances(v.key, v.toKey),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useKeepBoth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; otherKey: string }) => keepBothPrograms(v.key, v.otherKey),
    onSuccess: () => invalidateRewards(qc),
  });
}

/** A saved benefit shows at once (hidden ones move to Hidden), then the page refetches. */
function benefitSaved(qc: QueryClient, out: RewardBenefit): void {
  qc.setQueryData<RewardsResponse>(rewardKeys.page, (page) => (page ? withBenefit(page, out) : page));
  invalidateRewards(qc);
}

export function useAddBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BenefitIn) => addBenefit(body),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function usePatchBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; body: BenefitPatchIn }) => patchBenefit(v.key, v.body),
    onSuccess: (out: RewardBenefit) => benefitSaved(qc, out),
  });
}

export function useResolveBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; field: BenefitField; choice: ResolveChoice }) => resolveBenefit(v.key, v.field, v.choice),
    onSuccess: (out: RewardBenefit) => benefitSaved(qc, out),
  });
}

export function useResetBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => resetBenefit(key),
    onSuccess: (out: RewardBenefit) => benefitSaved(qc, out),
  });
}

export function useDeleteBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => deleteBenefit(key),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useMoveBenefit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; toKey: string }) => moveBenefit(v.key, v.toKey),
    onSuccess: () => invalidateRewards(qc),
  });
}

export function useKeepBothBenefits() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; otherKey: string }) => keepBothBenefits(v.key, v.otherKey),
    onSuccess: () => invalidateRewards(qc),
  });
}

/** Card credits to review, the groups and what needs a look. Off before the first 1.3 run. */
export function useReview(enabled: boolean) {
  return useQuery({ queryKey: rewardKeys.review, queryFn: getReview, enabled });
}

/** A credit's saved, suggested and seen match words. */
export function useTerms(key: string, enabled: boolean) {
  return useQuery({ queryKey: rewardKeys.terms(key), queryFn: () => getTerms(key), enabled });
}

/** What a typed word would match, asked once typing pauses. The word goes in the body. */
export function useTermPreview(key: string, term: string, enabled: boolean) {
  return useQuery({ queryKey: rewardKeys.preview(key, term), queryFn: () => previewTerm(key, term), enabled, retry: false });
}

export function useSaveTerms(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (terms: string[]) => saveTerms(key, terms),
    onSuccess: (out) => {
      qc.setQueryData(rewardKeys.terms(key), out);
      invalidateRewards(qc);
    },
  });
}

/** A marked cycle shows at once. A chat-only cycle leaves the review with it. */
export function useSetMark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; start: string; used: string; usedOn: string | null }) =>
      setMark(v.key, v.start, v.usedOn ? { used: v.used, used_on: v.usedOn } : { used: v.used }),
    onSuccess: (out: RewardBenefit, v) => {
      qc.setQueryData<RewardsReview>(rewardKeys.review, (r) => (r ? withoutChatOnly(r, v.key, v.start) : r));
      benefitSaved(qc, out);
    },
  });
}

export function useClearMark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; start: string }) => clearMark(v.key, v.start),
    onSuccess: (out: RewardBenefit) => benefitSaved(qc, out),
  });
}

/** A decided row leaves the review at once, then everything that reads credits refetches. */
function rowDecided(qc: QueryClient, txnKey: string): void {
  qc.setQueryData<RewardsReview>(rewardKeys.review, (r) => (r ? withoutRow(r, txnKey) : r));
  invalidateRewards(qc);
}

export function useSetCredit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { txnKey: string; body: CreditIn }) => setCredit(v.txnKey, v.body),
    onSuccess: (_out, v) => rowDecided(qc, v.txnKey),
  });
}

export function useClearCredit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (txnKey: string) => clearCredit(txnKey),
    onSuccess: (_out, txnKey) => rowDecided(qc, txnKey),
  });
}

/** "Download this card's list": asked on each tap and never cached, so it is always today's list. */
export function useBenefitTemplate() {
  return useMutation({ mutationFn: (accountKey: string) => getBenefitTemplate(accountKey) });
}

/** "Check file": what a benefit file would do. Nothing is saved, so nothing goes stale. */
export function usePreviewFile() {
  return useMutation({ mutationFn: (body: BenefitFileBody) => previewBenefitFile(body) });
}

/** "Apply": the checked file, all or nothing. Everything that reads benefits refetches. */
export function useApplyFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BenefitFileApplyBody) => applyBenefitFile(body),
    onSuccess: () => invalidateRewards(qc),
  });
}
