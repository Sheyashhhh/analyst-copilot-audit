# Contributing

Thanks for considering a contribution. This project is most useful when its evaluation cases are auditable and its claims remain reproducible.

## Local setup

1. Follow the [README quick start](README.md#quick-start).
2. Run `python -m copilot_audit doctor`.
3. Run `python -m pytest -q`.
4. For changes to traps or guardrails, also run `python -m copilot_audit traps` and `python -m copilot_audit holdout`.

## Good contributions

- Add questions that represent a concrete business request and have a defensible gold query.
- Add at least one test for each new comparison or lint rule.
- Include correct alternative SQL when multiple formulations should be accepted.
- Keep model-generated results separate from scripted self-test artifacts.
- Describe limitations and any change to benchmark interpretation in the pull request.

## Pull request checklist

- [ ] Tests pass locally.
- [ ] New SQL runs against the built DuckDB warehouse.
- [ ] Gold and alternate queries are checked for result-set equivalence.
- [ ] No API keys, local `.env`, downloaded data, or local database files are included.
- [ ] Documentation and methodology are updated where needed.

Please do not submit credentials, private datasets, or unredacted API responses containing sensitive information.
