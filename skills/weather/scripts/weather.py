#!/usr/bin/env python3
"""
Weather - Get weather forecasts using Open-Meteo API (free, no API key needed).

Usage:
    python weather.py "Athens"
    python weather.py "Athens" --forecast
    python weather.py --lat 37.98 --lon 23.73

Default location: New York, USA (40.71°N, 74.01°W)
"""

import argparse
from datetime import datetime

import httpx

# Default location (New York, USA) — change to your city
DEFAULT_LAT = 40.7128
DEFAULT_LON = -74.0060
DEFAULT_CITY = "New York"

# Weather code descriptions
WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def geocode(city: str) -> tuple:
    """Get coordinates for a city name."""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "en", "format": "json"}

    response = httpx.get(url, params=params, timeout=10)
    data = response.json()

    if not data.get("results"):
        return None, None, None

    result = data["results"][0]
    return result["latitude"], result["longitude"], result.get("name", city)


def get_weather(lat: float, lon: float) -> dict:
    """Get current weather and forecast."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "timezone": "auto",
        "forecast_days": 7
    }

    response = httpx.get(url, params=params, timeout=10)
    return response.json()


def format_current(data: dict, city: str) -> str:
    """Format current weather."""
    current = data["current"]
    units = data["current_units"]

    temp = current["temperature_2m"]
    feels_like = current["apparent_temperature"]
    humidity = current["relative_humidity_2m"]
    wind = current["wind_speed_10m"]
    code = current["weather_code"]
    condition = WEATHER_CODES.get(code, "Unknown")

    lines = [
        f"Weather in {city}",
        f"",
        f"Condition: {condition}",
        f"Temperature: {temp}{units['temperature_2m']}",
        f"Feels like: {feels_like}{units['apparent_temperature']}",
        f"Humidity: {humidity}{units['relative_humidity_2m']}",
        f"Wind: {wind} {units['wind_speed_10m']}",
    ]

    return "\n".join(lines)


def format_forecast(data: dict, city: str) -> str:
    """Format weather forecast."""
    daily = data["daily"]
    units = data["daily_units"]

    lines = [f"7-Day Forecast for {city}", ""]

    for i in range(len(daily["time"])):
        date = datetime.fromisoformat(daily["time"][i]).strftime("%a %b %d")
        code = daily["weather_code"][i]
        condition = WEATHER_CODES.get(code, "Unknown")
        temp_max = daily["temperature_2m_max"][i]
        temp_min = daily["temperature_2m_min"][i]
        precip = daily["precipitation_probability_max"][i]

        lines.append(f"{date}: {condition}")
        lines.append(f"  High: {temp_max}°C / Low: {temp_min}°C")
        if precip and precip > 0:
            lines.append(f"  Precipitation: {precip}%")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Get weather forecast")
    parser.add_argument("city", nargs="?", default=DEFAULT_CITY, help="City name")
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--forecast", action="store_true", help="Show 7-day forecast")
    args = parser.parse_args()

    # Determine coordinates
    if args.lat and args.lon:
        lat, lon = args.lat, args.lon
        city = f"({lat}, {lon})"
    elif args.city != DEFAULT_CITY:
        lat, lon, city = geocode(args.city)
        if lat is None:
            print(f"Could not find location: {args.city}")
            return 1
    else:
        lat, lon, city = DEFAULT_LAT, DEFAULT_LON, DEFAULT_CITY

    # Get weather
    try:
        data = get_weather(lat, lon)
    except Exception as e:
        print(f"Error fetching weather: {e}")
        return 1

    # Format output
    if args.forecast:
        print(format_forecast(data, city))
    else:
        print(format_current(data, city))
        print("")
        # Also show tomorrow
        if "daily" in data:
            tomorrow = data["daily"]
            code = tomorrow["weather_code"][1]
            condition = WEATHER_CODES.get(code, "Unknown")
            temp_max = tomorrow["temperature_2m_max"][1]
            temp_min = tomorrow["temperature_2m_min"][1]
            print(f"Tomorrow: {condition}, {temp_min}°C - {temp_max}°C")

    return 0


if __name__ == "__main__":
    exit(main())
