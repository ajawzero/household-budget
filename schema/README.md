# Import format v1 (schema 1.4)

This is the one format document for the budget app import. The export chat writes runs in this format. The app checks each run and rejects the whole run on any error.

Files in this folder (copied to `_schema/` beside your run folders):

| File | What it is |
|---|---|
| `README.md` | This document. |
| `import-v1.schema.json` | JSON Schema for `manifest.json`, `snapshot.json` and one CSV row. Generated from the app's models. Do not edit. |
| `categories.json` | The category list. Use only these `category_key` values. |
| `redaction.json` | The redaction patterns. The checker and the app both load it. |
| `check_run.py` | The checker. Standard library only. Python 3.10 or newer. |

## 1. Run folders

```
data/
  2026-10-21/                      one run, named by its run id
    snapshot.json
    transactions/
      summit-card-2026-09-10.csv   <account_key>-<period_end>.csv
    manifest.json                  written last
  2026-10-21-2/                    a second run on the same day
```

- The run id is `YYYY-MM-DD` for the first run of a day. More runs that day use `YYYY-MM-DD-2`, `-3` and so on. `-1` and leading zeros are not allowed.
- Runs are ordered by date, then by number, as integers. So `-10` comes after `-2`.
- The run date may be at most 1 day after today.
- Only these paths are allowed: `snapshot.json`, `manifest.json` and `transactions/<name>.csv` where the name matches `[a-z0-9-]+`. No other files, folders or symbolic links. No `.DS_Store`.
- Each file is 20 MB at most.
- Write `snapshot.json` and the CSV files first. Run `check_run.py <folder> --pre`. Then write `manifest.json` last. Then run `check_run.py <folder>`.
- **A folder never changes after its manifest exists.** To fix something, write a new run. The app rejects a run whose manifest differs from one it already imported with the same id.

## 2. manifest.json

```json
{
  "created_at": "2026-10-21T19:04:11-05:00",
  "files": [
    {"bytes": 1234, "path": "snapshot.json", "sha256": "<64 lowercase hex>"},
    {"bytes": 567, "path": "transactions/summit-card-2026-09-10.csv", "rows": 12, "sha256": "<64 lowercase hex>"}
  ],
  "generator": {"skill": "my-exporter", "version": "2026-10-21"},
  "mode": "monthly",
  "run_id": "2026-10-21",
  "schema_version": "1.4"
}
```

| Field | Rule |
|---|---|
| `schema_version` | `"1.4"` (new runs), `"1.3"`, `"1.2"`, `"1.1"` or `"1.0"`. Equals `schema_version` in `snapshot.json`. |
| `run_id` | Equals the folder name. |
| `mode` | `monthly`, `backfill` or `correction`. Equals `mode` in `snapshot.json`. |
| `created_at` | ISO 8601 with seconds and an offset, like `2026-10-21T19:04:11-05:00`. |
| `generator` | `skill` and `version`, both text. |
| `files` | Every file in the folder except `manifest.json`. `snapshot.json` is required. |
| `files[].path` | `snapshot.json` or `transactions/<name>.csv`. |
| `files[].sha256` | sha256 of the file bytes, 64 lowercase hex. Use the device-side checksum. |
| `files[].bytes` | File size in bytes. |
| `files[].rows` | CSV files only: the number of data rows, not counting the header. `snapshot.json` has no `rows`. |

## 3. Writing files (determinism)

- UTF-8 without a byte order mark. LF line endings only. Every file ends with a newline.
- JSON: `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)` plus `"\n"`.
- No JSON numbers for money or quantities. They are strings. The only JSON numbers are counts and small whole numbers (`txn_count`, `seq`, `bytes`, `rows`, `stale_after_days`, `display_order`, `priority`, `cycle_close_day`, in 1.1 `interval_n`, `payments_total`, `payments_left` and the `active_months` values, in 1.2 `days` and `heating_degree_days`, in 1.3 `year` (milestones and timeshare allocations) and `guests`, and in 1.4 `start_year` (goals)). Points and cents per point are strings. Paycheck deposit rows may carry the income stream key in `recurring_key`; the Cash Flow Income tab counts deposits by it.
- CSV: `newline=''` when opening, `csv.writer(f, lineterminator='\n')`.
- Every list is sorted by its identity (section 5.1). No duplicates.
- CSV rows are sorted by `txn_date`, then `amount` as a number, then `description`, then the other columns in header order.
- The same inputs must give the same bytes, apart from `created_at`.

## 4. Value formats

| Format | Rule | Example |
|---|---|---|
| Money | `^-?[0-9]+\.[0-9]{2}$`. Exactly 2 places. No `$`, no commas. | `-1234.50` |
| Amount | Money that is zero or more (`money-sign`). Used in the 1.1, 1.2, 1.3 and 1.4 sections. | `79.99` |
| Quantity, price | `^-?[0-9]+(\.[0-9]{1,6})?$`. Up to 6 places. | `12.345678` |
| Usage | A quantity that is zero or more (`money-sign` when negative). Utility usage (1.2), and points, miles and cents per point (1.3). | `84250` |
| Rate | Percent with up to 3 places. | `24.99` |
| Date | A real date as `YYYY-MM-DD`. | `2026-09-30` |
| Month | `YYYY-MM`. | `2026-10` |
| Key | Lowercase words joined by single dashes. Starts with a letter. 40 characters at most. No run of 3 or more digits, except the whole words `401k`, `403b` and `529`. | `alex-401k` |
| last4 | 3 or 4 digits. Never more. Cut a 6-digit "Ending" value to its last 4. | `8812` |
| Text | 1 to 500 characters, no control characters, no line breaks. Must pass the redaction rules (section 9). | `Corner Grocery` |
| sha256 | 64 lowercase hex characters. | |
| CUSIP | 9 characters with a valid check digit. | |
| Symbol | Uppercase letters, digits, dots or dashes, starting with a letter. 12 at most. Only when the statement prints it. | `BRK.B` |

Signs, the same everywhere: negative means money out of a cash account, or a new charge or debt on a card or loan. Positive means money into a cash account, or a payment, credit or refund on a card or loan. Balances owed are negative. Investment accounts use the change to the account's cash (a buy is negative, a dividend is positive).

Display strings like `Oct` or `Sep 30` never go in data fields.

## 5. snapshot.json

Top level:

| Field | Rule |
|---|---|
| `schema_version` | `"1.4"` (new runs), `"1.3"`, `"1.2"`, `"1.1"` or `"1.0"`. See section 14. |
| `run_id` | Equals the folder name. |
| `mode` | `monthly`, `backfill` or `correction`. |
| sections | Below. Leave a section out when it has nothing for this run, unless the mode requires it. |

| Kind | Sections | How the app applies them |
|---|---|---|
| Registry | `people`, `accounts`, `new_accounts` | Every mode. Upsert. Per field, the newer run wins, except that a backfill run only adds people and accounts not yet known and never changes known ones. An account ends only by `active: false` or `closed_on`, never because a run left it out. |
| History | `statements`, `balances`, `holdings`, `income.paychecks`, `contributions`, from 1.2 `utility_bills`, and from 1.3 `trips` | Every mode. Statements follow section 11. Other items: the newest run wins per identity. |
| Check | `net_worth`, `cashflow_actual` | Every mode. The app compares them with its own numbers. |
| State | `meta`, `cashflow_plan`, `cashflow_events`, `cashflow_phases`, `income.income_streams`, `budgets`, `payoff_sequence`, `todos`, `review`, from 1.1 `recurring`, `installment_plans`, `flags`, `pending`, from 1.3 `rewards`, and from 1.4 `goals` | Monthly runs only. Rejected in backfill and correction runs. The newest monthly run that has a section wins. Items left out of that run count as ended or resolved. For `cashflow_plan` and `budgets`, month M comes from the newest monthly run with that section whose `meta.this_month` is on or before M, so past months keep the plan they had. |

Required sections:

| Mode | Required |
|---|---|
| monthly | `meta`, `people`, `accounts`, `statements`, `balances`, `holdings`, `income`, `net_worth`, `cashflow_actual`, `cashflow_plan`, `cashflow_events`, `budgets`, `todos`, `review`. From 1.1 also `recurring`, `installment_plans`, `flags`, `pending`. From 1.3 also `rewards`. |
| backfill | `people`, `accounts`, `statements`, `balances`, `holdings`, `income` |
| correction | `statements` (the corrected ones) |

