# Dental API CORS policy

The API uses an explicit, exact-origin browser allow-list. Set
`DENTAL_CORS_ORIGINS` to a comma-separated list of canonical origins, for
example `https://portal-staging.example.com`. Do not include a path, trailing
slash, query, fragment, credentials or wildcard.

When the variable is unset or empty, the API denies cross-origin browser
access. Local development origins belong only in a local environment file;
they are not runtime defaults and must never be copied into a production
environment.

A staging rollout must verify the actual portal origin, preflight method and
headers, credentialed login, authenticated API calls, and an unlisted-origin
denial before the origin is added to the staging service configuration. This
code change does not modify any Render or production environment variable.
