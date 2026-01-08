"""
API Meteo per Slappy Bot
- Open-Meteo API (gratuita) per meteo atmosferico
- Open-Meteo Marine API (gratuita) per condizioni mare
- Stormglass API per maree
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import httpx

from config import STORMGLASS_API_KEY

logger = logging.getLogger(__name__)

# Coordinate Cavallino-Treporti
LAT = 45.4833
LON = 12.5500

# Timeout per le richieste HTTP
HTTP_TIMEOUT = 10.0


async def get_meteo_forecast() -> Optional[Dict[str, Any]]:
    """
    Ottiene previsioni meteo atmosferico da Open-Meteo API (gratuita).
    Ritorna dati per oggi e domani.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
        "timezone": "Europe/Rome",
        "forecast_days": 3
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Formatta i dati per uso facile
            current = data.get("current", {})
            daily = data.get("daily", {})

            return {
                "current": {
                    "temperature": current.get("temperature_2m"),
                    "feels_like": current.get("apparent_temperature"),
                    "humidity": current.get("relative_humidity_2m"),
                    "precipitation": current.get("precipitation"),
                    "weather_code": current.get("weather_code"),
                    "wind_speed": current.get("wind_speed_10m"),
                    "wind_direction": current.get("wind_direction_10m")
                },
                "daily": {
                    "dates": daily.get("time", []),
                    "weather_codes": daily.get("weather_code", []),
                    "temp_max": daily.get("temperature_2m_max", []),
                    "temp_min": daily.get("temperature_2m_min", []),
                    "precipitation": daily.get("precipitation_sum", []),
                    "precipitation_prob": daily.get("precipitation_probability_max", []),
                    "wind_max": daily.get("wind_speed_10m_max", [])
                }
            }

    except httpx.TimeoutException:
        logger.error("Timeout chiamata Open-Meteo API")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"Errore HTTP Open-Meteo: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Errore Open-Meteo API: {e}")
        return None


async def get_marine_conditions() -> Optional[Dict[str, Any]]:
    """
    Ottiene condizioni marine da Open-Meteo Marine API (gratuita).
    Altezza onde, direzione, periodo.
    """
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "wave_height,wave_direction,wave_period,wind_wave_height,swell_wave_height",
        "daily": "wave_height_max,wave_direction_dominant,wave_period_max",
        "timezone": "Europe/Rome",
        "forecast_days": 3
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            return {
                "current": {
                    "wave_height": current.get("wave_height"),
                    "wave_direction": current.get("wave_direction"),
                    "wave_period": current.get("wave_period"),
                    "wind_wave_height": current.get("wind_wave_height"),
                    "swell_wave_height": current.get("swell_wave_height")
                },
                "daily": {
                    "dates": daily.get("time", []),
                    "wave_height_max": daily.get("wave_height_max", []),
                    "wave_direction": daily.get("wave_direction_dominant", []),
                    "wave_period_max": daily.get("wave_period_max", [])
                }
            }

    except httpx.TimeoutException:
        logger.error("Timeout chiamata Open-Meteo Marine API")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"Errore HTTP Open-Meteo Marine: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Errore Open-Meteo Marine API: {e}")
        return None


async def get_tides() -> Optional[Dict[str, Any]]:
    """
    Ottiene orari maree da Stormglass API.
    Richiede API key in .env: STORMGLASS_API_KEY
    """
    if not STORMGLASS_API_KEY:
        logger.warning("STORMGLASS_API_KEY non configurata")
        return None

    url = "https://api.stormglass.io/v2/tide/extremes/point"
    params = {
        "lat": LAT,
        "lng": LON
    }
    headers = {
        "Authorization": STORMGLASS_API_KEY
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

            extremes = data.get("data", [])

            # Filtra solo le maree di oggi e domani
            today = datetime.now().date()
            filtered = []
            for extreme in extremes:
                try:
                    dt = datetime.fromisoformat(extreme["time"].replace("Z", "+00:00"))
                    if dt.date() >= today:
                        filtered.append({
                            "time": dt.strftime("%H:%M"),
                            "date": dt.strftime("%Y-%m-%d"),
                            "type": extreme.get("type"),  # "high" o "low"
                            "height": extreme.get("height")
                        })
                except (KeyError, ValueError):
                    continue

            return {
                "extremes": filtered[:8]  # Prossime 8 maree (circa 2 giorni)
            }

    except httpx.TimeoutException:
        logger.error("Timeout chiamata Stormglass API")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"Errore HTTP Stormglass: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Errore Stormglass API: {e}")
        return None


