#!/usr/bin/env node
// Render the app icon PNGs from frontend/public/icon.svg (the browser tab uses
// the SVG itself). Run again after changing icon.svg:
//
//   node scripts/make_icons.mjs
//
//   icon-512.png          rounded corners, for a server's Docker page 
//   apple-touch-icon.png  180 px with square corners (iOS rounds them itself)
//
// Uses the installed Google Chrome (or SHOT_CHROME) through puppeteer-core
// (a frontend dev dependency), with a throwaway profile, like screenshot.mjs.

import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PUBLIC = join(ROOT, "frontend", "public");
const CHROME = process.env.SHOT_CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const CORNER = 'rx="14"'; // the background rect in icon.svg

const ICONS = [
  { file: "icon-512.png", size: 512, square: false },
  { file: "apple-touch-icon.png", size: 180, square: true },
];

async function loadPuppeteer() {
  const require = createRequire(join(ROOT, "frontend", "package.json"));
  const mod = await import(pathToFileURL(require.resolve("puppeteer-core")).href);
  return mod.default ?? mod;
}

async function main() {
  const svg = readFileSync(join(PUBLIC, "icon.svg"), "utf8");
  if (!svg.includes(CORNER)) throw new Error(`icon.svg has no ${CORNER} on its background rect.`);

  const puppeteer = await loadPuppeteer();
  const profile = mkdtempSync(join(tmpdir(), "budget-icons-"));
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    userDataDir: profile,
    args: ["--no-first-run", "--no-default-browser-check", "--disable-extensions"],
  });
  try {
    const page = await browser.newPage();
    for (const { file, size, square } of ICONS) {
      const art = square ? svg.replace(CORNER, 'rx="0"') : svg;
      await page.setViewport({ width: size, height: size });
      await page.setContent(
        `<!doctype html><style>html,body{margin:0;background:transparent}` +
          `svg{display:block;width:${size}px;height:${size}px}</style>${art}`,
      );
      await page.screenshot({
        path: join(PUBLIC, file),
        omitBackground: true,
        clip: { x: 0, y: 0, width: size, height: size },
      });
      console.log(`Wrote frontend/public/${file}`);
    }
  } finally {
    await browser.close();
    rmSync(profile, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
