# API Test

HTTP API testing and debugging.

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

## Examples

"Test this API endpoint"
"Send a POST request with this JSON"
"Check if this URL is responding"
"Get the headers from this URL"
