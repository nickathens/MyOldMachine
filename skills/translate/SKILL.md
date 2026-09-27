# Translation

Machine translation through Google Translate, with MyMemory as the fallback.

**Translate yourself first.** For Greek and English, and most European
languages, your own translation is better than either engine and sends
nothing to a third party. Use this script when someone asks for Google's (or
a machine) translation, for a language you are unsure of, or to cross-check.
Never send confidential client text (contracts, unreleased scripts) through
it: both engines are public web services.

## Commands

```bash
# Translate to English (auto-detect source)
python $SKILL_DIR/scripts/translate.py "Bonjour le monde"

# Translate to Greek
python $SKILL_DIR/scripts/translate.py "Hello world" --to el

# Translate from Greek to English
python $SKILL_DIR/scripts/translate.py "Γεια σου" --from el --to en

# A long text from a file (split at paragraph and sentence breaks)
python skills/translate/scripts/translate.py --file letter.txt --from el --to en

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

- Default target language is English. The output names the engine that answered
- Google can answer a machine with its "unusual traffic" page (302 to /sorry, 429 on the API; the Linux bot has had it since 2026-09-27), which the library reports as "too many requests"; waiting does not clear it. The script then falls back to MyMemory, which has no auto-detection: it guesses the source from the script for Greek, Cyrillic, Arabic, Hebrew and CJK text, and otherwise asks for `--from`. MyMemory's quality is lower and its free quota is daily; a quota warning is an error, not a translation
- Long texts are split under each engine's limit (Google 5000 characters, MyMemory 500) at line, sentence and word breaks; the old script failed on anything over 5000 and printed the whole text back as the error
- The engine's detected source language is not exposed, so auto mode says "auto-detected by the engine" rather than inventing one
