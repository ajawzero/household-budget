# Household Budget

A self-hosted finance web app for one household. It imports structured monthly exports, checks them, stores them in SQLite and shows a dashboard, accounts, transactions, cash flow, budgets, recurring bills, investments, goals, flags and to-dos, rewards, travel, statements and history. Claude-powered Ask, monthly review, category suggestions and a trip planner are optional.

It never connects to your bank. You make the monthly exports yourself, and your data stays on your machine.

## Try it

You need git, [uv](https://docs.astral.sh/uv/) and Node 24. Mac or Linux; on Windows, use WSL2.

```
git clone <this repo's URL> household-budget
cd household-budget/frontend && npm ci && cd ..
scripts/dev.sh
```

In a second terminal, make a user:

```
cd backend
UV_FROZEN=1 uv run python -m app.cli create-user <name> --display-name <Name> --admin
```

Open `http://localhost:5173` and sign in. The app imports a fake household from `samples/` within a minute.

## Use it for real

- [docs/SETUP.md](docs/SETUP.md): run it with Docker on one machine or a home server, plus hardware and what to change for your household.
- [docs/IMPORT_GUIDE.md](docs/IMPORT_GUIDE.md): turn your own statements into monthly exports.
- [schema/README.md](schema/README.md): the full import format.
- [docs/budget.env.example](docs/budget.env.example): every setting.

## Before you rely on it

- It is built for the home network. Don't put it on the open internet. It has not had an outside security audit.
- It is personal software, shared as is. Back up your data and check the numbers yourself. It is not financial advice.
- With an Anthropic key set, the AI features send scrubbed financial facts to Anthropic, and you pay for what you use. With no key they stay off.
- Other outbound calls: market prices (Tiingo, Yahoo) and the FHFA house price index file.

## About this repo

This repo is a one-way export, updated now and then. So:

- Pull requests can't be merged back. Fork it and make it yours. Issues are welcome, with no promise of a reply.
- Code comments mention planning docs that aren't published (`PLAN`, `docs/plans/`). "The chat" means whatever makes your monthly exports.
- The sample data and its names are made up.

## Develop

`scripts/check.sh` runs every test and check. See [AGENTS.md](AGENTS.md) for the commands and layout. To scan each commit for account numbers and secrets, run `git config core.hooksPath scripts/hooks`.

## License

[MIT](LICENSE)
