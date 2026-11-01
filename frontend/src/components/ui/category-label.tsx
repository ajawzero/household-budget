import type { CategoriesResponse } from "../../lib/api";

/** category_key to emoji, from GET /api/categories. */
export function emojiMap(categories: CategoriesResponse | undefined): Map<string, string> {
  const map = new Map<string, string>();
  for (const g of categories?.groups ?? []) {
    for (const c of g.categories) if (c.emoji) map.set(c.category_key, c.emoji);
  }
  return map;
}

/**
 * A category name with its emoji in a fixed-width slot (PLAN 8.3), so names
 * line up in a column. The slot stays when there is no emoji.
 */
export function CategoryLabel({ name, emoji }: { name: string; emoji?: string | null }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span aria-hidden="true" className="inline-block w-5 shrink-0 text-center">
        {emoji ?? ""}
      </span>
      <span>{name}</span>
    </span>
  );
}
