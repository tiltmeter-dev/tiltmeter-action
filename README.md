# Tiltmeter GitHub Action

Run [Tiltmeter](https://github.com/tiltmeter-dev/tiltmeter) on a schedule and fail the job
when a third-party API your code or AI agents depend on changes shape: a renamed field, a
number that became a string, a value that started arriving as `null`.

The action installs Tiltmeter, keeps the baseline in the Actions cache between runs, writes
a table of changes to the job summary and fails the step according to `fail-on`.

## Usage

Put a [`tiltmeter.yaml`](https://github.com/tiltmeter-dev/tiltmeter/blob/main/docs/configuration.md)
in your repository and add `.github/workflows/api-drift.yml`:

```yaml
name: API drift

on:
  schedule:
    - cron: "17 * * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  tiltmeter:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: tiltmeter-dev/tiltmeter-action@v1
        env:
          # Every ${VAR} in tiltmeter.yaml needs a matching variable here.
          STRIPE_API_KEY: ${{ secrets.STRIPE_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }} # optional, for explanations
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }} # optional, for alerts
```

The first run records a baseline. Later runs report each change once, then fold it into
the baseline.

## Provider notices

When the APIs you call publish [change notices](https://github.com/tiltmeter-dev/tiltmeter/blob/main/docs/notices.md),
`mode: notices` applies them: for each new notice that affects the repository, an agent
adapts the code and the action opens a pull request. Notices that do not affect it are
recorded without running an agent, and nothing is handled twice. List the providers under
`subscriptions` in `tiltmeter.yaml`, then add `.github/workflows/api-notices.yml`:

```yaml
name: API change notices

on:
  schedule:
    - cron: "23 */6 * * *"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

concurrency: tiltmeter-notices

jobs:
  notices:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: tiltmeter-dev/tiltmeter-action@v1
        with:
          mode: notices
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

- Turn on "Allow GitHub Actions to create and approve pull requests" under Settings >
  Actions > General.
- Pull requests opened with the default `GITHUB_TOKEN` do not start other workflows. Pass
  a GitHub App or fine-grained token as `token`, and to `actions/checkout`, to have your
  CI run on them.
- Keep the default `actions/checkout` credentials: the action pushes the fix branches.
- `mode: notices` needs a Tiltmeter release that has `tiltmeter notices`; set `version`
  to one until it is the default, or `source` to run it from a git branch.
- Subscriptions can also follow a provider's OpenAPI spec (`openapi:`): its last version
  is kept in the same cache, and changes to its responses are handled like notices.
- `agent: claude-code` needs the CLI installed in an earlier step
  (`npm install -g @anthropic-ai/claude-code`) and `CLAUDE_CODE_OAUTH_TOKEN` (from
  `claude setup-token`) or `ANTHROPIC_API_KEY` in the step's `env`.
- With `fix.test_command` in the config, the agent's edits are tested before the pull
  request opens, and a failure goes back to the agent. Install your dependencies in an
  earlier step: the tests run without network, and are skipped where the runner cannot
  cut it (Ubuntu 24.04 restricts the network namespaces this needs), unless
  `fix.test_network: true`.

## For API providers

Two modes let an API provider publish its own notices from CI, without writing or
signing JSON by hand:

```yaml
name: API change notices

on:
  pull_request:
    paths: [openapi.yaml]
  push:
    branches: [main]
    paths: [notices/**, site/**]

jobs:
  draft:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v7
      - uses: tiltmeter-dev/tiltmeter-action@v1
        with:
          mode: notice-draft
          spec: openapi.yaml

  publish:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
    steps:
      - uses: actions/checkout@v7
      - uses: tiltmeter-dev/tiltmeter-action@v1
        with:
          mode: notice-publish
          site-dir: site
        env:
          TILTMETER_SIGNING_KEY: ${{ secrets.TILTMETER_SIGNING_KEY }}
      - uses: actions/upload-pages-artifact@v5
        with:
          path: site
          include-hidden-files: true  # .well-known is a hidden folder
      - uses: actions/deploy-pages@v5
```

- **`notice-draft`** compares the spec with the pull request's base branch and keeps one
  comment on the pull request with the changes and a draft notice. Save the draft in
  `notices/`, review its summary and notes, and merge. With `require-notice: true`, the
  step fails while the pull request changes responses without adding a notice. Pull
  requests from forks get a read-only token, so they get no comment.
- **`notice-publish`** signs every notice in `notices/` with the `TILTMETER_SIGNING_KEY`
  secret (the PEM itself) into `site/.well-known/api-changes.json`, checking each signature
  against the `api-change-key` you commit next to it. Create both with
  `tiltmeter notice keygen notice-key.pem --public site/.well-known/api-change-key`, then
  `gh secret set TILTMETER_SIGNING_KEY < notice-key.pem`.

## Inputs

| Input | Default | Description |
|---|---|---|
| `mode` | `check` | `check` checks the endpoints for drift. `notices` applies the change notices and specs the config subscribes to. For providers, `notice-draft` comments a draft notice on pull requests that change the spec, and `notice-publish` signs `notices-dir` into the feed in `site-dir`. |
| `config` | `tiltmeter.yaml` | Path to the config file, relative to the workspace. |
| `endpoints` | all | Endpoint ids to check, separated by spaces or new lines. |
| `version` | `0.5.0` | Tiltmeter version from PyPI. |
| `source` | none | Run Tiltmeter from here instead of PyPI, such as `git+https://github.com/tiltmeter-dev/tiltmeter@main`. Overrides `version`. |
| `fail-on` | `warning` | `warning` fails on warning or breaking drift and on checks that could not run. `breaking` fails only on breaking drift. `never` only reports. A broken config always fails. |
| `explain` | `true` | Ask Claude to explain drift. Needs `ANTHROPIC_API_KEY`; without it the check runs without explanations. |
| `notify` | `true` | Send the Slack and webhook notifications declared in the config. |
| `cache` | `true` | Keep the baseline in the Actions cache. Without it nothing is ever reported. |
| `cache-key` | `tiltmeter` | Cache key prefix. Change it when one job checks several independent configs. |
| `summary` | `true` | Write the results to the job summary. |
| `agent` | config's `fix.agent` | With `mode: notices`: `deepagents`, `claude-code` or `codex`. The default agent needs `ANTHROPIC_API_KEY`; the other two need their CLI on the runner. |
| `token` | `github.token` | The token `gh` uses: to open pull requests with `mode: notices`, and to comment with `mode: notice-draft`. |
| `spec` | none | With `mode: notice-draft`: the path of your OpenAPI spec. |
| `base-url` | the spec's server | With `mode: notice-draft`: the URL paths start from, when the spec's first server is relative or templated. |
| `notices-dir` | `notices` | Where the provider keeps its notices, one JSON file each. |
| `site-dir` | `site` | With `mode: notice-publish`: the folder your site is served from. |
| `require-notice` | `false` | With `mode: notice-draft`: fail while the pull request changes responses without a notice. |

## Outputs

| Output | Description |
|---|---|
| `exit-code` | `0` clean, `1` warning, `2` breaking, `3` a check or a fix could not run, `4` configuration error. With `mode: notices`, `1` means an agent left questions for a human. |
| `result` | `clean`, `warning`, `breaking`, `failed` or `error`. |
| `json` | Path to the JSON results: one check result per endpoint; the feeds and what was done about each new notice; the draft notice (`notice-draft`, empty when nothing changed); or the feed (`notice-publish`). |

Use them to react in later steps, for example to open an issue on breaking drift:

```yaml
      - uses: tiltmeter-dev/tiltmeter-action@v1
        id: drift
        with:
          fail-on: never
      - if: steps.drift.outputs.result == 'breaking'
        run: gh issue create --title "Breaking API drift" --body "Details: $RUN_URL"
        env:
          GH_TOKEN: ${{ github.token }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

## Notes

- **Pin it.** `@v1` follows compatible releases. For a fixed version, use a full commit
  SHA, and set `version` to the Tiltmeter release you reviewed.
- **Runners.** Works on Linux, macOS and Windows runners. It uses
  [`astral-sh/setup-uv`](https://github.com/astral-sh/setup-uv) and
  [`actions/cache`](https://github.com/actions/cache), both pinned by commit SHA.
- **Secrets.** Credentials are passed through `env` and expanded by Tiltmeter at request
  time. They are never written to the cache, the summary or the logs. The cache holds only
  response shapes.
- **Schedules.** GitHub runs `cron` in UTC and can delay busy hours; avoid minute `0`.

## License

[MIT](LICENSE)
