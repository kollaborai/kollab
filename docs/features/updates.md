# Update checks

The Web UI reports update checks as available, current, disabled, or failed. A failed or unknown release lookup never appears as “up to date.” When a prior lookup succeeded, the Web UI shows its time alongside a failure.

The Web UI keeps successful release data in memory for the life of the app process. A current-version result is checked again within one hour; a newer release uses the configured update interval (24 hours by default). Concurrent requests share one lookup. Failed results are not cached as successful: transient failures wait five seconds before retry, successful manual refreshes are reused for one minute, and GitHub rate limits honor `Retry-After` or `X-RateLimit-Reset` (capped at one hour) or wait 60 seconds by default. The notice shows the retry time and disables retry until then.

The update notice includes “Check Again” to refresh a successful result. Installing an update continues through the existing safe update flow.
