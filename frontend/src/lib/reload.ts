// Whole-page loads, in one place so tests can stub them. Every change of mode
// (demo start, leave, sign-in) loads a new page instead of moving inside the
// app, so nothing from the other mode stays in memory: open drawers, the Ask
// panel, cached numbers (docs/plans/demo-mode.md section 7).

/** Loads `path` in place of this page, with no new history entry. */
export function replacePage(path: string): void {
  window.location.replace(path);
}

/** Loads this page again. */
export function reloadPage(): void {
  window.location.reload();
}