A 1.0 run simply has no 1.1 sections, a 1.0 or 1.1 run has no `utility_bills`, a run before 1.3 has no `rewards` or `trips`, and a run before 1.4 has no `goals` and none of the fields and values marked (1.4). A run that carries a section, field or value newer than its `schema_version` is rejected (`section-version`), even a section or field written as `null`. `utility_bills` (1.2), `trips` (1.3) and `goals` (1.4) are never required.

A required section may be an empty list when there is nothing to report. Any field not listed below is rejected. Leave optional fields out instead of writing `null`.

### 5.1 Identity and sort order

Each list is sorted by its identity, with no two items the same.

| List | Identity (sort order) |
|---|---|
| `people`, `accounts`, `new_accounts` | `key` |
| `statements` | `account_key`, `period_end` |
| `balances` | `account_key`, `as_of`, `source` |
| `holdings` | `account_key`, `period_end`, `key` |
| `income.paychecks` | `person`, `pay_date`, `kind`, `seq` |
| `income.income_streams` | `key` |
| `contributions` | `person`, `account_key`, `kind`, `source`, `date` |
| `cashflow_actual`, `cashflow_plan` | `month` |
| `cashflow_events`, `cashflow_phases`, `payoff_sequence`, `todos` | `key` |
| `recurring`, `installment_plans`, `flags`, `pending`, `goals` (1.4) | `key` |
| `utility_bills` | `utility_key`, `period_end` |
| `utility_bills[].lines` | `service` (one line per service) |
| `utility_bills[].credits` | `label`, then `amount` as a number |
| `rewards.programs`, `rewards.benefits`, `rewards.milestones`, `rewards.routing`, `rewards.redemptions` | `key` |
| `rewards.programs[].earned_on`, `rewards.benefits[].category_keys`, `goals[].accounts` (1.4) | the keys themselves |
| `rewards.programs[].allocations` | `year` (one per year) |
| `rewards.benefits[].cycles` | `start` |
| `trips` | `key` |
| `trips[].items`, `trips[].ious` | `key` (unique in the trip) |
| `trips[].people` | the person keys themselves |
| `flags[].updates` (1.4) | `on`, then `text` (oldest first) |
| `accounts[].rate_changes` (1.4) | `effective_on` (oldest first) |
| `budgets` | `month`, `key` |
| `by_group`, `parts` lists | `key` |

### 5.2 Registry

**people[]**

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. For example `alex`, `joint`, `trust`. |
| `display_name` | yes | Text. |
| `role` | yes | `adult`, `child`, `trust` or `joint`. |
| `aliases` | | List of text. |

**accounts[]**: the full registry every time it is sent.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key, named by person and custodian, never by employer. |
| `display_name` | yes | Text, like `Alex 401k (Lakeview)`. |
| `institution` | yes | Text. |
| `type` | yes | `checking`, `savings`, `credit_card`, `store_card`, `loan`, `mortgage`, `heloc`, `brokerage`, `retirement`, `529`, `hsa`, `custodial`, `nqdc`, `property`, `vehicle`, `other`. |
| `owner` | yes | A person key. |
| `ownership` | yes | `individual`, `joint`, `trust`, `custodial`. |
| `group` | yes | `cash`, `credit_cards`, `investments`, `retirement`, `real_estate`, `vehicles`, `loans`, `other`. |
| `is_asset` | yes | true or false. |
| `include_in_net_worth` | yes | true or false. |
| `statement_cadence` | yes | `monthly`, `quarterly`, `report`, `none`. |
| `nw_group` | if in net worth | Key of the net worth group, like `alex-retirement`. |
| `nw_group_label` | | Text label for that group. |
| `subtype`, `custodian`, `advisor`, `servicer`, `policy_note`, `notes` | | Text. |
| `in_cashflow` | | Leave out for the default: true for checking and cards, false otherwise. |
| `tracked_by` | | `statements` (the default when left out) or `payments`. See "Cards tracked by payments" below. |
| `stale_after_days` | | Whole number, 1 or more. For cadence `none`. |
| `last4` | | 3 or 4 digits. |
| `opened_on`, `closed_on`, `payoff_date`, `fee_renews_on` | | Date. |
| `active`, `dormant` | | true or false. |
| `display_order` | | Whole number, 0 or more. |
| `priority` | | Whole number, 1 or more. |
| `apr` | | Rate. On a checking or savings account, the APY as printed. |
| `rate_type` | | `fixed`, `variable`, `promo`. |
| `credit_limit`, `min_payment`, `annual_fee` | | Money. |
| `autopay` | | `minimum`, `statement`, `full`, `none`. |
| `cycle_close_day` | | 1 to 31. |
| `secured_by` | | Account key of the home or vehicle a loan is secured by. |
| `original` (1.4) | | Amount, above zero. The loan amount when it was made, like a plan's `original`. Only on a `loan` or `mortgage` account (`debt-terms`). A HELOC keeps using `credit_limit`. |
| `rate_changes` (1.4) | | List of up to 4 scheduled rates, oldest first. Each has `effective_on` (Date) and `apr` (Rate), both required. Sorted by `effective_on`, no repeats (`sort-order`, `key-duplicate`). A list with items needs `apr` and `rate_type: variable` in the same account item (`debt-terms`): the rule checks the account as sent, not what the app stored. `[]` clears the schedule and is fine on any account. Left out, the app keeps the schedule it has. |

**new_accounts[]**: every account key that no earlier run has. Each key must also be in `accounts`.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `opened_on` | | Date. |

**Cards tracked by payments.** A store card with no regular statements has `tracked_by: payments`, `statement_cadence: none`, `include_in_net_worth: false` and `in_cashflow: true`. Its payments from checking are the spending ("payment basis"):
- In the paying account's CSV: `txn_type: payment`, `is_transfer: false`, `counterparty_account_key` = the card, and a spending category (for example `clothing`). Anything else is rejected (`tracked-payment`). The description is the payee text only.
- For a cycle where you export that card's statement, its rows count instead, and the payment that pays that cycle is a normal card payment (`is_transfer: true`).
- Such a card never has `balances` rows with source `statement` (`tracked-balance`) and is never in net worth (`tracked-net-worth`).

An account key used anywhere (statements, balances, holdings, contributions, `secured_by`, budget cards, CSV `counterparty_account_key`) must be in an earlier run or in `new_accounts`. A person key used anywhere must be in `people` in this run or an earlier one.

### 5.3 History

**statements[]**: one per account per period. Only accounts produce statements. Pay stubs, utility bills and insurance bills are not statements.

