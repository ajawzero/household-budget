#!/usr/bin/env node
// Logged-in screenshots of the app, for design review. Dev and sample data only.
//
//   node scripts/screenshot.mjs --base http://127.0.0.1:5173 --user alex \
//     --routes "/,/accounts" --widths 1440,390 --themes light,dark --out var/shots
//     [--settle 400]   extra wait in ms after the network goes quiet
//
// Sideways overflow check (Phase 7 chunk 6): no PNGs. Fails when a page is
// wider than the screen, or a box meant to scroll only up and down (a drawer
// body) scrolls sideways, and names the elements that stick out.
//
//   node scripts/screenshot.mjs --check overflow --routes all --user alex
//
// --check targets lists every control smaller than 44px on a phone (a tap
// 21px from its center misses it). Text links inside a sentence do not count.
//
// --demo on starts demo mode after sign-in (the user must be an admin), so
// every page is checked or shot with the demo banner and the sample brand.
//
// --routes all is every page the app links to: the sidebar, the page tabs,
// the first link to each other path, /transactions/suggestions, the calendar
// and holdings-by-class views, /demo-ended, /dev/gallery and /dev/trip-plan
// (the trip planner with test fixtures). Routes after "all" are
// added, like "all,/travel?trip=lake-weekend". In check mode --widths
// defaults to 390.
//
// The password comes from --pass or SHOT_PASS (the env var keeps it out of
// the shell history). The user can come from SHOT_USER. It signs in through
// the real login form, sets the theme the way the app stores it, waits for
// the network to go quiet and saves full-page PNGs named
// <route>-<width>-<theme>.png (the whole page). Uses the installed Google Chrome (or SHOT_CHROME) through
// puppeteer-core (a frontend dev dependency), with a throwaway profile.

import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// SHOT_CHROME points at another Chrome or Chromium, like /opt/pw-browsers/chromium in the cloud.
const CHROME = process.env.SHOT_CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const THEME_KEY = "budget.theme"; // src/lib/theme.ts

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) throw new Error(`Unexpected argument: ${a}`);
    const key = a.slice(2);
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`Missing value for --${key}`);
    out[key] = value;
    i++;
  }
  return out;
}

const list = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function routeName(route) {
  const name = route.replace(/^\/+|\/+$/g, "").replace(/[^a-zA-Z0-9-]+/g, "_");
  return name || "home";
}

// Never visited by the crawl: sign-in, the API, and links that leave the app.
const NO_CRAWL = /^\/(login|logout|api)(\/|$)/;
const MAX_ROUTES = 120;

/** Every route the app links to, from "/". Links in a nav (the sidebar, page
 * tabs) are all followed; any other link only for a path not seen yet. */
async function crawl(page, base, extra) {
  const origin = new URL(base).origin;
  const pathOf = (r) => new URL(r, origin).pathname;
  // Views a segmented control sets (buttons, not links) are seeded by hand.
  const queue = ["/", "/demo-ended", "/dev/gallery", "/dev/trip-plan", "/transactions/suggestions", "/recurring?view=calendar", "/investments?group=class", ...extra];
  const done = [];
  const seen = new Set();
  while (queue.length > 0 && done.length < MAX_ROUTES) {
    const asked = queue.shift();
    if (seen.has(asked)) continue;
    seen.add(asked);
    await page.goto(`${base}${asked}`, { waitUntil: "networkidle0" });
    const at = new URL(page.url());
    // "Demo ended" is a page of its own; landing there from anywhere else means signed out.
    if (at.pathname.startsWith("/login") || (at.pathname === "/demo-ended" && pathOf(asked) !== "/demo-ended"))
      throw new Error(`Signed out at ${asked}`);
    const route = at.pathname + at.search;
    if (route !== asked && seen.has(route)) continue; // a redirect to a page already listed
    seen.add(route);
    done.push(route);
    const links = await page.$$eval("a[href]", (as) => as.map((a) => ({ href: a.href, nav: a.closest("nav") !== null })));
    // PageTabs are buttons that set ?tab= (id "<prefix>-tab-<key>"); count them as nav links.
    const tabs = await page.$$eval("[role=tablist] [role=tab][id]", (ts) => ts.map((t) => t.id));
    for (const id of tabs) {
      const key = /-tab-([\w-]+)$/.exec(id)?.[1];
      if (key) links.push({ href: `${origin}${at.pathname}?tab=${key}`, nav: true });
    }
    for (const { href, nav } of links) {
      const u = new URL(href);
      if (u.origin !== origin || NO_CRAWL.test(u.pathname)) continue;
      const r = u.pathname + u.search;
      if (seen.has(r) || queue.includes(r)) continue;
      const newPath = ![...seen, ...queue].some((q) => pathOf(q) === u.pathname);
      if (nav || newPath) queue.push(r);
    }
  }
  if (queue.length > 0) console.error(`Stopped at ${MAX_ROUTES} routes; ${queue.length} not checked.`);
  return done;
}

