# Schwartz MCP

A local MCP server for source-grounded campaign research, structured creative review, bounded debate, and tournament ranking.

An agent can turn a brief into versioned hypotheses, draft candidates, reviews, an experiment plan, and a local review package. Runs persist in SQLite and resume after reconnecting. Source gaps travel with the artifacts.

**This public release contains the engine and an original demo dataset. It does not contain Eugene Schwartz's book, extracted passages, the private semantic review, or personal campaign data. The demo does not reproduce the book's teachings.** The project is inspired by studying *Breakthrough Advertising* and is not affiliated with or endorsed by its author or rights holders.

## Quick start

Requires Python 3.11+ and `uv`.

```sh
git clone https://github.com/jaydenjk0329/schwartz-mcp.git
cd schwartz-mcp
uv sync --locked
uv run python scripts/init_demo.py
uv run schwartz doctor
```

Copy `examples/mcp-config.json` into your MCP client's configuration and replace both `/absolute/path/to/schwartz-mcp` values with your checkout path. The server uses stdio; it has no web dashboard.

```json
{
  "mcpServers": {
    "schwartz": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/schwartz-mcp", "run", "schwartz-mcp"],
      "env": {"SCHWARTZ_ROOT": "/absolute/path/to/schwartz-mcp"}
    }
  }
}
```

## What it does

- 34 workflow graphs, including campaign sequencing, headline development, copy audit, proof placement, channel adaptation, and experiment learning.
- 27 MCP tools for project/evidence management, retrieval, execution, inspection, and local exports.
- Host-driven execution using your connected agent, with no separate provider key required.
- An optional provider worker with explicit per-run and durable batch budgets.
- Separate market, source, creative, evidence, and experiment review roles; bounded revision rounds and blinded pairwise comparisons.
- Three registered checks: knowledge search, offer arithmetic, and asset measurement.
- Hash-linked artifacts, source provenance, leases, idempotency, recovery, and backup/restore.

## Agent workflow

1. Inspect `audit_coverage`, `list_workflows`, and `get_workflow`.
2. `create_project` with a factual brief; add evidence with its actual origin and scope.
3. `plan_run`, then `start_run` in `host_driven` mode with the returned plan hash.
4. `claim_work`, read the frozen context and output schema, perform the actual work, and `submit_work`. Use `fail_work` if required facts are missing.
5. Repeat until the run is complete or explicitly waiting for real observations.
6. `inspect_run` and `export_campaign` to review the local JSON/HTML package.

For a demo, ask your agent: "Use Schwartz to plan a campaign for an imaginary reusable notebook. Label all product claims as hypothetical, use the demo knowledge only, and do not publish anything."

## Knowledge and privacy

The included corpus is deliberately tiny, original, and marked `demo_only`. The workflow names and routing tags are not a substitute for book-grounded knowledge. For substantive source-grounded use, supply your own lawfully usable reviewed corpus, maintaining provenance and explicit gaps. [The corpus contract](docs/CORPUS.md) lists the required files. A PDF alone is not a ready runtime corpus, and an automatic book-ingestion UI is not included.

Projects and exports are stored under gitignored `private/` by default. Gitignore is not encryption. Host-driven execution gives task content to your connected agent. A configured provider worker sends task context to that provider. Choose sources and provider settings accordingly.

## Provider execution

`examples/provider.example.toml` is disabled until you supply a model, current pricing, and an API-key environment variable. Keep credentials outside the repository. The adapter expects a Responses-compatible endpoint. Live-provider qualification is still pending; a protocol-compatible endpoint is not proof that every provider/model works.

```sh
uv run schwartz worker --config private/provider.toml
```

The default run dollar cap is zero. Host calls are performed outside the server and are not independently metered. Worker caps are conservative application estimates, not billing guarantees.

## Verification and limitations

```sh
uv run pytest
```

The public tests use clearly labeled synthetic fixtures to test orchestration, isolation, recovery, and MCP protocol behavior. They do not establish persuasion quality, source fidelity to the book, or marketing outcomes. Judge rankings are model preferences, not conversion probabilities. One agent serving multiple review roles can share context and bias.

This release does not publish posts, buy media, browse the web, or execute arbitrary shell commands. External research must be collected by the host and imported as evidence. No claim of complete book coverage or production qualification for every provider is made.

## License

No open-source license has been selected yet. Public availability alone does not grant a general reuse license. Third-party dependencies retain their own licenses.
