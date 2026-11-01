import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import type { CategoriesResponse } from "../../lib/api";
import { bulkEdit, invalidateAfterEdit, type TxnPatch } from "./api";
import { CategorySelect } from "./controls";

interface BulkBarProps {
  selected: string[];
  categories: CategoriesResponse | undefined;
  onClear: () => void;
  onDone: (message: string) => void;
}

const MARKS: { label: string; changes: TxnPatch }[] = [
  { label: "Mark as transfer", changes: { is_transfer: true } },
  { label: "Mark as not a transfer", changes: { is_transfer: false } },
  { label: "Mark as reimbursable", changes: { is_reimbursable: true } },
  { label: "Mark as not reimbursable", changes: { is_reimbursable: false } },
  { label: "Exclude from budget", changes: { exclude_from_budget: true } },
  { label: "Count in budget", changes: { exclude_from_budget: false } },
  { label: "Hide", changes: { hidden: true } },
  { label: "Show again", changes: { hidden: false } },
];

const RESET: TxnPatch = {
  category_key: null,
  is_transfer: null,
  is_reimbursable: null,
  exclude_from_budget: null,
  hidden: null,
};

/** The bar for Edit multiple: set a category or a flag on every selected row at once (all or nothing). */
export function BulkBar({ selected, categories, onClear, onDone }: BulkBarProps) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState("");
  const categoryId = useId();
  const n = selected.length;

  const edit = useMutation({
    mutationFn: (changes: TxnPatch) => bulkEdit(selected, changes),
    onSuccess: (out) => {
      setCategory("");
      void invalidateAfterEdit(queryClient);
      onDone(`Updated ${out.updated} of ${out.count} transaction${out.count === 1 ? "" : "s"}.`);
    },
  });

  return (
    <div
      role="region"
      aria-label="Edit selected transactions"
      className="fixed inset-x-4 bottom-[calc(64px+env(safe-area-inset-bottom))] z-30 flex animate-bar-in flex-wrap items-center gap-2 rounded-card bg-card p-3 shadow-pop md:inset-x-auto md:bottom-6 md:left-1/2 md:-translate-x-1/2 lg:left-[calc(50%+112px)]"
    >
      <p className="m-0 mr-1 text-sm font-medium text-fg" aria-live="polite">
        {n} selected
      </p>
      <label htmlFor={categoryId} className="sr-only">
        Category for selected
      </label>
      <CategorySelect
        id={categoryId}
        value={category}
        placeholder="Set category"
        categories={categories}
        onChange={setCategory}
        disabled={n === 0 || edit.isPending}
        className="w-auto max-w-[12rem]"
      />
      <Button size="sm" variant="primary" disabled={!category || n === 0 || edit.isPending} onClick={() => edit.mutate({ category_key: category })}>
        Apply
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" disabled={n === 0 || edit.isPending}>
            More
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" side="top">
          <DropdownMenuLabel>Set on {n} selected</DropdownMenuLabel>
          {MARKS.map((m) => (
            <DropdownMenuItem key={m.label} onSelect={() => edit.mutate(m.changes)}>
              {m.label}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => edit.mutate(RESET)}>Undo category and flag edits</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button size="sm" variant="ghost" onClick={onClear} disabled={n === 0}>
        Clear
      </Button>
      {edit.isError && (
        <p role="alert" className="m-0 basis-full text-sm text-bad">
          {edit.error.message}
        </p>
      )}
    </div>
  );
}
