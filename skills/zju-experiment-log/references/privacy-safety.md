# Privacy and destination safety

- Treat human participant identifiers, health information, unpublished sequences, proprietary formulations, access credentials, and sponsor-confidential data as restricted.
- Keep the original local path in the authorized record. Before publishing or syncing externally, use a redacted derivative and preserve a mapping only in an approved location.
- Do not embed passwords, API keys, browser cookies, authentication QR codes, or session tokens.
- Obsidian is a filesystem destination, not an evidence source. Confirm the target vault/path and collision policy before writing.
- Feishu or another cloud destination requires explicit user selection and applicable authorization. Record the destination document ID after a successful write; do not claim a sync occurred when only a local file exists.
- Prefer append-only amendments. Record author, timestamp, changed field, old value, new value, and reason.
