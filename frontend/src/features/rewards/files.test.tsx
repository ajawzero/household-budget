// Rewards inputs (docs/plans/rewards-inputs.md chunk E): "Update from a
// file" on the Benefits tab. The card's list as a download, a file or pasted
// text checked in the browser first, the preview, and Apply. Made-up numbers only.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountsResponse, BenefitFile, BenefitFilePreview } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { REWARDS } from "./fixtures";
import {
  FILE_MAX_BYTES,
  FILE_TOO_BIG,
  fileBody,
  fileChangeText,
  fileFieldName,
  fileSavedText,
  jsonErrorLine,
  OTHER_CARD,
  unchangedText,
} from "./model";
import { RewardsPage } from "./RewardsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Reply = (body: unknown) => Response | Promise<Response>;

let role: "admin" | "member" = "admin";
let replies: Record<string, Reply> = {};

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  if (url === "/api/auth/me") return json(200, { username: "alex", display_name: "Alex", role });
  if (url === "/api/rewards" && method === "GET") return json(200, REWARDS);
  const reply = replies[`${method} ${url}`];
  if (reply) return reply(init?.body ? JSON.parse(String(init.body)) : undefined);
  return json(404, { detail: "Not found." });
});

// Only the fields cardOptions reads.
const ACCOUNTS = {
  groups: [
    {
      accounts: [
        { account_key: "summit-card", display_name: "Summit card", type: "credit_card", active: true },
        { account_key: "orchard-card", display_name: "Orchard card", type: "store_card", active: true },
      ],
    },
  ],
} as unknown as AccountsResponse;

const PREVIEW_URL = "/api/rewards/benefit-files/preview";
const APPLY_URL = "/api/rewards/benefit-files/apply";
const TEMPLATE_URL = "/api/rewards/benefit-files/template?account_key=summit-card";
const SIG = "c0ffee".repeat(10) + "abcd";

const FILE = {
  kind: "budget-app-benefits",
  version: 1,
  account_key: "summit-card",
  starts_on: "2026-10-20",
  mode: "merge",
  benefits: [{ key: "dining-credit", name: "Dining credit", kind: "credit", cadence: "monthly", amount: "12.00", match_terms: ["DINING CREDIT"] }],
};
const FILE_TEXT = JSON.stringify(FILE, null, 2);

const TEMPLATE: BenefitFile = { ...FILE, kind: "budget-app-benefits", version: 1, mode: "merge", benefits: [{ ...FILE.benefits[0]!, kind: "credit", cadence: "monthly", account_key: "summit-card" }] };

const CLEAN: BenefitFilePreview = {
  account_key: "summit-card",
  account_name: "Summit card",
  base_sig: SIG,
  mode: "merge",
  starts_on: "2027-01-01",
  no_changes: false,
  problems: [],
  to_add: [
    { key: "app-gym-credit", name: "Gym credit", kind: "credit", terms: true },
    { key: "app-spa-night", name: "Spa night", kind: "free_night", terms: false },
  ],
  to_change: [{ key: "dining-credit", name: "Dining credit", fields: ["amount", "how_to_use"], amount_from: "2027-01-01", terms: true }],
  to_end: [{ key: "travel-credit", name: "Yearly travel credit", ends_on: "2026-12-31" }],
  unchanged: 2,
};

const WITH_PROBLEMS: BenefitFilePreview = {
  ...CLEAN,
  base_sig: null,
  to_add: [],
  to_change: [],
  to_end: [],
  unchanged: 0,
  problems: [
    { where: "benefits[0].amount", rule: "money-format", message: "Use dollars and cents, like 12.00." },
    { where: "benefits[0].(unknown field)", rule: "extra-forbidden", message: "This field is not known." },
  ],
};

const NO_CHANGES: BenefitFilePreview = { ...CLEAN, to_add: [], to_change: [], to_end: [], unchanged: 3, no_changes: true };