def get_weather_emoji(code: int) -> str:
    """Converte weather code WMO in emoji"""
    weather_emojis = {
        0: "☀️",      # Clear sky
        1: "🌤️",     # Mainly clear
        2: "⛅",      # Partly cloudy
        3: "☁️",      # Overcast
        45: "🌫️",    # Fog
        48: "🌫️",    # Depositing rime fog
        51: "🌧️",    # Light drizzle
        53: "🌧️",    # Moderate drizzle
        55: "🌧️",    # Dense drizzle
        56: "🌧️",    # Light freezing drizzle
        57: "🌧️",    # Dense freezing drizzle
        61: "🌧️",    # Slight rain
        63: "🌧️",    # Moderate rain
        65: "🌧️",    # Heavy rain
        66: "🌧️",    # Light freezing rain
        67: "🌧️",    # Heavy freezing rain
        71: "🌨️",    # Slight snow
        73: "🌨️",    # Moderate snow
        75: "🌨️",    # Heavy snow
        77: "🌨️",    # Snow grains
        80: "🌦️",    # Slight rain showers
        81: "🌦️",    # Moderate rain showers
        82: "🌦️",    # Violent rain showers
        85: "🌨️",    # Slight snow showers
        86: "🌨️",    # Heavy snow showers
        95: "⛈️",    # Thunderstorm
        96: "⛈️",    # Thunderstorm with slight hail
        99: "⛈️"     # Thunderstorm with heavy hail
    }
    return weather_emojis.get(code, "🌡️")


def get_weather_description(code: int, lang: str = "it") -> str:
    """Converte weather code WMO in descrizione testuale"""
    descriptions = {
        "it": {
            0: "Sereno",
            1: "Prevalentemente sereno",
            2: "Parzialmente nuvoloso",
            3: "Coperto",
            45: "Nebbia",
            48: "Nebbia con brina",
            51: "Pioviggine leggera",
            53: "Pioviggine moderata",
            55: "Pioviggine intensa",
            61: "Pioggia leggera",
            63: "Pioggia moderata",
            65: "Pioggia intensa",
            71: "Neve leggera",
            73: "Neve moderata",
            75: "Neve intensa",
            80: "Rovesci leggeri",
            81: "Rovesci moderati",
            82: "Rovesci intensi",
            95: "Temporale",
            96: "Temporale con grandine",
            99: "Temporale con grandine forte"
        },
        "en": {
            0: "Clear sky",
            1: "Mainly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            71: "Slight snow",
            73: "Moderate snow",
            75: "Heavy snow",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            95: "Thunderstorm",
            96: "Thunderstorm with hail",
            99: "Thunderstorm with heavy hail"
        },
        "de": {
            0: "Klar",
            1: "Überwiegend klar",
            2: "Teilweise bewölkt",
            3: "Bedeckt",
            45: "Nebel",
            48: "Nebel mit Reif",
            51: "Leichter Nieselregen",
            53: "Mäßiger Nieselregen",
            55: "Starker Nieselregen",
            61: "Leichter Regen",
            63: "Mäßiger Regen",
            65: "Starker Regen",
            71: "Leichter Schnee",
            73: "Mäßiger Schnee",
            75: "Starker Schnee",
            80: "Leichte Regenschauer",
            81: "Mäßige Regenschauer",
            82: "Starke Regenschauer",
            95: "Gewitter",
            96: "Gewitter mit Hagel",
            99: "Gewitter mit starkem Hagel"
        }
    }
    lang_desc = descriptions.get(lang, descriptions["it"])
    return lang_desc.get(code, "N/D")


def get_wind_direction_text(degrees: float, lang: str = "it") -> str:
    """Converte gradi in direzione cardinale"""
    directions = {
        "it": ["N", "NE", "E", "SE", "S", "SO", "O", "NO"],
        "en": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "de": ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
    }
    if degrees is None:
        return "N/D"
    idx = round(degrees / 45) % 8
    return directions.get(lang, directions["it"])[idx]


def get_wave_condition(height: float, lang: str = "it") -> str:
    """Valuta condizione mare in base all'altezza onde"""
    conditions = {
        "it": {
            "calm": "Calmo",
            "slight": "Poco mosso",
            "moderate": "Mosso",
            "rough": "Molto mosso",
            "very_rough": "Agitato"
        },
        "en": {
            "calm": "Calm",
            "slight": "Slight",
            "moderate": "Moderate",
            "rough": "Rough",
            "very_rough": "Very rough"
        },
        "de": {
            "calm": "Ruhig",
            "slight": "Leicht bewegt",
            "moderate": "Mäßig bewegt",
            "rough": "Bewegt",
            "very_rough": "Stark bewegt"
        }
    }

    lang_cond = conditions.get(lang, conditions["it"])

    if height is None:
        return "N/D"
    if height < 0.2:
        return lang_cond["calm"]
    if height < 0.5:
        return lang_cond["slight"]
    if height < 1.25:
        return lang_cond["moderate"]
    if height < 2.5:
        return lang_cond["rough"]
    return lang_cond["very_rough"]
