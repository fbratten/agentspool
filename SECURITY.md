# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.4.x   | :white_check_mark: |
| < 0.4   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it privately:

1. **Do NOT** open a public issue
2. Email the maintainer directly or use GitHub's private vulnerability reporting
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 1 week
- **Fix timeline:** Depends on severity

## Security Considerations

This library handles inter-agent communication. Key security features:

- **HMAC-SHA256 authentication** for HTTP relay transport
- **Replay protection** with nonce tracking (5-minute window)
- **Per-agent shared secrets** for relay authentication

### Best Practices

1. Keep relay secrets confidential (`relay_secrets.json`)
2. Use HTTPS for relay communication in production
3. Run relay servers on trusted networks (VPN, Tailscale, etc.)
4. Regularly rotate shared secrets

## Known Limitations

- File transport (`FileTransport`) is intended for development/debugging only
- SQLite spool should be access-controlled at the filesystem level
