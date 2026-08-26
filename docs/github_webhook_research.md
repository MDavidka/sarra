# GitHub webhook research notes

- GitHub documents that repository webhooks can subscribe to the `push` event, which covers pushes and branch/tag changes. Source: https://docs.github.com/webhooks/webhook-events-and-payloads
- GitHub documents the `X-Hub-Signature-256` header for webhook deliveries configured with a secret and recommends HMAC SHA-256 validation using constant-time comparison. Source: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- The user selected periodic branch checking rather than repository-push webhooks, so Sycord uses encrypted GitHub OAuth credentials to query the configured branch head every five minutes. The webhook research remains relevant for a future instant-trigger option.
