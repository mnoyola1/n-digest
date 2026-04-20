# n-digest

Daily curated AI industry digest, emailed weekdays at 6:30 AM ET.

Built for one reader (mnoyola1@gmail.com). Runs on GitHub Actions cron. Two-stage Claude curation: Haiku 4.5 filters and scores the daily pool, Opus 4.7 writes the 3 "What Matters Today" blurbs and the weekend Deeper Look.

## How it works

```
cron -> fetch (~20 RSS + HN + GitHub) -> dedup (14d)
     -> Haiku 4.5 score/cluster -> Opus 4.7 compose
     -> Jinja2 HTML -> Resend email
     -> commit state.json + docs/archive/YYYY-MM-DD.html
```

## First-time setup (one-time, ~10 minutes)

### 1. Push to GitHub

```powershell
cd c:\Dev\n-solutions\n-digest
git add .
git commit -m "initial commit"
# Create the remote repo at https://github.com/new (name: n-digest, private, no README)
git remote add origin https://github.com/mnoyola1/n-digest.git
git branch -M main
git push -u origin main
```

### 2. Add repo secrets

Go to `https://github.com/mnoyola1/n-digest/settings/secrets/actions` and add:

- `ANTHROPIC_API_KEY` (copy from `c:\Dev\n-solutions\n-learn\.env`)
- `RESEND_API_KEY` (copy from `c:\Dev\n-solutions\n-learn\.env`)

`GITHUB_TOKEN` is provided automatically by the workflow runner.

### 3. Enable workflow write permissions

`Settings -> Actions -> General -> Workflow permissions ->` select **Read and write permissions** and **Allow GitHub Actions to create and approve pull requests**. This lets the workflow commit `state/state.json` and `docs/archive/` back to main.

### 4. Enable GitHub Pages

`Settings -> Pages -> Build and deployment -> Source: Deploy from a branch -> Branch: main / /docs`. Archive will be live at `https://mnoyola1.github.io/n-digest/`.

### 5. First send (manual trigger)

`Actions -> Daily AI Digest -> Run workflow`. Leave both inputs at their defaults (`dry_run: false`, `ignore_schedule: true`). After ~45 seconds the email will hit `mnoyola1@gmail.com`.

From then on, the dual-cron schedule (`30 10 * * 1-5` and `30 11 * * 1-5` UTC) fires every weekday; only the entry that lands on 6:30 AM ET proceeds past the DST guard.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# copy keys into .env (already gitignored)
copy .env.example .env
python -m src.main --dry-run       # writes out/preview.html, no send
python -m src.main --send-to-self  # real email
```

`--dry-run` skips the Resend send and does not write to `state/` or `docs/archive/`. Use freely while tuning.

## Tuning

- **Source list**: edit [src/sources.yml](src/sources.yml). Add, remove, or tag feeds; no code change needed. Supported types: `rss`, `atom`, `hn_search`, `github_search`, `hf_papers`.
- **Filter behavior** (what gets scored high / low): edit [prompts/filter_system.md](prompts/filter_system.md). Content priorities live here.
- **Writing voice and framing**: edit [prompts/compose_system.md](prompts/compose_system.md). The "about me" block drives how each blurb gets framed.
- **Email look**: edit [templates/digest.html](templates/digest.html). Mobile-first, dark-mode ready, inline CSS only.
- **Models**: override via repo Variables `FILTER_MODEL` and `COMPOSE_MODEL`. Defaults are `claude-haiku-4-5` and `claude-opus-4-7`.

## Archive

Past digests live at `https://mnoyola1.github.io/n-digest/archive/YYYY-MM-DD.html` after Pages is enabled. The archive index `https://mnoyola1.github.io/n-digest/` lists the most recent 30 days. Each email footer links to the previous day's digest.

## Observability

Every run logs to the Actions job log:

- Per-source fetch counts and failures
- Haiku filter token usage and how many items cleared the score threshold
- Opus compose token usage
- Dollar-cost estimate per run
- Resend message id on success

Each scheduled run also uploads `out/preview.html` as an artifact named `digest-preview` (retained 14 days), so you can re-read any past digest as it was rendered without hunting for the email.

## Costs

Typical daily run: ~$0.30 (Haiku: ~$0.03, Opus: ~$0.28). ~21 weekdays/month = **~$6-7/month**.

