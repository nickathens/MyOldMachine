# Regex

Regular expression testing and text extraction.

## Capabilities

- **Test**: Validate regex patterns
- **Match**: Find matches in text
- **Replace**: Search and replace with regex
- **Extract**: Pull data from text

## Commands

```bash
# Test pattern
python3 -c "import re; print(re.findall(r'\d+', 'abc123def456'))"

# Find all matches
python3 -c "import re; print(re.findall(r'\d+', 'abc123def456'))"

# Replace
python3 -c "import re; print(re.sub(r'\d+', 'X', 'abc123def456'))"

# Extract groups
python3 -c "import re; m=re.search(r'(\w+)@(\w+\.\w+)', 'email@domain.com'); print(m.groups() if m else 'No match')"
```

## Common Patterns

| Pattern | Matches |
|---------|---------|
| `\d+` | Numbers |
| `\w+` | Words |
| `[a-zA-Z]+` | Letters only |
| `\S+@\S+` | Email (simple) |
| `https?://\S+` | URLs |
| `\b\d{3}-\d{4}\b` | Phone (xxx-xxxx) |

## Examples

"Test this regex pattern"
"Extract all emails from this text"
"Find all numbers in this string"
"Replace all URLs with [LINK]"

## Notes

- Uses Python's built-in `re` module — no dependencies needed