beforeEach(() => {
  role = "admin";
  replies = { "GET /api/accounts": () => json(200, ACCOUNTS) };
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BENEFITS_TAB = "/rewards?tab=benefits";

function calls(method: string, url: string) {
  return fetchMock.mock.calls.filter(([u, init]) => String(u) === url && (init?.method ?? "GET") === method);
}

function sentBody(method: string, url: string): unknown {
  const call = calls(method, url).at(-1);
  if (!call) throw new Error(`${method} ${url} was not sent`);
  return JSON.parse(String(call[1]!.body));
}

function invalidated(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls.map((c) => ((c[0] as { queryKey: string[] }).queryKey ?? [])[0]!);
}

type User = ReturnType<typeof userEvent.setup>;

/** Open the drawer, with a card picked unless asked not to. */
async function openFiles(user: User, card: string | null = "summit-card"): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Update from a file" }));
  const drawer = screen.getByRole("dialog", { name: "Update from a file" });
  const select = within(drawer).getByLabelText("Card") as HTMLSelectElement;
  await waitFor(() => expect(select.disabled).toBe(false));
  if (card) await user.selectOptions(select, card);
  return drawer;
}

function jsonFile(text: string, name = "summit-card-benefits.json"): File {
  return new File([text], name, { type: "application/json" });
}

function applyButton(drawer: HTMLElement): HTMLButtonElement {
  return within(drawer).getByRole("button", { name: "Apply" }) as HTMLButtonElement;
}

/** Upload the file and check it against a preview reply. */
async function check(user: User, drawer: HTMLElement, preview: BenefitFilePreview, text = FILE_TEXT): Promise<void> {
  replies[`POST ${PREVIEW_URL}`] = () => json(200, preview);
  await user.upload(within(drawer).getByLabelText("File"), jsonFile(text));
  await user.click(within(drawer).getByRole("button", { name: "Check file" }));
  await within(drawer).findByRole("region", { name: "What the file does" });
}

describe("benefit file helpers", () => {
  it("finds the line of a parse error from any browser, and never more", () => {
    const text = '{\n  "kind": "x"\n  "version": 1\n}';
    expect(jsonErrorLine("Expected ',' or '}' after property value in JSON at position 18 (line 3 column 3)", text)).toBe(3);
    expect(jsonErrorLine("JSON.parse: expected ',' or '}' after property value in object at line 3 column 3 of the JSON data", text)).toBe(3);
    expect(jsonErrorLine("Unexpected string in JSON at position 18", text)).toBe(3); // a position only
    expect(jsonErrorLine("JSON Parse error: Expected '}'", text)).toBeNull();
  });

  it("turns the file's text into the body, with the drawer's card, mode and start on top", () => {
    expect(fileBody(FILE_TEXT, "summit-card", "replace", "2027-01-01")).toEqual({ ...FILE, mode: "replace", starts_on: "2027-01-01" });
    // No start in the drawer keeps the file's. A file with no card takes the drawer's.
    const { account_key: _, ...noCard } = FILE;
    expect(fileBody(JSON.stringify(noCard), "summit-card", "merge", "")).toEqual(FILE);
    // A mode and start not set in the drawer are the file's, else the API's defaults.
    const own = JSON.stringify({ ...FILE, mode: "replace", starts_on: "2027-01-01" });
    expect(fileBody(own, "summit-card", null, null)).toEqual({ ...FILE, mode: "replace", starts_on: "2027-01-01" });
    const { mode: _m, starts_on: _s, ...bare } = FILE;
    expect(fileBody(JSON.stringify(bare), "summit-card", null, null)).toEqual({ ...bare, mode: "merge" });
    expect(fileBody(JSON.stringify({ ...FILE, mode: "all" }), "summit-card", null, null)).toEqual({ ...FILE, mode: "all" });
    expect(fileBody(FILE_TEXT, "orchard-card", "merge", "")).toBe(OTHER_CARD);
    expect(fileBody("[]", "summit-card", "merge", "")).toBe("This is not a benefits file. Start from this card's list.");
    expect(fileBody("12", "summit-card", "merge", "")).toBe("This is not a benefits file. Start from this card's list.");
  });

  it("names the line of a broken file and never quotes it", () => {
    // V8 quotes the text for some errors and gives no position: then no line.
    const text = '{\n  "name": "Gym credit",\n  "kind": \n}';
    expect(() => JSON.parse(text)).toThrow(/"kind"/);
    expect(fileBody(text, "summit-card", "merge", "")).toBe("This is not valid JSON.");
    expect(fileBody('{\n  "kind": "x"\n  "version": 1\n}', "summit-card", "merge", "")).toBe("This is not valid JSON. Look near line 3.");
  });

  it("words what a file changes and saves", () => {
    expect(fileChangeText(CLEAN.to_change[0]!)).toBe("Amount from Jan 1, 2027 · how to use it · match words");
    expect(fileChangeText({ key: "k", name: "K", fields: [], amount_from: null, terms: true })).toBe("Match words");
    expect(fileChangeText({ key: "k", name: "K", fields: ["cycles", "category_keys"], amount_from: null, terms: false })).toBe("Cycles · categories");
    expect(fileFieldName("some_new_field")).toBe("some new field");
    expect(fileSavedText(1)).toBe("Saved. 1 change.");
    expect(fileSavedText(5)).toBe("Saved. 5 changes.");
    expect(unchangedText(1)).toBe("1 benefit stays as it is.");
    expect(unchangedText(3)).toBe("3 benefits stay as they are.");
  });
});

describe("Update from a file", () => {
  it("sits next to Add a benefit, full width on phones, with 16px fields and Apply off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const button = await screen.findByRole("button", { name: "Update from a file" });
    expect(button.className).toContain("w-full");
    expect(button.className).toContain("md:w-auto");
    const add = screen.getByRole("button", { name: "Add a benefit" });
    expect(add.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const drawer = await openFiles(user, null);
    for (const label of ["Card", "File", "Starts on"]) {
      const field = within(drawer).getByLabelText(label);
      expect(field.className).toContain("text-base"); // 16px: no zoom on iOS
      expect(field.className).toContain("h-11"); // 44px on phones
    }
    const file = within(drawer).getByLabelText("File");
    expect(file.getAttribute("accept")).toBe(".json,application/json");
    expect((within(drawer).getByLabelText("Starts on") as HTMLInputElement).value).toBe(REWARDS.today);
    expect(within(drawer).getByText("New amounts and new benefits start on this day.")).toBeTruthy();
    const merge = within(drawer).getByRole("radio", { name: "Only the benefits that change" }) as HTMLInputElement;
    expect(merge.checked).toBe(true);
    expect(within(drawer).getByRole("group", { name: "What the file holds" })).toBeTruthy();
    expect(within(drawer).getByText("Benefits the file leaves out end the day before the start date.")).toBeTruthy();
    for (const name of ["Download this card's list", "Paste instead", "Check file", "Apply"]) {
      expect(within(drawer).getByRole("button", { name }).className).toContain("h-11");
    }
    expect(applyButton(drawer).disabled).toBe(true);
    expect(within(drawer).getByText("Apply turns on once the file checks out.")).toBeTruthy();
    expect(drawer.textContent).not.toContain("—");
    // A card first.
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(within(drawer).getByText("Pick a card.")).toBeTruthy();
    await user.selectOptions(within(drawer).getByLabelText("Card"), "summit-card");
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(within(drawer).getByText("Pick a file.")).toBeTruthy();
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
  });

  it("shows members who can change benefits, with no file button", async () => {
    role = "member";
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    expect(await screen.findByText("Only an admin can change benefits.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Update from a file" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a benefit" })).toBeNull();
  });

  it("refuses a file over 256 KB before reading it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    const big = jsonFile(" ".repeat(FILE_MAX_BYTES + 1));
    const read = vi.spyOn(big, "text");
    await user.upload(within(drawer).getByLabelText("File"), big);
    expect(within(drawer).getByText(FILE_TOO_BIG)).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(within(drawer).getByText(FILE_TOO_BIG)).toBeTruthy();
    expect(read).not.toHaveBeenCalled();
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
    // Pasted text has the same limit.
    await user.click(within(drawer).getByRole("button", { name: "Paste instead" }));
    await user.click(within(drawer).getByLabelText("File text"));
    await user.paste(`{"a": "${"x".repeat(FILE_MAX_BYTES)}"}`);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(within(drawer).getByText(FILE_TOO_BIG)).toBeTruthy();
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
  });

  it("shows a broken file quietly with its line, and sends nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    await user.upload(within(drawer).getByLabelText("File"), jsonFile('{\n  "name": "Gym credit"\n  "kind": "credit"\n}'));
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    const note = await within(drawer).findByText("This is not valid JSON. Look near line 3.");
    expect(note.closest("[aria-live='polite']")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(drawer.textContent).not.toContain("Gym credit");
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
    expect(applyButton(drawer).disabled).toBe(true);
  });

  it("checks a file and lists what it adds, changes and ends", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const drawer = await openFiles(user);
    const starts = within(drawer).getByLabelText("Starts on");
    await user.clear(starts);
    await user.type(starts, "2027-01-01");
    await check(user, drawer, CLEAN);
    expect(sentBody("POST", PREVIEW_URL)).toEqual({ ...FILE, starts_on: "2027-01-01" });
    const preview = within(drawer).getByRole("region", { name: "What the file does" });
    expect(within(preview).getByText("Summit card, from Jan 1, 2027. Only the benefits that change.")).toBeTruthy();
    const adds = within(preview).getByRole("region", { name: "To add" });
    expect(within(adds).getByText("To add (2)")).toBeTruthy();
    expect(within(adds).getByText("Gym credit")).toBeTruthy();
    expect(within(adds).getByText("Credit · with match words")).toBeTruthy();
    expect(within(adds).getByText("Spa night")).toBeTruthy();
    expect(within(adds).getByText("Free night")).toBeTruthy();
    const changes = within(preview).getByRole("region", { name: "To change" });
    expect(within(changes).getByText("Dining credit")).toBeTruthy();
    expect(within(changes).getByText("Amount from Jan 1, 2027 · how to use it · match words")).toBeTruthy();
    const ends = within(preview).getByRole("region", { name: "To end" });
    expect(within(ends).getByText("Yearly travel credit")).toBeTruthy();
    expect(within(ends).getByText("Ends Dec 31, 2026")).toBeTruthy();
    expect(within(preview).getByText("2 benefits stay as they are.")).toBeTruthy();
    expect(within(preview).queryByRole("alert")).toBeNull();
    expect(applyButton(drawer).disabled).toBe(false);
    expect(preview.textContent).not.toContain("—");
    expect(invalidated(spy)).toEqual([]); // a check saves nothing
  });

  it("keeps Apply off until a clean check, and off again after a change", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    expect(applyButton(drawer).disabled).toBe(true);
    // Problems: the place and the rule only, read out at once.
    await check(user, drawer, WITH_PROBLEMS);
    const alert = within(drawer).getByRole("alert");
    expect(within(alert).getByText("The file has problems. Fix them, then check it again.")).toBeTruthy();
    const problems = within(alert).getByRole("list", { name: "Problems" });
    expect(within(problems).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "benefits[0].amountUse dollars and cents, like 12.00.",
      "benefits[0].(unknown field)This field is not known.",
    ]);
    expect(within(drawer).queryByRole("region", { name: "To add" })).toBeNull();
    expect(applyButton(drawer).disabled).toBe(true);
    // A clean file that changes nothing.
    replies[`POST ${PREVIEW_URL}`] = () => json(200, NO_CHANGES);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(await within(drawer).findByText("No changes. The file matches this card's list.")).toBeTruthy();
    expect(applyButton(drawer).disabled).toBe(true);
    // A clean file with changes.
    replies[`POST ${PREVIEW_URL}`] = () => json(200, CLEAN);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "To change" });
    expect(applyButton(drawer).disabled).toBe(false);
    // Any change to what was checked turns it off and drops the preview.
    await user.click(within(drawer).getByRole("radio", { name: "Every benefit on the card" }));
    expect(applyButton(drawer).disabled).toBe(true);
    expect(within(drawer).queryByRole("region", { name: "What the file does" })).toBeNull();
    replies[`POST ${PREVIEW_URL}`] = () => json(200, { ...CLEAN, mode: "replace" });
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "What the file does" });
    expect((sentBody("POST", PREVIEW_URL) as { mode: string }).mode).toBe("replace");
    expect(applyButton(drawer).disabled).toBe(false);
    await user.selectOptions(within(drawer).getByLabelText("Card"), "orchard-card");
    expect(applyButton(drawer).disabled).toBe(true);
  });

  it.each([
    [409, "The benefits changed since you checked the file. Check it again."],
    [422, "The file has problems. Check it again."],
    [413, FILE_TOO_BIG],
  ])("says why Apply was refused (%i) and drops the preview", async (status, detail) => {
    const user = userEvent.setup();
    replies[`POST ${APPLY_URL}`] = () => json(status, status === 422 ? { detail, problems: WITH_PROBLEMS.problems } : { detail });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const drawer = await openFiles(user);
    await check(user, drawer, CLEAN);
    await user.click(applyButton(drawer));
    expect(await within(drawer).findByText(detail)).toBeTruthy();
    expect(within(drawer).queryByRole("region", { name: "What the file does" })).toBeNull();
    expect(applyButton(drawer).disabled).toBe(true);
    expect(screen.getByRole("dialog", { name: "Update from a file" })).toBeTruthy();
    expect(invalidated(spy)).toEqual([]);
  });

  it("drops a check that was under way when the form changed", async () => {
    const user = userEvent.setup();
    let answer: (r: Response) => void = () => {};
    replies[`POST ${PREVIEW_URL}`] = () => new Promise<Response>((done) => (answer = done));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    await user.upload(within(drawer).getByLabelText("File"), jsonFile(FILE_TEXT));
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await waitFor(() => expect(calls("POST", PREVIEW_URL)).toHaveLength(1));
    await user.click(within(drawer).getByRole("radio", { name: "Every benefit on the card" }));
    answer(json(200, CLEAN));
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "Check file" }).hasAttribute("disabled")).toBe(false));
    expect(within(drawer).queryByRole("region", { name: "What the file does" })).toBeNull();
    expect(applyButton(drawer).disabled).toBe(true);
  });

  it("applies a clean check with its base_sig, says how many changed and refreshes", async () => {
    const user = userEvent.setup();
    replies[`POST ${APPLY_URL}`] = () => json(200, { saved: 5, added: 2, changed: 1, ended: 2, unchanged: 2 });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const drawer = await openFiles(user);
    await check(user, drawer, CLEAN);
    await user.click(applyButton(drawer));
    expect(await screen.findByText("Saved. 5 changes.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    // The same body the check sent, plus its base_sig.
    expect(sentBody("POST", APPLY_URL)).toEqual({ ...(sentBody("POST", PREVIEW_URL) as object), base_sig: SIG });
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
  });

  it("downloads the card's list as <card>-benefits.json", async () => {
    const user = userEvent.setup();
    replies[`GET ${TEMPLATE_URL}`] = () => json(200, TEMPLATE);
    const create = vi.fn((_blob: Blob) => "blob:summit-list");
    const revoke = vi.fn();
    const had = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const saved: { download: string; href: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push({ download: this.download, href: this.href });
    });
    try {
      renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
      const drawer = await openFiles(user, null);
      await user.click(within(drawer).getByRole("button", { name: "Download this card's list" }));
      expect(within(drawer).getByText("Pick a card.")).toBeTruthy();
      expect(calls("GET", TEMPLATE_URL)).toHaveLength(0);
      await user.selectOptions(within(drawer).getByLabelText("Card"), "summit-card");
      expect(within(drawer).queryByText("Pick a card.")).toBeNull();
      await user.click(within(drawer).getByRole("button", { name: "Download this card's list" }));
      await waitFor(() => expect(saved).toEqual([{ download: "summit-card-benefits.json", href: "blob:summit-list" }]));
      const blob = create.mock.calls[0]![0];
      expect(blob.type).toBe("application/json");
      expect(JSON.parse(await blob.text())).toEqual(TEMPLATE);
      await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:summit-list"));
      expect(document.querySelector("a[download]")).toBeNull(); // the link is gone again
    } finally {
      Object.assign(URL, had);
    }
  });

  it("checks pasted text the same way", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    replies[`POST ${PREVIEW_URL}`] = () => json(200, CLEAN);
    await user.click(within(drawer).getByRole("button", { name: "Paste instead" }));
    expect(within(drawer).queryByLabelText("File")).toBeNull();
    const area = within(drawer).getByLabelText("File text");
    expect(area.className).toContain("text-base");
    await user.click(area);
    await user.paste(FILE_TEXT);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "To change" });
    expect(sentBody("POST", PREVIEW_URL)).toEqual(FILE);
    expect(applyButton(drawer).disabled).toBe(false);
    // Back to a file: the pasted check no longer holds.
    await user.click(within(drawer).getByRole("button", { name: "Pick a file instead" }));
    expect(within(drawer).getByLabelText("File")).toBeTruthy();
    expect(applyButton(drawer).disabled).toBe(true);
    expect(within(drawer).queryByRole("region", { name: "What the file does" })).toBeNull();
  });

  it("checks only the file the field shows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    replies[`POST ${PREVIEW_URL}`] = () => json(200, CLEAN);
    await user.upload(within(drawer).getByLabelText("File"), jsonFile(FILE_TEXT));
    // To paste and back: the new field is empty, so nothing is sent.
    await user.click(within(drawer).getByRole("button", { name: "Paste instead" }));
    await user.click(within(drawer).getByRole("button", { name: "Pick a file instead" }));
    expect((within(drawer).getByLabelText("File") as HTMLInputElement).files).toHaveLength(0);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(await within(drawer).findByText("Pick a file.")).toBeTruthy();
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
    expect(applyButton(drawer).disabled).toBe(true);
    // A clean check, then the same file picked again (fixed on disk): the check
    // no longer holds, and the next one sends the new text.
    await user.upload(within(drawer).getByLabelText("File"), jsonFile(FILE_TEXT));
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "To change" });
    const fixed = JSON.stringify({ ...FILE, benefits: [{ ...FILE.benefits[0], amount: "14.00" }] });
    await user.upload(within(drawer).getByLabelText("File"), jsonFile(fixed));
    expect(within(drawer).queryByRole("region", { name: "What the file does" })).toBeNull();
    expect(applyButton(drawer).disabled).toBe(true);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "To change" });
    expect((sentBody("POST", PREVIEW_URL) as typeof FILE).benefits[0]!.amount).toBe("14.00");
  });

  it("empties the field when the file cannot be read, so picking it again works", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    replies[`POST ${PREVIEW_URL}`] = () => json(200, CLEAN);
    const stale = jsonFile(FILE_TEXT);
    vi.spyOn(stale, "text").mockRejectedValue(new DOMException("changed", "NotReadableError"));
    const field = within(drawer).getByLabelText("File") as HTMLInputElement;
    await user.upload(field, stale);
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    expect(await within(drawer).findByText("Could not read the file. Pick it again.")).toBeTruthy();
    expect(field.files).toHaveLength(0);
    expect(calls("POST", PREVIEW_URL)).toHaveLength(0);
    await user.upload(field, jsonFile(FILE_TEXT));
    expect(within(drawer).queryByText("Could not read the file. Pick it again.")).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "To change" });
    expect(calls("POST", PREVIEW_URL)).toHaveLength(1);
    expect(sentBody("POST", PREVIEW_URL)).toEqual(FILE);
  });

  it("moves focus to what the file does, and back to Check file when Apply is refused", async () => {
    const user = userEvent.setup();
    replies[`POST ${APPLY_URL}`] = () => json(409, { detail: "The benefits changed since you checked the file. Check it again." });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    await check(user, drawer, CLEAN);
    await waitFor(() => expect(document.activeElement?.textContent).toBe("What the file does"));
    expect(document.activeElement?.tagName).toBe("H3");
    await user.click(applyButton(drawer));
    await within(drawer).findByText("The benefits changed since you checked the file. Check it again.");
    const checkButton = within(drawer).getByRole("button", { name: "Check file" });
    await waitFor(() => expect(document.activeElement).toBe(checkButton));
    // A file with problems: its heading too, with the problems read out.
    await check(user, drawer, WITH_PROBLEMS);
    await waitFor(() => expect(document.activeElement?.textContent).toBe("What the file does"));
    expect(within(drawer).getByRole("alert")).toBeTruthy();
  });

  it("uses the file's start and choice until they are changed here, and says which", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openFiles(user);
    const own = JSON.stringify({ ...FILE, starts_on: "2027-01-01", mode: "replace" });
    await check(user, drawer, { ...CLEAN, mode: "replace", starts_on: "2027-01-01" }, own);
    expect(sentBody("POST", PREVIEW_URL)).toEqual({ ...FILE, starts_on: "2027-01-01", mode: "replace" });
    // The drawer shows what the check used.
    expect((within(drawer).getByLabelText("Starts on") as HTMLInputElement).value).toBe("2027-01-01");
    expect((within(drawer).getByRole("radio", { name: "Every benefit on the card" }) as HTMLInputElement).checked).toBe(true);
    expect(within(drawer).getByText("Summit card, from Jan 1, 2027. Every benefit on the card.")).toBeTruthy();
    expect(applyButton(drawer).disabled).toBe(false);
    // Changed here, the drawer's win.
    await user.click(within(drawer).getByRole("radio", { name: "Only the benefits that change" }));
    const starts = within(drawer).getByLabelText("Starts on");
    await user.clear(starts);
    await user.type(starts, "2026-11-01");
    replies[`POST ${PREVIEW_URL}`] = () => json(200, { ...CLEAN, starts_on: "2026-11-01" });
    await user.click(within(drawer).getByRole("button", { name: "Check file" }));
    await within(drawer).findByRole("region", { name: "What the file does" });
    expect(sentBody("POST", PREVIEW_URL)).toEqual({ ...FILE, starts_on: "2026-11-01", mode: "merge" });
    expect(within(drawer).getByText("Summit card, from Nov 1, 2026. Only the benefits that change.")).toBeTruthy();
  });
});
