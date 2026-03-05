# Weather

Get current weather and forecasts for any location.

## Commands

```bash
# Current weather (default: New York)
python skills/weather/scripts/weather.py

# Weather for a specific city
python skills/weather/scripts/weather.py "London"
python skills/weather/scripts/weather.py "Tokyo"

# 7-day forecast
python skills/weather/scripts/weather.py "Athens" --forecast

# By coordinates
python skills/weather/scripts/weather.py --lat 40.7128 --lon -74.0060
```

## Notes

- Uses Open-Meteo API (free, no API key needed)
- Temperatures in Celsius
- Shows current conditions and tomorrow's forecast by default
- Use --forecast for full 7-day outlook
