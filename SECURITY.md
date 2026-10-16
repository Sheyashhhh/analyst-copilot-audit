# Security policy

## Reporting a vulnerability

Please do not publish credentials, API keys, or sensitive data in a public issue. For security-sensitive reports, use GitHub's private vulnerability reporting feature if it is enabled for this repository, or contact the repository owner privately.

## API keys and data

Keep provider keys in a local `.env` file. Do not commit `.env`; only `.env.example` belongs in version control. Revoke and rotate any key that is accidentally exposed. The project executes generated SQL against a local DuckDB database, but you should still review queries and use data appropriate for your environment before running untrusted prompts or models.
