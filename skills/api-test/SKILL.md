# API Test

HTTP API testing and debugging with curl.

## Tools

- **curl** - Classic HTTP client (usually pre-installed)
- **httpie** - Human-friendly HTTP client

## Commands

```bash
# GET request
curl -s https://api.example.com/users | python3 -m json.tool

# POST with JSON
curl -s -X POST https://api.example.com/users \
  -H "Content-Type: application/json" \
  -d '{"name": "John", "email": "john@example.com"}'

# With auth header
curl -s https://api.example.com/data \
  -H "Authorization: Bearer token123"

# Download file
curl -LO https://example.com/file.zip

# HTTPie (if installed)
http GET https://api.example.com/users
http POST https://api.example.com/users name=John email=john@example.com
http GET example.com Header:Value
http --verbose example.com  # Full request/response
```

- `-sS` hides the progress bar but keeps errors; `-f` makes an HTTP error
  (404, 500) exit non zero; `-L` follows redirects; add `--max-time 20` so a
  dead endpoint cannot hang the turn.
- Keep tokens in variables (`$TOKEN`), not pasted into commands that land in
  logs.

## Examples

"Test this API endpoint"
"Send a POST request with this JSON"
"Check if this URL is responding"
"Get the headers from this URL"
