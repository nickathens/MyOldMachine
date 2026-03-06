# Translation

Translate text between languages using Google Translate (free).

## Commands

```bash
# Translate to English (auto-detect source)
python $SKILL_DIR/scripts/translate.py "Bonjour le monde"

# Translate to Greek
python $SKILL_DIR/scripts/translate.py "Hello world" --to el

# Translate from Greek to English
python $SKILL_DIR/scripts/translate.py "Γεια σου" --from el --to en

# List language codes
python $SKILL_DIR/scripts/translate.py --languages
```

## Common Language Codes

| Code | Language |
|------|----------|
| en | English |
| el | Greek |
| es | Spanish |
| fr | French |
| de | German |
| it | Italian |
| pt | Portuguese |
| ru | Russian |
| zh-CN | Chinese |
| ja | Japanese |
| ko | Korean |
| ar | Arabic |
| tr | Turkish |

## Notes

- Auto-detects source language when --from is not specified
- Default target language is English
- Uses Google Translate (free, no API key needed)
