// While a cover is up, every other child of body is hidden too, not just the app root:
// drawers and menus are portals, so they sit beside the root, outside its cover. Only
// `inert` and `visibility` are used. Radix sets and clears aria-hidden on these same
// elements while a drawer opens and closes, so the shield leaves that alone.

const saved = new Map<HTMLElement, { inert: boolean; visibility: string }>();
let covers = 0;
let watch: MutationObserver | null = null;

function hideOthers() {
  for (const el of Array.from(document.body.children)) {
    if (!(el instanceof HTMLElement) || el.hasAttribute("data-page-cover") || saved.has(el)) continue;
    saved.set(el, { inert: el.hasAttribute("inert"), visibility: el.style.visibility });
    el.setAttribute("inert", "");
    el.style.visibility = "hidden";
  }
}

/** Not painted, not read out, no focus or taps. A drawer that opens later is hidden too. */
export function shieldPage(): void {
  if (covers++ > 0) return;
  hideOthers();
  watch = new MutationObserver(hideOthers);
  watch.observe(document.body, { childList: true });
}

/** Puts back what `shieldPage` changed once the last cover is gone. */
export function unshieldPage(): void {
  if (covers === 0 || --covers > 0) return;
  watch?.disconnect();
  watch = null;
  for (const [el, was] of saved) {
    if (!was.inert) el.removeAttribute("inert");
    el.style.visibility = was.visibility;
  }
  saved.clear();
}
