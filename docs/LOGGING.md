# Logging Documentation

This document explains the runtime logging architecture of ValuedCRM.

## Log Files
For development and production auditing, logs are saved to the `/logs` directory (excluded from version control):

| File | Content |
| :--- | :--- |
| `logs/app.log` | General application logs (Services, Views, Business Logic). |
| `logs/clickfunnels.log` | Dedicated integration logs for ClickFunnels API transactions. |
| `logs/errors.log` | Aggregated ERROR and CRITICAL level logs for troubleshooting. |

## Log Monitoring
You can monitor logs in real-time using the `tail` command:

```bash
# Monitor all application activity
tail -f logs/app.log

# Monitor only ClickFunnels API calls
tail -f logs/clickfunnels.log

# Monitor errors across the entire system
tail -f logs/errors.log
```

## Security & Privacy
ValuedCRM implements a multi-layer scrubbing system to protect sensitive data:

1.  **SecretScrubberFilter:** A regex-based filter that redacts `Authorization` headers, `Bearer` tokens, and `API Keys` from log messages before they are written to files or `stdout`.
2.  **Whitelist Sanitization:** The `_sanitize_data` helper ensures only safe identifiers (e.g., `workspace_id`, `contact_id`, `duration_ms`) are included in log metadata.

**NEVER LOG:**
- Decrypted API Access Tokens
- Full Contact Payloads (Emails, Phones, Addresses)
- Full Response JSON from ClickFunnels (unless in the `EnrollmentAttempt` database record)

## Runtime Logs vs. Audit Records
It is important to distinguish between **Runtime Logs** and **Audit Records**:

*   **Runtime Logs (Files/Stdout):** Volatile, high-volume debugging information used for system monitoring. They are scrubbed of PII and secrets.
*   **Audit Records (Database):** Persistent records stored in the `EnrollmentAttempt` model. These contain the full ClickFunnels response and student email, intended for business verification and individual troubleshooting. Access is restricted to authenticated staff.
