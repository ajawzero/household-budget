# Make your own monthly exports

The app does not read bank sites or PDFs. It imports run folders: one `snapshot.json`, one CSV per statement and a `manifest.json`. The format is in [schema/README.md](../schema/README.md). The `samples/data/` folders are complete examples.

An AI assistant that can read files and run Python, like Claude, can make these from your statements. You can also write your own script. Any method works if `check_run.py` passes.

Your statements hold sensitive data, and an assistant's provider will see what you give it. Check that you are comfortable with its privacy terms first.

## Each month

1. Download your statements as PDF or CSV into a folder. Copy `schema/` beside it as `_schema/`.
2. Give your assistant the statements and the prompt below. Start with one account and one month.
3. Check the numbers against the statements yourself. The checker tests the format, not whether the amounts are right.
4. Copy the finished run folder into the app's import folder. It imports within a minute. See the Imports page for the result.

An import rejects the whole run on any error, and a run folder never changes once its manifest exists. To fix a mistake, write a new run.

## Starter prompt

```text
Turn my statements into one import run for my budget app.

Read _schema/README.md in full first and follow it exactly. Use only the category keys
in _schema/categories.json. Look at the sample runs if you need an example.

Rules:
- Use only numbers printed on my statements. Never guess or fill in a number. If something
  is missing or unclear, leave it out if the format allows that, and tell me.
- Never write a full account or card number, a street address, or a routing number.
  Last four digits at most. Follow the redaction rules in section 9.
- Write snapshot.json and the CSV files first, then run:
    python _schema/check_run.py <run-folder> --pre
- Then write manifest.json last, and run:
    python _schema/check_run.py <run-folder>
- Fix every error and rerun until it passes. Never edit the checker or the schema.

When done, give me the run folder, the checker's result, how the totals for each account
compare to its statement, and anything you left out or were unsure about.
```

Save the prompt and any notes about your accounts (their keys, how you categorize things) somewhere to reuse. Keep account keys the same each month, so the history lines up.

## Keeping private words out

`samples/private_rules.example.json` shows the private rules file: your employer's name and any subscriptions you'd rather not name. Copy it to your `config` folder, edit it, and point `BUDGET_PRIVATE_RULES` at it. Pass the same file to the checker with `--private-rules`.
