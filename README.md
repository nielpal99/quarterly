# Quarterly

A local financial-document research workbench built with LlamaParse: discover company reports, prepare evidence, ask cited questions, save earnings briefs, and review metric changes.

## Run locally

Requires Python 3.12+ and uv.

```sh
uv sync
# On a fresh checkout only: copy .env.example to .env and configure keys.
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Keep this single-user prototype bound to localhost. It has no authentication or tenant isolation and is not ready for unrestricted public hosting.

## Configuration

- `LLAMA_CLOUD_API_KEY`: PDF parsing and native cited-answer generation through Extract. The Research UI selects this provider when configured. Generation consumes account credits.
- `EXA_API_KEY`: investor-relations discovery and a verified company-identifier fallback when the SEC ticker directory is unavailable. Candidate identifiers are checked against SEC company records.
- `SEC_USER_AGENT`: application name and real contact email for SEC requests. SEC hosts can still reject requests.
- `OPENAI_API_KEY` and `QUARTERLY_EXTRACTION_MODEL`: optional legacy answer provider and structured metric-extraction engine. An OpenAI key is not required for LlamaParse-native answers.
- `QUARTERLY_DATA_DIR`: SQLite database, source documents and artifacts; defaults to local `data`.

Never commit `.env`. App keys are separate from any connected Codex plugin account.

## Portable demo

In Research, click **Try a fictional example · no keys needed**. The bundled fixture is explicitly synthetic and creates a new saved research packet. Search its evidence and build an excerpt brief with AI generation off. This works from an empty database without downloads or provider calls. It demonstrates the workflow, not LlamaParse accuracy. Existing real-document guided scenarios remain separate and need their saved source artifacts.

To try a pinned real PDF, run `uv run python scripts/download_public_sample.py --download`, then upload the downloaded file through the app. The downloader verifies the source hash and never parses or changes the database. Select page 25 for the Snowflake table. Parsing is a separate explicit credit-consuming action. This sample is historical, not the latest earnings report.

## Research workflow

1. Enter a U.S. SEC ticker and choose Latest earnings, recent comparison, or historical cutoff.
2. Inspect discovered documents and their covered periods. Search candidates require publisher/company/period confirmation. Discovery alone does not make documents searchable.
3. Explicitly ingest the desired sources. PDF downloads require selecting up to five pages for paid parsing.
4. Search excerpts or enable Write cited answer. Native answers use LlamaParse Extract over bounded retrieved excerpts, not the entire discovered corpus.
5. Select searchable sources to build a saved earnings brief. Reopen snapshots or export Markdown. Written sections use separate provider requests when enabled.

The source library supports tags, notes, archive and restore. Archive changes library visibility; it does not remove sources from existing research sessions.

Metric updates compare two periods within one parsed run. Review units, definitions, fiscal periods and actual/guidance status before accepting a change. Separate-document reconciliation is not implemented.

## Tests and evaluations

```sh
uv run pytest -q
uv run python scripts/community_smoke.py
uv run python scripts/evaluate_suite.py --portable
# Full development corpus checks (requires local real-source snapshots):
uv run python scripts/evaluate_suite.py
# Optional: two paid LlamaParse answer calls, scored by local code:
uv run python scripts/evaluate_native_smoke.py --live
```

The offline suite combines real-release development examples, synthetic financial challenges and isolated API checks. Missing or blocked corpora produce a nonzero exit status. Reports are written to `outputs`. Real-source snapshots under `data/evaluation` are local and ignored; they are not supplied by a clean checkout. Portable challenge fixtures are in `evals/challenges.json`.

The live smoke command calls LlamaParse Extract and scores its answers locally. It does not invoke a vendor evaluation endpoint. That new endpoint has not yet been identified in the authenticated public API schema. Development checks are not independent held-out financial accuracy or parser-quality measurements.

## Evidence limits

Evidence IDs and supporting quotes are checked for native answers. Extract's provider citations refer to the uploaded excerpt bundle; original-document links are retained by this app. These checks do not establish semantic claim correctness or correct table-column interpretation. Keyword retrieval can miss evidence. No source-grounded answer is guaranteed for arbitrary questions or companies.

## License

Source code is distributed under the MIT License; see LICENSE. Third-party financial documents retain their original provenance and are downloaded separately, not included in the source bundle.

## Sharing status

Suitable for a supervised local demo. Before public hosting: authentication and isolated storage, durable jobs, usage limits, reliable source-download fallbacks, and failure reporting are required. The MIT-licensed source includes a portable demo, offline eval mode, a pinned public-sample downloader and a clean-install verifier. Documents, database, secrets and raw parse artifacts remain excluded from version control.