/** How far the page is wider than `width`, and the elements that stick out of
 * a parent that fits (the start of each problem). Skips anything inside a
 * scroller or a clip, since that cannot widen the page. Also lists any element
 * meant to scroll only up and down (a drawer body) that scrolls sideways: it
 * hides the same problem from the page width. */
function measureOverflow(width) {
  const name = (el) => {
    const cls = typeof el.className === "string" ? el.className.trim().split(/\s+/).slice(0, 4).join(".") : "";
    const text = (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ""}${text ? ` "${text}"` : ""}`;
  };
  // Sideways scrollers on purpose: wide tables, tab bars, segments, and any
  // class that asks for sideways (or both ways) scrolling.
  const meant = (el) =>
    el.matches(".table-scroll, nav, [role=tablist], [role=group], [role=radiogroup], input, textarea, select") ||
    /\boverflow-(x-)?(auto|scroll)\b/.test(typeof el.className === "string" ? el.className : "");
  const inner = [];
  for (const el of document.body.querySelectorAll("*")) {
    const ox = getComputedStyle(el).overflowX;
    if ((ox === "auto" || ox === "scroll") && el.scrollWidth > el.clientWidth + 1 && !meant(el)) {
      inner.push(`${name(el)} scrolls sideways by ${el.scrollWidth - el.clientWidth}px`);
      if (inner.length >= 5) break;
    }
  }
  const doc = document.documentElement;
  const over = Math.max(doc.scrollWidth, document.body.scrollWidth) - width;
  if (over <= 0) return { over: 0, culprits: inner };
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== "visible") return true;
    }
    return false;
  };
  const culprits = [...inner];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= width + 0.5) continue;
    const parent = el.parentElement?.getBoundingClientRect();
    if (parent && parent.right > width + 0.5) continue;
    if (clipped(el)) continue;
    culprits.push(`${name(el)} (right edge ${Math.round(r.right)}px)`);
    if (culprits.length >= 5) break;
  }
  return { over, culprits };
}

/** Controls whose hit area is under 44px: the box, or a tap 21px left,
 * right, above or below its center lands on something else. Padding and
 * ::before/::after overlays count, since a tap on them reaches the control. A
 * checkbox, a radio or a hidden input counts by its label. Text links inside a
 * sentence are exempt. */
function measureTargets() {
  const SEL = [
    "a[href]", "button", "select", "textarea", "summary", "input:not([type=hidden])",
    "[role=button]", "[role=tab]", "[role=link]", "[role=checkbox]", "[role=switch]",
    "[role=radio]", "[role=menuitem]", "[role=option]",
  ].join(",");
  const name = (el) => {
    const label = el.getAttribute("aria-label") ?? (el.textContent ?? "").trim().replace(/\s+/g, " ");
    return `${el.tagName.toLowerCase()}${label ? ` "${label.slice(0, 40)}"` : ""}`;
  };
  const small = new Map();
  for (const control of document.querySelectorAll(SEL)) {
    if (control.closest("[aria-hidden=true], [inert]")) continue;
    // A checkbox, a radio or a visually hidden input is reached through its
    // label, so the label is the target.
    const hidden = control.matches("input") && control.getBoundingClientRect().width < 2;
    const viaLabel = hidden || control.matches("input[type=checkbox], input[type=radio]");
    const el = viaLabel && control.labels?.length ? control.labels[0] : control;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || getComputedStyle(el).visibility === "hidden") continue;
    if (getComputedStyle(el).display === "inline" && el.matches("a")) {
      const around = (el.parentElement?.textContent ?? "").trim().length - (el.textContent ?? "").trim().length;
      if (around >= 12) continue; // a link in a sentence
    }
    if (r.width >= 43.5 && r.height >= 43.5) continue;
    el.scrollIntoView({ block: "center", inline: "center" });
    const b = el.getBoundingClientRect();
    const [cx, cy] = [b.left + b.width / 2, b.top + b.height / 2];
    const hits = (x, y) => {
      const h = document.elementFromPoint(x, y);
      // A tap on the control's own label reaches it too.
      const labels = [...(control.labels ?? [])];
      return h !== null && (h === el || el.contains(h) || h === control || labels.some((l) => l === h || l.contains(h)));
    };
    if (hits(cx - 21, cy) && hits(cx + 21, cy) && hits(cx, cy - 21) && hits(cx, cy + 21)) continue;
    const key = `${name(control)} ${Math.round(r.width)}x${Math.round(r.height)}`;
    small.set(key, (small.get(key) ?? 0) + 1);
  }
  window.scrollTo(0, 0);
  return [...small].map(([k, n]) => (n > 1 ? `${k} (${n} times)` : k));
}

async function loadPuppeteer() {
  const require = createRequire(join(ROOT, "frontend", "package.json"));
  const mod = await import(pathToFileURL(require.resolve("puppeteer-core")).href);
  return mod.default ?? mod;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const base = (args.base ?? "http://127.0.0.1:5173").replace(/\/$/, "");
  const user = args.user ?? process.env.SHOT_USER;
  const pass = args.pass ?? process.env.SHOT_PASS;
  if (!user || !pass) throw new Error("Give --user and --pass, or set SHOT_USER and SHOT_PASS.");
  const check = args.check;
  if (check !== undefined && check !== "overflow" && check !== "targets") throw new Error(`Unknown check: ${check}`);
  const asked = list(args.routes ?? "/,/accounts");
  const widths = list(args.widths ?? (check ? "390" : "1440,390")).map(Number);
  const themes = list(args.themes ?? "light,dark");
  for (const t of themes) if (t !== "light" && t !== "dark") throw new Error(`Unknown theme: ${t}`);
  const settle = Number(args.settle ?? "400");
  const outDir = resolve(ROOT, args.out ?? "var/shots");
  if (!check) mkdirSync(outDir, { recursive: true });

  const puppeteer = await loadPuppeteer();
  const profile = mkdtempSync(join(tmpdir(), "budget-shots-"));
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    userDataDir: profile,
    args: ["--no-first-run", "--no-default-browser-check", "--disable-extensions"],
  });
  const saved = [];
  const failed = [];
  let checked = 0;
  try {
    const page = await browser.newPage();
    // Charts skip their entry animation, so every shot is the settled chart.
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
    await page.setViewport({ width: 1280, height: 900 });

    await page.goto(`${base}/login`, { waitUntil: "networkidle0" });
    await page.type("input[name=username]", user);
    await page.type("input[name=password]", pass);
    await Promise.all([
      page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 15_000 }),
      page.click("button[type=submit]"),
    ]).catch(async () => {
      const alert = await page.$eval("[role=alert]", (el) => el.textContent).catch(() => null);
      throw new Error(`Sign-in failed${alert ? `: ${alert}` : ""}`);
    });

    if (args.demo !== undefined && args.demo !== "on") throw new Error(`Unknown --demo value: ${args.demo}`);
    if (args.demo === "on") {
      // The app's Start demo button does the same POST, then loads a new page.
      const status = await page.evaluate(async () => (await fetch("/api/demo/start", { method: "POST", credentials: "same-origin" })).status);
      if (status !== 204) throw new Error(`Demo did not start (HTTP ${status}). Is the user an admin?`);
      await page.goto(`${base}/`, { waitUntil: "networkidle0" });
      const banner = await page.$$eval("[role=status]", (els) => els.some((el) => el.textContent?.startsWith("Demo mode.")));
      if (!banner) throw new Error("Demo started, but the page shows no demo banner.");
      console.log("Demo mode on.");
    }

    const routes = asked.includes("all")
      ? await crawl(page, base, asked.filter((r) => r !== "all"))
      : asked;
    if (check) console.log(`Checking ${routes.length} routes: ${routes.join(" ")}`);

    for (const theme of themes) {
      await page.evaluate((key, value) => localStorage.setItem(key, value), THEME_KEY, theme);
      await page.emulateMediaFeatures([
        { name: "prefers-reduced-motion", value: "reduce" },
        { name: "prefers-color-scheme", value: theme },
      ]);
      for (const width of widths) {
        const phone = width < 768;
        await page.setViewport({ width, height: phone ? 844 : 900, deviceScaleFactor: phone ? 2 : 1, isMobile: phone, hasTouch: phone });
        for (const route of routes) {
          await page.goto(`${base}${route}`, { waitUntil: "networkidle0" });
          await page.evaluate(() => document.fonts.ready);
          // Let charts measure their cards and dev CSS settle.
          await new Promise((r) => setTimeout(r, settle));
          if (check === "overflow") {
            const { over, culprits } = await page.evaluate(measureOverflow, width);
            checked++;
            if (over > 0 || culprits.some((c) => c.includes("scrolls sideways")))
              failed.push(`${route} at ${width}px, ${theme}: ${over > 0 ? `${over}px too wide` : "a box scrolls sideways"}\n    ${culprits.join("\n    ") || "(no single element found)"}`);
            continue;
          }
          if (check === "targets") {
            const small = await page.evaluate(measureTargets);
            checked++;
            if (small.length > 0) failed.push(`${route} at ${width}px, ${theme}: ${small.length} small\n    ${small.join("\n    ")}`);
            continue;
          }
          const file = join(outDir, `${routeName(route)}-${width}-${theme}.png`);
          // Grow the viewport to the page height and let it settle, then capture.
          // Plain fullPage capture resizes mid-shot, which catches charts mid-resize.
          const fullHeight = await page.evaluate(() => document.documentElement.scrollHeight);
          await page.setViewport({ ...page.viewport(), height: Math.max(fullHeight, phone ? 844 : 900) });
          await new Promise((r) => setTimeout(r, settle));
          await page.screenshot({ path: file });
          await page.setViewport({ ...page.viewport(), height: phone ? 844 : 900 });
          saved.push(file);
        }
      }
    }
  } finally {
    await browser.close();
    rmSync(profile, { recursive: true, force: true });
  }
  for (const f of saved) console.log(f);
  if (check) {
    for (const f of failed) console.log(`FAIL ${f}`);
    console.log(`${failed.length ? "FAIL" : "PASS"} ${check}: ${checked - failed.length} of ${checked} pages pass`);
    if (failed.length) process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
