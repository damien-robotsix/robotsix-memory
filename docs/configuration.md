# Configuration reference

robotsix-memory reads its runtime settings from a single JSON config file
(path in `ROBOTSIX_CONFIG_FILE`, default `config/config.json`) via
`robotsix_config.load_config`. The settings model is
`robotsix_memory.config.Settings`; its JSON Schema lives in
`config/config.schema.json` and is regenerated with
`uv run python -m robotsix_memory.gen_schema` whenever a field changes.

Any field omitted from the config file falls back to the default below.
The only setting that an environment variable can override is
`hindsight_url` (via `MEMORY_HINDSIGHT_URL`); every other value comes from
the config file alone. Deploy-time environment variables that do not feed
into `Settings` (`ENVIRONMENT`, `ROBOTSIX_CONFIG_FILE`, `ENABLE_METRICS`)
are listed in the README's Configuration section.

## Settings fields

<!-- Kept in sync with Settings.model_fields by tests/test_config_doc.py. -->

| Field | Type | Default | Meaning | Env override |
| --- | --- | --- | --- | --- |
| `hindsight_url` | `str` | `"http://memory-hindsight:8888"` | Base URL of the sibling Hindsight engine the wrapper forwards to. | `MEMORY_HINDSIGHT_URL` |
| `request_timeout` | `float` | `60.0` | Timeout in seconds for ordinary Hindsight HTTP requests (recall, reflect, bank management). | none |
| `retain_timeout` | `float` | `120.0` | Timeout in seconds for retain requests and retain retries, which run longer than other calls. | none |
| `recall_limit` | `int` | `10` | Number of results returned by `/recall` when the caller does not pass `limit`. | none |
| `bank_prefix` | `str` | `"fleet"` | Prefix used to build Hindsight memory bank ids from an owner id (`bank_id(bank_prefix, owner_id)`). | none |
| `log_level` | `str` | `"INFO"` | Log level name passed to the structlog/logging configuration at startup. | none |
| `metrics_port` | `int` | `8001` | Port of the Prometheus exposition server; only used when `ENABLE_METRICS` is truthy. | none |
| `retain_replay_interval` | `float` | `900.0` | Seconds between ticks of the failed-retain replay loop; `<= 0` disables the loop. | none |
| `retain_replay_per_minute` | `int` | `30` | Maximum number of retain retry POSTs the replay loop sends per minute. | none |