| Field | Req | Rule |
|---|---|---|
| `account_key` | yes | Key. |
| `period_start`, `period_end` | yes | Date. Start on or before end. `period_end` is the closing date. A point-in-time report uses its as-of date for both. |
| `txn_count` | yes | Rows in this statement's CSV. 0 when there is no CSV. |
| `closing` | yes | Money, signed. Owed is negative. |
| `reconciled` | yes | true, or false with a `reconcile_note`. |
| `opening` | if rows | Money, signed. May be left out only when `txn_count` is 0. |
| `balance` | | Money. The total owed when it differs from `closing` (on some cards the statement's Account Total or Total Balance: plan balances included). |
| `credits` | | Money. Sum of positive rows. |
| `debits` | | Money, negative. Sum of negative rows. |
| `txn_sum` | | Money. Sum of all rows. |
| `fees`, `interest` | | Money, signed: negative when charged, positive when refunded. |
| `min_due`, `credit_limit` | | Money. |
| `net_flows`, `market_change` | investments | Money. Required to reconcile brokerage, retirement, 529, HSA, custodial and nqdc statements. |
| `statement_date`, `due_date` | | Date. |
| `reconcile_note`, `flags_text` | | Text. |
| `ocr`, `has_holdings` | | true or false. Set `has_holdings: true` when the statement lists positions. |
| `source_sha256` | | sha256 of the source PDF. Two statements from one PDF share it. |

**balances[]**

| Field | Req | Rule |
|---|---|---|
| `account_key` | yes | Key. |
| `as_of` | yes | Date. |
| `source` | yes | `statement`, `valuation`, `manual`, `estimate`. |
| `balance` | yes | Money, signed. |
| `method` | | `comps`, `kbb`, `report`, `manual`. For valuations. |
| `source_note` | | Text. |

A `statement` balance needs a statement in this run with the same `account_key` and `period_end` equal to `as_of`. It must equal that statement's `balance`, or its `closing` when there is no `balance`.

A `kbb` valuation is the KBB value alone. Never add a camper conversion or other build-out to it: the app holds that value (Settings > Vehicles), adds it on top and lets it lose value at its own rate. A vehicle valuation with any other method (for example `manual`) is taken as the whole vehicle, build-out included, and the app adds nothing to it.

**holdings[]**: the complete position list for each statement with `has_holdings: true`. A holding missing from the list is zero. A statement with `has_holdings: true` and no holdings means everything was sold.

| Field | Req | Rule |
|---|---|---|
| `account_key`, `period_end` | yes | The statement it belongs to. |
| `key` | yes | Key, unique within the statement, stable across statements. |
| `name` | yes | Text. Never a stock plan, ESPP, RSU or unvested item. |
| `value` | yes | Money. |
| `quantity`, `price` | | Quantity. Leave both out for a value-only holding. |
| `symbol` | | Only when the statement prints it. Never infer a ticker from a fund name. |
| `cusip` | | When the statement prints it. |
| `cost_basis` | | Money. |
| `asset_class` | | Key, like `us-stock`, `bond`, `cash`, `target-date`. |

**income** (object)

| Field | Req | Rule |
|---|---|---|
| `paychecks` | yes | List, may be empty. One per deposit advice. |
| `income_streams` | | State. Monthly runs only. |

**income.paychecks[]**: no employer name, no deposit account.

| Field | Req | Rule |
|---|---|---|
| `person` | yes | Person key. |
| `pay_date` | yes | Date. |
| `kind` | yes | `regular`, `bonus`, `draw`. |
| `seq` | yes | 1, 2, ... among advices with the same person, pay_date and kind, in order of `gross`, smallest first. Usually 1. |
| `gross`, `net` | yes | Money. |
| `period_start`, `period_end` | | Date. |
| `k401`, `hsa_employee`, `hsa_employer`, `pretax_health` | | Money for this advice. |
| `k401_pct` | | Rate. |
| `ytd_k401`, `ytd_hsa_employee`, `ytd_hsa_employer` | | Money, year to date from the stub. |

**contributions[]**

| Field | Req | Rule |
|---|---|---|
| `person` | yes | Person key. |
| `account_key` | yes | Account key. |
| `kind` | yes | `401k_employee`, `401k_employer`, `hsa_employee`, `hsa_employer`, `ira`, `529`. |
| `source` | yes | `current_employer`, `prior_employer`, `personal`, `gift`. |
| `date` | yes | Date. One item per identity. Add same-day amounts together. |
| `amount` | yes | Money. |

**Tombstones.** To retract a balance, paycheck, contribution or utility bill sent in an earlier run, send its identity fields plus `"deleted": true` and nothing else:

```json
{"account_key": "family-car", "as_of": "2026-06-30", "deleted": true, "source": "valuation"}
```

Do not write `"deleted": false`. Statements are never retracted. Send a correction instead. (Statement tombstones are not in 1.0. They wait on the owner's decision.)

### 5.4 Check sections

**net_worth** (object)

| Field | Req | Rule |
|---|---|---|
| `as_of` | yes | Date. |
| `value`, `assets` | yes | Money. |
| `liabilities` | yes | Money, written as a positive number. |
| `parts` | yes | List of `{key, amount}` per `nw_group`, sorted by key. A loan, mortgage or HELOC secured by an asset in net worth counts in that asset's group, so Home equity and Vehicle equity are net of their loans. The parts add up to `value`. |

**cashflow_actual[]**

| Field | Req | Rule |
|---|---|---|
| `month` | yes | Month. |
| `income`, `spending` | yes | Money, positive. `spending` leaves out saved and invested moves. |
| `saved_invested` | | Money. Moves into savings, brokerage, 529, UTMA and the kids' debit cards, net. |
| `by_group` | yes | List of `{key, amount}`, sorted by key. |

### 5.5 State sections

**meta** (object): `updated` (date, required), `this_month` (month, required), `complete_as_of` (date), `location_label` (text).

**cashflow_plan[]**

| Field | Req | Rule |
|---|---|---|
| `month` | yes | Month. |
| `planned_income` | yes | Money, positive. |
| `planned_spending` | yes | Money, positive. It **includes** the planned moves into savings, brokerage, 529, UTMA and the kids' debit cards (the `saved-invested` group). |
| `by_group` | yes | List of `{key, amount}`, sorted by key. Keys are spending group keys from `categories.json`, plus `saved-invested` and `plan-installments`. The amounts add up to `planned_spending` exactly. |
| `note` | | Text. |

So the plan's left over is `planned_income - planned_spending`. The app shows `saved-invested` as its own line, not as spending. It compares `planned_spending` with actual spending plus actual saved and invested. The app warns (rule `plan-groups-sum`) when `by_group` does not add up to `planned_spending`.

**cashflow_events[]**: `key`, `effective_month`, `delta` (money), `label` (all required), `reason` (text), `linked_kind` (key), `linked_key` (key), and from 1.4 `status`.

- `status` (1.4): how sure the change is. `confirmed` (scheduled, or seen on a statement), `expected` (likely, not confirmed), `target` (a goal the owner set) or `suggested` (the chat's idea). Left out shows no badge.

**cashflow_phases[]**: `key`, `label`, `start_month`, `surplus` (money) (all required), `end_month`, `reason`, `note`.

**income.income_streams[]**: `key`, `person`, `label` (no employer name), `kind` (`paycheck`, `benefit`, `draw`, `bonus`), `schedule` (text), `expected_net` (money) (all required), `start_month`, `end_month`, `note`.

**budgets[]**: `key`, `month`, `label`, `target` (money) (all required). Optional: `category_key` (from `categories.json`), `scope` (object with `owner` person key, `cards` list of account keys, `merchant` text, `exclusions` list of text), `period_basis` (`calendar` or `card_cycle`, default `calendar`), `cycle_note`, `plan` (money), `reported_actual` (money), `actual_source` (`statement` or `baseline`), `statements_needed` (list of account keys), `note`.

**payoff_sequence[]**: `key`, `when_label`, `action` (all required), `month`.

**todos[]**: `key`, `title`, `status` (`open` or `done`) (all required), `subtitle`, `due_date`, `flag_key`.

**review** (object): `summary` (text, required), `key_numbers` (list of `{label, value}` with money values), `changes`, `anomalies` (lists of text), `top_three` (list of text, 3 at most).

### 5.6 State sections added in 1.1

Monthly runs only, and required in monthly runs from 1.1. Each is a list sorted by `key`. Send every current item each time, including resolved flags and paid off plans you still want shown. The app reads them as they are and never changes them, except that it masks text that matches the private lists (section 9) when those lists change.

**recurring[]**: bills and subscriptions that repeat.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. Use the same key as `recurring_key` in the CSV rows it pays. |
| `name` | yes | Text. A personal subscription is `Personal subscription`. |
| `category_key` | yes | From `categories.json`. |
| `amount` | yes | Amount, per occurrence. For a `variable` bill, a typical amount. |
| `cadence` | yes | `weekly`, `biweekly`, `every_n_weeks`, `semi_monthly`, `monthly`, `quarterly`, `semester`, `semi_annual`, `annual`. |
| `status` | yes | `active`, `paused`, `ending` (cancelled, runs until `active_through`), `ended`. |
| `interval_n` | | Whole number, 2 to 52. Required for `every_n_weeks`, not allowed otherwise (`recurring-interval`). |
| `active_months` | | List of month numbers 1 to 12, ascending, no repeats (`active-months`). Leave out when it runs all year. A seasonal bill lists its months. A `semester` bill lists the months it is charged. |
| `next_date` | | Date of the next charge. |
| `start_date`, `end_date` | | Date. Start on or before end (`date-order`). |
| `renewal_date` | | Date a yearly plan or fee renews. |
| `active_through` | | Date the last paid period ends, for an `ending` item. |
| `review_by` | | Date to decide on it by. |
| `account_key` | | The account it is charged to. |
| `payment_rail` | | `card`, `ach`, `bill_pay`, `check`, `cash`, `payroll`. |
| `preferred_account_key` | | The account it should move to, when that differs. |
| `owner` | | Person key. |
| `variable` | | true when the amount changes each time (utilities). |
| `credit_offset` | | Amount. A card credit that pays back part of each charge. |
| `credit_note` | | Text. Which credit. |
| `notes` | | Text. |
| `keep` (1.4) | | Only `true`. the owner chose to keep it after a review. The why goes in `notes`. With `review_by` it means kept for now, review by that date. Only on an `active` or `paused` item (`recurring-keep`). |
| `tag` (1.4) | | `streaming`, `hobbies` or `software`. Splits subscriptions into sections, with no new categories. Never on the personal subscription (`recurring-tag-private`): a tag would hint at what it is. |

The app's monthly equivalent is `amount` times occurrences per year, divided by 12. Occurrences per year: weekly 52, biweekly 26, every_n_weeks 52 / `interval_n`, semi_monthly 24, monthly 12, quarterly 4, semester 2, semi_annual 2, annual 1. With `active_months`, only those months count. A weekly to monthly bill counts its active months (a monthly bill with 7 active months is 7 a year). A semester bill counts one charge per listed month. A quarterly, semi_annual or annual bill steps from its date (`next_date`, else `start_date`, else `renewal_date`) and counts only the steps that land in a listed month (a quarterly bill on Apr 15 with months 4 to 10 is Apr, Jul and Oct, so 3 a year).

An `active` item with no `next_date`, `start_date` or `renewal_date` has no due date. It is a warning (`recurring-no-date`, 1.4). The run is imported.

**installment_plans[]**: Plan It, Pay Over Time, Apple installments, 0% promos and deferred interest balances.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. The same key as `plan_key` in the CSV rows. |
| `account_key` | yes | The card that holds the plan. |
| `kind` | yes | `plan_it`, `pay_over_time`, `promo_0pct`, `deferred_interest`, `apple_installment`. |
| `name` | yes | Text. |
| `created_on` | yes | Date. |
| `original` | yes | Amount. The plan amount when it was made. |
| `status` | yes | `active`, `paid_off`, `closed`. |
| `monthly_payment` | | Amount, without the fee. |
| `monthly_fee` | | Amount. Plan It fee per month. |
| `total_fees` | | Amount. All fees over the plan. |
| `payments_total` | | Whole number, 1 or more. |
| `payments_left` | | Whole number, 0 or more. Not more than `payments_total` (`plan-payments`). |
| `remaining` | | Amount still owed on the plan. Information only: the card's `balance` already holds it, so the app never adds it to net worth. |
| `end_month` | | Month of the last payment. The app uses it for plan step-downs. |
| `promo_expiry` | | Date. Required for `promo_0pct` (`plan-dates`). |
| `pay_before` | | Date to pay in full to avoid back interest. Required for `deferred_interest` (`plan-dates`). |
| `included_in_spending_plan` | | true when its payment is in the plan's `plan-installments` group. |
| `components` | | List of the purchases in the plan, in statement order: `merchant` (text, required), `amount` (amount, required), `trip_key` (key). The amounts add up to `original` (`plan-components`). |
| `note` (1.4) | | Text. The payoff plan, an early payoff, a refund, or what the plan is for. |
| `paid_off_on` (1.4) | | Date it was paid off. Only on a `paid_off` or `closed` plan, on or after `created_on` (`plan-dates`). |

**flags[]**: the chat's flags. Read-only in the app.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `severity` | yes | `high`, `medium`, `low`. |
| `status` | yes | `open`, `watching`, `resolved`. A resolved flag needs `resolved_on` (`flag-resolved`). |
| `kind` | yes | `debt`, `tax`, `budget`, `subscription`, `fraud`, `travel`, `rewards`, `cost_cutting`, `milestone`. From 1.4 also `retirement` (401k, IRA, rollover, pension), `investment` (brokerage, 529, custodial, fund moves), `insurance` (policies, premiums, claims), `medical` (care costs, HSA, FSA), `payroll` (withholding, deferral, pay changes; never the employer's name), `home` (repairs, upkeep, the property) and `other` (only when nothing else fits). A reimbursement is an `action`, not a kind. |
| `title` | yes | Text. |
| `created_on` | yes | Date. |
| `body` | | Text. |
| `related_kind`, `related_key` | | Both or neither (`flag-related`). Kind: `account`, `recurring`, `plan`, `budget`, `todo`, `pending`, `trip`, `category`, `person`. The key is a Key. Account, person and category keys must be known. |
| `due_date` | | Date. |
| `resolved_on` | | Date, on or after `created_on` (`date-order`). |
| `archived` | | true to hide it from the main list. |
| `action` (1.4) | | What to do: `act` (the owner has something to do), `ask` (a question to ask someone), `reimburse` (money to claim back) or `info` (nothing to do, ever). Left out means `act`, so older runs count as before. `watching` stays the status for a flag that may need action later. |
| `updates` (1.4) | | List of 1 to 20 dated notes, oldest first. Each has `on` (Date) and `text` (Text), both required. Sorted by `on`, then `text`, no repeats (`sort-order`, `key-duplicate`). Each `on` is on or after `created_on` and on or before `meta.updated` (`date-order`). `body` stays the short summary. Send the whole list every run. |

**pending[]**: changes waiting to be applied or confirmed. Read-only in the app.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `kind` | yes | `apply` or `confirm`. |
| `subject` | yes | Text. |
| `status` | yes | `open`, `waiting`, `done`. |
| `added_on` | yes | Date. |
| `detail` | | Text. |
| `updated_on` | | Date, on or after `added_on` (`date-order`). |

### 5.7 History section added in 1.2: utility bills

**utility_bills[]**: one item per utility bill (City Power gas and electric monthly, a water bill quarterly). A history section, like `income.paychecks`: allowed in monthly, backfill and correction runs, never required. The newest run in run order wins per `utility_key` and `period_end`. Send `{"deleted": true, "period_end": ..., "utility_key": ...}` to retract one. Utility bills are never `statements`, and their payments stay rows in the paying account's CSV.

Bill history before 2026 (back to 2021) can come in backfill runs. No date in a bill may be before 2000-01-01 (`utility-date`).

| Field | Req | Rule |
|---|---|---|
| `utility_key` | yes | Key. The same key as `recurring_key` on the payment rows and the `recurring` item. City Power: the key of the existing City Power `recurring` item, else `city-power`. Water: its recurring key, else the key earlier runs used for it (`city-water` for a first run). |
| `provider` | yes | Text, like `City Power`. |
| `bill_date` | yes | Date. |
| `period_start`, `period_end` | yes | Date. The service period. `period_start` is before `period_end` (`utility-period`). |
| `days` | yes | Whole number, 1 or more. Days in the period as the bill prints it. |
| `total` | yes | Amount. What was billed for this period, not the account balance. |
| `lines` | yes | List, at least one, sorted by `service`, one line per service (`key-duplicate`). Charges not tied to one service, like a tax or fee on the whole bill, go together in one `other` line with unit `none` and usage `0`. See below. |
| `due_date` | | Date. |
| `paid_from` | | Account key of the account or card that pays it, when known. Must be known (`account-unknown`). |
| `heating_degree_days` | | Whole number, 0 or more. |
| `estimated_read` | | true or false. true when the bill says the meter read was estimated. |
| `credits` | | List of `{label, amount}`, sorted by `label` as plain text (capital letters before small ones, as Python's `sorted` does), then `amount`. Two credits with the same label and amount are a duplicate (`key-duplicate`): add them into one. `label` is text, like `Smart thermostat credit`. `amount` is money and negative (`utility-credit-sign`), like `-2.19`. |
| `note` | | Text. |

**utility_bills[].lines[]**

| Field | Req | Rule |
|---|---|---|
| `service` | yes | `electric`, `gas`, `water`, `sewer`, `stormwater`, `other`. |
| `usage` | yes | Quantity, 0 or more (`money-sign` when negative). |
| `unit` | yes | `kwh`, `therms`, `gallons`, `ccf`, `none`. It must fit the service (`utility-unit`): electric `kwh`, gas `therms`, water and sewer `gallons` or `ccf`, stormwater and other any unit. Use `none` with usage `0` for a flat fee. |
| `charges` | yes | Amount. Everything billed for that service: fixed charge, usage charges, riders and taxes. |
| `fixed_charges` | | Amount. The service point or customer charge part, when the bill splits it out. Not more than `charges` (`utility-fixed`). |

Checks:
- The sum of `lines[].charges` plus the sum of `credits[].amount` equals `total`. Off by 1 or 2 cents (rounding on the bill) is a warning (`utility-total-rounding`). Off by more is rejected (`utility-total`).
- `days` equals `period_end` minus `period_start` in days (City Power counts it that way). If not, a warning (`utility-days`).
- Two bills of one utility in the same run whose periods overlap: a warning on the later one (`utility-overlap`). Periods may share a boundary day: one bill's `period_end` can be the next one's `period_start`. The app's Checks page also shows overlaps between bills from different runs.
- A bill that breaks more than one rule may get more rule ids from `check_run.py` than from the app. Both reject the run.

Privacy: a utility bill export holds no account number, no meter number, no service address and no customer name. A utility's history file has all four. Drop them before writing. Text fields pass the redaction rules (section 9) like every other field.

### 5.8 State section added in 1.3: rewards

**rewards**: the chat's current view of points, card benefits, spend milestones, card routing and redemptions. One object with the lists `programs`, `benefits`, `milestones`, `routing` and `redemptions` (all required, may be empty) and `highlights` (optional). Monthly runs only, required in monthly runs from 1.3, rejected in backfill and correction runs (`section-not-allowed`). The newest monthly run wins whole: items left out count as ended, so send every current item each time. Each list is sorted by `key` with no duplicates (`key-duplicate`, `sort-order`).

Annual fees stay out of `rewards`. They come from `recurring` items (category `annual-fees`, plus `memberships` with cadence `annual`) and the registry's `annual_fee` and `fee_renews_on`.

**rewards.programs[]**

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `name` | yes | Text, like `Summit Points`. |
| `kind` | yes | `points`, `miles`, `cash_back`, `timeshare`. |
| `group` | yes | `primary` or `secondary`: which block on the page. |
| `balance` | | Usage. Points or miles. Dollars for `cash_back`. |
| `as_of`, `source` | | Date, and `statement` or `manual`. Both required with `balance`, not allowed without it (`reward-as-of`). A statement balance is dated the period end. A manual balance is dated the day the owner gave it. |
| `owner` | | Person key (`person-unknown`). |
| `earned_on` | | Account keys of the cards that earn it, sorted (`account-unknown`). |
| `status_tier` | | Text. |
| `status_via` | | Account key of the card that grants the status (`account-unknown`). |
| `cpp_low`, `cpp_high` | | Usage, cents per point. Both or neither, low not more than high, and never on `cash_back`, where the value is the balance (`reward-value-range`). |
| `expires_on` | | Date. |
| `best_uses`, `avoid` | | Lists of text, 5 at most, in the chat's order. |
| `note` | | Text. |
| `allocations` | | Timeshare only (`reward-allocation`). Sorted by `year`, one per year. See below. |

**rewards.programs[].allocations[]**

| Field | Req | Rule |
|---|---|---|
| `year` | yes | Whole number, 2000 to 2100. |
| `allocated`, `used` | yes | Usage. |
| `pulled_forward` | | Usage. Points of this year's allocation already borrowed into the year before. `used` plus `pulled_forward` is not more than `allocated` (`reward-allocation`). |
| `expires_on`, `book_by` | | Date. |

**rewards.benefits[]**: a card benefit. Each kind has its own fields. A field from another kind is rejected, and so is a missing field the kind requires (`reward-benefit-fields`).

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `account_key` | yes | The card (`account-unknown`). |
| `name` | yes | Text, like `Dining credit`. |
| `kind` | yes | `credit`, `earning_cap`, `status_perk`, `free_night`, `other`. |
| `cadence` | credit | `monthly`, `quarterly`, `semi_annual`, `annual`, `multi_year`, `one_time`. Credit only. |
| `amount` | credit | Amount per cycle. Credit only. |
| `cycles` | | Credit only. This year's cycles, sorted by `start`. See below. |
| `counted_value` | | Amount per year the household really gets. Used in the fees table. Leave out to count the full value. |
| `auto`, `enrolled`, `rolls_over` | | true or false. Leave out when unknown. |
| `enroll_by` | | Date. |
| `ends_on` | | Date: a benefit going away, or a free night's expiry. |
| `how_to_use`, `note` | | Text. |
| `cap` | earning_cap | Amount per calendar year. |
| `earn_before`, `earn_after` | earning_cap | Text, like `4x` or `5%`, as in `routing.earn`. |
| `category_keys` | earning_cap | Category keys, sorted (`category-unknown`). |
| `reported_spent` | | Earning cap only. Amount, with `as_of` (Date). Both or neither (`reward-as-of`). |
| `program_key` | status_perk, free_night | A program in `rewards.programs` (`reward-program-unknown`). |
| `status_tier` | status_perk | Text. |

Fields in more than one kind: `counted_value`, `auto`, `enrolled`, `rolls_over`, `enroll_by`, `ends_on`, `how_to_use` and `note` go on any kind. `other` has only those.

**rewards.benefits[].cycles[]**

| Field | Req | Rule |
|---|---|---|
| `start`, `end` | yes | Date. Start on or before end (`date-order`). |
| `amount` | | Amount, only when this cycle differs, like a bigger December. |
| `used` | | Amount used. Leave out for none. |
| `used_on` | | Date. |

The app works out each cycle's state from dates and amounts: done (used reaches the amount), partial, open, or expired after `end`.

**rewards.milestones[]**: a card spend milestone. Two thresholds on one card are two items.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `account_key` | yes | The card (`account-unknown`). |
| `year` | yes | Whole number, 2000 to 2100. |
| `threshold` | yes | Amount. |
| `reward` | yes | Text. |
| `status` | yes | `open`, `met`, `missed`. |
| `reported_spent`, `as_of` | | Amount and Date, both or neither (`reward-as-of`). |
| `met_on` | | Date. |
| `plan` | | Text: the routing strategy to reach it. |
| `note` | | Text. |

**rewards.routing[]**: which card to use for which spending.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `spend` | yes | Text, like `Dining and groceries`. |
| `account_key` | yes | The card (`account-unknown`). |
| `display_order` | yes | Whole number, 0 or more. |
| `earn` | | Text, like `4x`. |
| `note` | | Text. |

**rewards.redemptions[]**

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `program_key` | yes | A program in `rewards.programs` (`reward-program-unknown`). |
| `title` | yes | Text. |
| `status` | yes | `ready`, `consider`, `done`. |
| `points_needed` | | Usage. The app shows the balance it has from the program, so it never goes stale. On a `done` redemption, the points it used. |
| `trip_key` | | Key of a trip (warning `trip-unknown` when no run defines it). |
| `note` | | Text. |
| `done_on` (1.4) | | Date it was used. Only on a `done` redemption (`reward-done-on`). |

**rewards.highlights**: list of text, 5 at most, in order. The banner news.

A balance or `reported_spent` with `as_of` more than 90 days before the run date is a warning (`reward-stale`). The run is imported.

### 5.9 History section added in 1.3: trips

**trips[]**: one item per trip, sorted by `key`. A history section: allowed in monthly, backfill and correction runs, never required. The newest run in run order wins per `key`: the whole trip, with its items and IOUs. Send `{"deleted": true, "key": ...}` to remove one. Send a trip each run while it changes. Once it is `final`, stop sending it: the app keeps it. Old trips, before 2026 too, can come in backfill runs.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key, the same value as `trip_key` on rows. Pattern `<place>-<mon><yy>`, like `lake-jul26`. |
| `name` | yes | Text. |
| `status` | yes | `planning`, `booked`, `in_progress`, `complete`, `final` (all statements captured). |
| `items` | yes | List, may be empty, sorted by `key`. See below. |
| `start_date`, `end_date` | | Date. Both, or `month` alone: exactly one of the two (`trip-dates`). Start on or before end (`date-order`). |
| `month` | | Month, for a trip without dates yet. |
| `people` | | Person keys of the household members going, sorted (`person-unknown`). |
| `guests` | | Whole number, 0 to 20: people outside the household. Never their names. |
| `vehicle` | | Text. |
| `estimate_low`, `estimate_high` | | Amount. The estimate before the trip, for "est. was". Both or neither, low not more than high (`trip-estimate`). |
| `ious` | | List sorted by `key`. See below. |
| `note` | | Text. |

No trip date may be before 2000-01-01 (`trip-dates`).

**trips[].items[]**: one cost of the trip.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key, unique in the trip. |
| `kind` | yes | `flight`, `lodging`, `rental_car`, `gas`, `food`, `activities`, `tickets`, `shopping`, `insurance`, `parking`, `transit`, `cash`, `supplies`, `fees`, `other`. |
| `label` | yes | Text. |
| `status` | yes | `posted`, `booked`, `estimate`, `tbd`, `excluded`. |
| `tender` | yes | `cash`, `points`, `free_night`, `points_credit`, `covered`. |
| `amount` | | Amount. Cash paid, for the whole household. For points and free nights, the taxes and fees paid in cash. For `points_credit`, the size of the credit (the app subtracts it). `covered` means someone else paid ("Not ours"). |
| `estimate_low`, `estimate_high` | | Amount. Both or neither, low not more than high (`trip-estimate`). |
| `points` | | Usage, with `program_key` (Key, not checked: backfill runs carry no `rewards`). |
| `program_key` | | Key of the points program. |
| `account_key` | | The card or account that paid (`account-unknown`). |
| `person` | | Person key: the cardholder or traveler (`person-unknown`). |
| `note` | | Text. |
| `due_on` (1.4) | | Date. The day a booked amount is charged or due. Only on a `booked` item (`trip-due`). On such an item, `account_key` is the card it will go on and `amount` is the amount due. A deposit now and the rest later are two items: the deposit `posted`, the rest `booked` with `due_on`. When the charge posts, the item becomes `posted` and `due_on` goes. |
| `maybe` (1.4) | | Only `true`. A cost not booked that may be added. Only on an `estimate` item (with its range) or a `tbd` item (`trip-maybe`). The app leaves it out of the trip totals and lists it as still open. |

Item rules:
- `trip-item-amount`: `posted`, `booked` and `excluded` items need `amount`. An `estimate` item needs both estimate fields and no `amount`. A `tbd` item has neither.
- `trip-tender`: tender `points` needs `points` and `program_key`. Tender `points_credit` needs `amount`.
- `trip-due` (1.4): `due_on` only on a `booked` item.
- `trip-maybe` (1.4): `maybe` only on an `estimate` or `tbd` item.

**trips[].ious[]**: money owed between the household and someone else on the trip.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key, unique in the trip. |
| `direction` | yes | `owed_to_us` or `we_owe`. |
| `counterparty` | yes | Text: a role, like `relative` or `friend couple`. Never a name. |
| `amount` | yes | Amount. |
| `status` | yes | `open`, `settled`, `waived` ("called it a wash"). |
| `settled_on` | | Date. Required when `settled`, not allowed otherwise (`trip-iou-settled`). |
| `item_key` | | An item in the same trip (`trip-item-unknown`). |
| `note` | | Text. |

A `trip_key` on a row, a plan component, a flag with `related_kind` `trip`, or a redemption that names a trip no run so far defines (this run's `trips`, or an earlier run folder's) is a warning (`trip-unknown`), not a reject: backfill rows and old rows may name trips the chat never sent.

### 5.10 State section added in 1.4: goals

**goals[]**: the household's goals, sorted by `key`. Monthly runs only, never required. The newest monthly run that has `goals` wins whole: goals left out of it ended. `[]` clears them. A monthly run that leaves the section out keeps the goals it had. Send the goals every monthly run.

| Field | Req | Rule |
|---|---|---|
| `key` | yes | Key. |
| `name` | yes | Text. A generic name, like `College, older child`. No school names, no ages. |
| `kind` | yes | `college` or `save_up`. A retirement milestone is a `save_up`. |
| `accounts` | yes | 1 to 10 account keys, sorted, no repeats. Progress is their balances. A college goal holds only `529` or `custodial` accounts from this run's `accounts` (`goal-fields`). |
| `target` | yes | Amount. The round target, or for college the total cost in today's dollars. |
| `person` | | Person key. Required for `college`: a person with role `child` in this run's `people` (`goal-fields`). |
| `start_year` | | Whole number, 2000 to 2100: the year college starts. Required for `college`, not allowed on `save_up` (`goal-fields`). |
| `target_date` | | Date to reach the target by. Only on `save_up` (`goal-fields`). |
| `note` | | Text. |

Account and person keys must be known (`account-unknown`, `person-unknown`).

## 6. Transaction CSV

One file per statement with rows: `transactions/<account_key>-<period_end>.csv`. Each file must match a statement in `snapshot.json` and hold at least one row. A statement with rows must have its file. Statements with no rows (a quarterly savings summary, a 401k printout) have no file.

Header, in this exact order:

```
txn_date,post_date,amount,description,merchant,category_key,cardholder,txn_type,is_transfer,counterparty_account_key,is_reimbursable,exclude_from_budget,plan_key,recurring_key,trip_key,hsa_eligible,needs_review,confidence,notes
```

A blank cell means unknown or none.

| Column | Req | Rule |
|---|---|---|
| `txn_date` | yes | Date. Within the period, or up to 60 days before it. |
| `post_date` | | Date. Within the period, or up to 5 days after it. |
| `amount` | yes | Money, signed (section 4). |
| `description` | yes | Text, cleaned. |
| `merchant` | | Text. The cleaned merchant name. |
| `category_key` | yes | From `categories.json`, or `uncategorized`. |
| `cardholder` | | Person key, where the statement splits by cardholder. |
| `txn_type` | yes | `purchase`, `refund`, `payment`, `fee`, `interest`, `transfer`, `deposit`, `withdrawal`, `dividend`, `reinvest`, `buy`, `sell`, `contribution`, `plan_fee`, `adjustment`. |
| `is_transfer`, `is_reimbursable`, `exclude_from_budget`, `hsa_eligible`, `needs_review` | | `true`, `false` or blank. |
| `counterparty_account_key` | | Account key of the other household account in a move. |
| `plan_key`, `recurring_key`, `trip_key` | | Key. |
| `confidence` | | Blank, or a decimal from 0 to 1 with up to 2 places. |
| `notes` | | Text. |

Rules:
- Rows dated before 2026-01-01 are kept for reconciliation and left out of cash flow.
- An installment purchase with a `plan_key` may have a `txn_date` outside the window.
- Moves between household accounts (card payments, internal transfers, loan payments, brokerage, 529 and custodial contributions): `is_transfer=true` and `counterparty_account_key`.
- Refunds and card credits take the category of the purchase they offset, never an income category. Returns, card benefit credits and points credits are all `txn_type: refund` with a positive amount, in that same category. A refund row in an income category is rejected. Reimbursement deposits use `reimbursement`. Mark the original charge `is_reimbursable=true`.
- Plan It, Pay Over Time and Apple installments: the original purchase is `purchase` with its real category, `plan_key` and purchase date. Plan creation, balance moves and monthly installment billing are `adjustment` with `is_transfer=true`. Plan fees are `plan_fee` with category `plan-fees`.
- Personal subscriptions: `description` and `merchant` are `Personal subscription`, category `subscriptions`. Keep amount, date, card and cardholder.
- A reinvest is a pair that nets to zero: `dividend` positive, `reinvest` negative.
- `trip_key` (from 1.3, section 5.9), whatever the row's date: set it on bookings, on-trip charges, gas and food on the way, airport parking, ATM cash at the destination, Plan It purchase rows, plan fees and travel insurance, and points credits. Also set it on repayments from co-travelers that land in an exported account. Never on work travel or reimbursable work charges. Rows already imported keep the `trip_key` they came with. Trip items carry the money. Tagged rows are a check only.

The app computes `occurrence_n` and the row keys itself. Do not add them.

## 7. Statement checks

Skipped when `reconciled` is false (a warning, and that month is never marked complete). `txn_count` is always checked.

- Checking, savings, credit card, store card, loan, mortgage and HELOC, with rows: `opening` + sum of rows = `closing`. If given, `credits` = sum of positive rows and `debits` = sum of negative rows.
- The same types with no rows: `opening` + `credits` + `debits` = `closing` (missing values count as 0).
- Brokerage, retirement, 529, HSA, custodial and nqdc: `opening` + `net_flows` + `market_change` = `closing`.
- No `opening` (a point-in-time report): no reconciliation. Not allowed with rows.
- If given, `txn_sum` = sum of rows.
- Every holding points at a statement in this run with `has_holdings: true`.
- Every statement has its balance row in this run: source `statement`, the same `account_key`, and `as_of` = `period_end` (`statement-balance-missing`). Cards tracked by payments are the exception: they have none.
- A statement with `has_holdings: true` needs the `holdings` section in the run, even if it is an empty list (`holdings-missing`). An empty list means everything was sold.

So a correction run always carries the full statement: its CSV, its balance row and, for an account with holdings, its holdings. A winning statement replaces the balance and holdings of the version it replaces.

## 8. Categories

`categories.json` lists `groups` (key, name, sort, counts_in_spending) and `categories` (key, name, group, emoji, kind, sort). Kinds: `spending`, `income`, `transfer`, `reimbursement`, `uncategorized`. Keys never change. Use `uncategorized` when unsure. Do not guess.

## 9. Redaction

Every text field and every key is checked. A match rejects the whole run. Formatted values (money, dates, hashes, `last4`, CUSIP) are checked by their own format instead.

Text is normalized before the rules run (Unicode NFKC, every digit to its ASCII digit, every dash to `-`, every space to a plain space, zero-width characters removed). So full-width digits, no-break spaces, en dashes and zero-width spaces do not hide a number.

Text must not contain:

| Rule id | What |
|---|---|
| `digits-run` | A run of 5 or more digits. Write amounts in text with commas: `12,345.67`. |
| `digits-split` | Digit groups of 2 or more digits joined by spaces, or by one dot or dash (spaces around it allowed), with 8 or more digits in total. ISO dates, year ranges like `2025-26`, and money like `1,234.56` are not counted. |
| `luhn` | 13 to 19 digits that pass the card number check, with or without separators. Groups may be joined by up to 3 characters that are not letters or digits (spaces, commas, slashes, dashes). |
| `phone` | A phone number like `555-123-4567`, `555/123/4567`, `(555) 123-4567` or `(555)123-4567`. |
| `split-account` | Four digits, a dash, four digits. Write a year range as `2025-26`. |
| `masked-number` | A masked number: `XXXX`, `****`, `#-#####`, `xx1234`, `XX-1234`, `** 1234`, `* * * * 1234`. |
| `email` | An email address. |
| `street-address` | A street address (numbered streets like `3rd` included) or PO box. |
| `stock-plan` | A holding name with stock plan, ESPP, RSU or unvested. |
| `private-subscription`, `private-employer` | A match with the private lists (below). |
| `booking-code` | In `rewards` and `trips` text (from 1.3) and in `flags`, `installment_plans` and `goals` text (from 1.4; the app checks these sections in runs of every version): a token of 6 to 8 capital letters and digits with at least 2 of each, like a record locator, a confirmation code, a claim or order number, or a flight number (two letters and four digits). Codes made only of letters are not caught, so leave those out too. Plan component merchants are checked too: leave marketplace order codes out of them. Rows and the other sections are not checked, since row descriptions keep marketplace order codes that look alike. A word like `COVID19` is caught: write it another way. |

Also: no ZIP codes, routing or member numbers, raw statement headers, issuer phone numbers or source file names. No employer names, employer tools or stock plan rows. Paychecks, 401k and HSA numbers are fine.

Rewards and trips (1.3): no loyalty member numbers, confirmation codes, record locators, flight numbers, seat or ticket numbers, addresses, host names, names of people outside the household, employer names or discount codes. Points go in typed fields, never in text. The personal subscription and employer rules cover every new text field.

Flags, installment plans and goals (1.4): no claim, case, order, booking or member numbers in titles, bodies, `updates`, notes, plan names or component merchants. The personal subscription and employer rules cover every new text field. The item named `Personal subscription` never carries a `tag` (`recurring-tag-private`).

The rules live in `redaction.json`. Version 4 (format 1.4) adds `flags`, `installment_plans` and `goals` to `booking-code`.

**Private lists.** `private_rules.json` holds `personal_subscriptions` and `employer_terms`. It never goes in `data/` or git. Matching ignores case and punctuation: both sides are lowercased and runs of other characters become one space, so `Example-Service` matches `example service`. A term of 5 or more letters and digits also matches with all spaces and punctuation removed. Run the checker with `--private-rules <file>` when a copy exists on that machine.

## 10. Modes

| Mode | Used for | Sections |
|---|---|---|
| `monthly` | The normal monthly run. | Everything. |
| `backfill` | Jan 2026 up to the first monthly export, one month per run. Also the utility bill history (section 5.7) and old trips (section 5.9), for example one run per year, with the other required sections as empty lists. | Registry, history and check. State is rejected. |
| `correction` | Fixing one or more statements already exported. Holds the full corrected statement: its CSV, its balance row and its holdings. It may also carry `trips`. | Registry, history and check. State (`rewards` included) is rejected. |

Export each statement once. Do not export a statement again in a later monthly or backfill run.

## 11. Which statement version wins

Per statement (`account_key` + `period_end`):
1. the latest correction run, in run order, that includes it;
2. if no correction run includes it, the earliest run, in run order, that includes it.

A later monthly or backfill run that sends a statement again loses. The app keeps it and counts it as drift. Arrival order never changes the result.

## 12. The checker

```
python data/_schema/check_run.py data/2026-10-21 --pre      # before manifest.json
python data/_schema/check_run.py data/2026-10-21            # after manifest.json
python data/_schema/check_run.py data/2026-10-21 --private-rules <file>
python data/_schema/check_run.py data/2026-10-21 --json
```

It checks paths, symbolic links, sizes, hashes, row counts, the CSV header, required sections, section, field and value versions, value and key formats, sort order, redaction, statement reconciliation, date windows, utility bill rules, rewards, trips and goals rules and account keys. It looks for account and person keys, and for trip keys (`trip-unknown`), in earlier run folders next to the run. It prints one line per problem, a rule id and a location, then `OK` or `FAILED`. Warnings print the same way with `(warning)` at the end, and in `--json` output they carry `"level": "warning"`. Warnings do not fail the run. It never prints a value. Exit code 0 means pass, 1 means fail. The app uses the same rule ids, and shows the same warnings on the run's page.

## 13. Rule ids

| Rule id | Meaning |
|---|---|
| `run-id` | Run id must be YYYY-MM-DD or YYYY-MM-DD-N with N of 2 or more. |
| `run-id-mismatch` | Run id must equal the folder name in every file. |
| `run-id-future` | Run date is more than 1 day ahead of today. |
| `run-changed` | App only: a run with this id was already imported with a different manifest. |
| `mode-mismatch` | Mode differs between manifest.json and snapshot.json. |
| `manifest-missing` | manifest.json is missing. |
| `manifest-rows` | CSV files need `rows`. snapshot.json has none. |
| `path-not-allowed` | Path is not allowed. |
| `symlink` | Symbolic links are not allowed. |
| `file-missing` | A required or listed file is missing. |
| `file-unlisted` | A file is not listed in manifest.json. |
| `file-too-large` | File is larger than 20 MB. |
| `size-mismatch`, `hash-mismatch`, `rows-mismatch` | File size, sha256 or CSV row count differs from manifest.json. |
| `json` | Not valid JSON, or the top level is not an object. |
| `encoding` | Not UTF-8, or has a byte order mark. |
| `line-endings` | Not LF only, or no newline at the end. |
| `version-format`, `version-major`, `version-minor` | schema_version is malformed, has an unknown major version, or is newer than the app knows. |
| `schema` | A field is missing, unknown, or has the wrong type or value. |
| `key-format`, `money-format`, `quantity-format`, `rate-format`, `date-format`, `month-format`, `timestamp-format`, `last4-format`, `text-format`, `hash-format`, `cusip-format`, `symbol-format`, `confidence-format`, `bool-format` | A value breaks its format (section 4). |
| `date-order` | Start date after end date. From 1.4 also a flag update's `on` before the flag's `created_on` or after `meta.updated`. |
| `reconcile-note` | `reconciled: false` without a note. |
| `nw-group` | An account in net worth has no nw_group. |
| `tracked-net-worth` | An account tracked by payments is included in net worth. |
| `money-sign` | An amount that must be zero or more is negative. |
| `section-version` | A section, field or value needs a newer `schema_version` than the run declares. The location is the field, never the value. |
| `recurring-interval` | `interval_n` is missing for `every_n_weeks`, or set for another cadence. |
| `active-months` | `active_months` is not ascending month numbers with no repeats. |
| `plan-dates` | A `promo_0pct` plan has no `promo_expiry`, a `deferred_interest` plan has no `pay_before`, or (1.4) `paid_off_on` is on an `active` plan or before `created_on`. |
| `plan-payments` | `payments_left` is more than `payments_total`. |
| `plan-components` | The component amounts do not add up to `original`. |
| `flag-resolved` | A resolved flag has no `resolved_on`. |
| `flag-related` | Only one of `related_kind` and `related_key` is set. |
| `utility-date` | A utility bill date is before 2000-01-01. |
| `utility-period` | A utility bill's `period_start` is not before its `period_end`. |
| `utility-unit` | A utility line's `unit` does not fit its `service`. |
| `utility-fixed` | A utility line's `fixed_charges` is more than its `charges`. |
| `utility-credit-sign` | A utility credit amount is zero or more. |
| `utility-total` | A utility bill's line charges plus credits differ from `total` by more than 2 cents. |
| `utility-total-rounding` | Warning: they differ by 1 or 2 cents. The run is imported. |
| `utility-days` | Warning: `days` does not equal `period_end` minus `period_start`. |
| `utility-overlap` | Warning: a bill's period overlaps the bill before it for the same utility. |
| `reward-as-of` | A program's `balance`, `as_of` and `source` are not all set or all left out, or `reported_spent` and `as_of` are not both set or both left out. |
| `reward-value-range` | `cpp_low` and `cpp_high` are not both set, low is more than high, or they are on a `cash_back` program. |
| `reward-benefit-fields` | A benefit has a field of another kind, or lacks one its kind requires. |
| `reward-program-unknown` | A benefit's or redemption's `program_key` is not in `rewards.programs`. |
| `reward-allocation` | `allocations` on a program that is not a timeshare, or `used` plus `pulled_forward` more than `allocated`. |
| `trip-dates` | A trip has not exactly one of `start_date` with `end_date`, or `month`, or a date before 2000-01-01. |
| `trip-estimate` | `estimate_low` and `estimate_high` are not both set, or low is more than high. |
| `trip-item-amount` | A trip item's `amount` and estimate fields do not fit its `status`. |
| `trip-tender` | Tender `points` without `points` and `program_key`, or `points_credit` without `amount`. |
| `trip-iou-settled` | A settled IOU without `settled_on`, or another IOU with it. |
| `trip-item-unknown` | An IOU's `item_key` is not an item in the same trip. |
| `recurring-keep` | `keep` is on an item that is not `active` or `paused`. |
| `recurring-tag-private` | The personal subscription (named `Personal subscription`, in any case) carries a `tag`. |
| `reward-done-on` | `done_on` is on a redemption whose status is not `done`. |
| `debt-terms` | An account's `original` is zero, or is on an account that is not a `loan` or `mortgage`. Or its `rate_changes` has items and the same account item has no `apr` or a `rate_type` other than `variable`. |
| `trip-due` | A trip item's `due_on` is on an item that is not `booked`. |
| `trip-maybe` | A trip item's `maybe` is on an item that is not `estimate` or `tbd`. |
| `goal-fields` | A `college` goal has no child as `person` (from this run's `people`), no `start_year`, a `target_date`, or an account that is not a `529` or `custodial` account in this run's `accounts`. Or a `save_up` goal has `start_year`. |
| `reward-stale` | Warning: a balance or `reported_spent` is dated more than 90 days before the run. The run is imported. |
| `trip-unknown` | Warning: a `trip_key` names a trip no run so far defines. The run is imported. |
| `recurring-no-date` | Warning: an `active` recurring item has no `next_date`, `start_date` or `renewal_date`, so it has no due date. The run is imported. |
| `statement-unreconciled`, `plan-groups-sum`, `statement-drift` | App only. Warnings: a statement marked not reconciled (section 7), plan groups that do not add up to `planned_spending` (section 5.5), or a statement sent again with different content by a run that does not win (section 11). The run is imported. |
| `section-missing`, `section-not-allowed` | A required section is missing, or state is in a backfill or correction run. |
| `key-duplicate`, `sort-order` | A list has two items with one identity, or is not sorted. |
| `paycheck-seq` | Paycheck seq is not 1, 2, ... by gross. |
| `csv-header`, `csv-format`, `csv-sort`, `csv-empty` | CSV header wrong, wrong number of fields, rows not sorted, or no rows. |
| `csv-orphan`, `csv-missing` | A CSV with no statement, or a statement with rows and no CSV. |
| `account-unknown`, `new-account-details`, `person-unknown`, `category-unknown` | A key is not known. |
| `txn-count`, `txn-sum` | Statement counts or sums differ from the CSV. |
| `statement-opening`, `statement-unbalanced`, `statement-credits`, `statement-debits` | Statement does not reconcile (section 7). |
| `balance-statement`, `balance-closing` | A statement balance has no statement, or differs from it. |
| `statement-balance-missing` | A statement has no balance row with source `statement` in the run. |
| `holdings-missing` | A statement has `has_holdings: true` and the run has no `holdings` section. |
| `tracked-balance` | An account tracked by payments has a balance from a statement. |
| `tracked-payment` | A row to a card tracked by payments with `is_transfer` not true is not a `payment` with a spending category. |
| `holding-statement` | A holding has no statement with has_holdings. |
| `date-window` | A row date is outside the window. |
| `refund-income` | A refund uses an income category. |
| `digits-run`, `digits-split`, `luhn`, `phone`, `split-account`, `masked-number`, `email`, `street-address`, `stock-plan`, `private-subscription`, `private-employer`, `booking-code` | Redaction (section 9). |

## 14. Versions

`schema_version` is `MAJOR.MINOR`. This document is 1.4. The app accepts 1.0, 1.1, 1.2, 1.3 and 1.4. Each minor version only adds sections, optional fields or values. The app that knows a version is deployed before the chat writes it. A newer minor version than the app knows is rejected with a clear message, and so is an unknown major version.

| Version | Adds |
|---|---|
| 1.0 | Everything except sections 5.6 to 5.10 and the parts marked (1.4). |
| 1.1 | `recurring`, `installment_plans`, `flags` and `pending`, required in monthly runs (section 5.6). The amount type and its rule `money-sign`. |
| 1.2 | `utility_bills`, an optional history section in every mode (section 5.7), and its rules. |
| 1.3 | `rewards`, a state section required in monthly runs (section 5.8), and `trips`, an optional history section in every mode (section 5.9), with their rules, the two warnings and `booking-code`. |
| 1.4 | Optional fields on flags, recurring, installment plans, cash flow events, accounts, trip items and redemptions, new flag kinds, and the optional `goals` state section (section 5.10), with their rules. |

Write 1.4 only after the app that knows it is deployed, and only when `data/_schema/README.md` says 1.4. A run below 1.4 that carries a 1.4 field or value is rejected (`section-version`). Write 1.3 only when it says 1.3, and 1.2 only when it says 1.2. A monthly run with no `rewards` may still say 1.2, and a run that carries no `utility_bills` may still say 1.1. Backfill and correction runs may say 1.0, 1.1, 1.2, 1.3 or 1.4. `manifest.json` and `snapshot.json` carry the same version. A 1.0 to 1.2 run that carries `rewards` or `trips` is rejected, and so is a run below 1.4 that carries `goals` (`section-version`).

`import-v1.schema.json` is generated from `backend/app/importer/models.py` by `scripts/gen_schema.py`. A test fails if it drifts.
