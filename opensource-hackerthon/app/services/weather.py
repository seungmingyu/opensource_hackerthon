import os, time, requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))

OW_KEY = os.getenv("OPENWEATHERMAP")
DEFAULT_LAT, DEFAULT_LON = 35.6462, 126.5051
#DEFAULT_LAT, DEFAULT_LON = 14.59, 120.98

_LANG, _UNITS = "kr", "metric"
_cache = {"key": None, "data": None, "ts": 0, "ttl": 600}

def _k(lat: float, lon: float) -> Tuple[float, float]:
    return (round(lat, 4), round(lon, 4))

def get_current_weather(lat: Optional[float]=None, lon: Optional[float]=None) -> dict:
    lat = lat or DEFAULT_LAT; lon = lon or DEFAULT_LON
    key = (_k(lat, lon), _LANG, _UNITS)
    now = time.time()
    if _cache["key"] == key and now - _cache["ts"] < _cache["ttl"]:
        return _cache["data"]
    r = requests.get("https://api.openweathermap.org/data/2.5/weather",
        params={"lat":lat,"lon":lon,"appid":OW_KEY,"units":_UNITS,"lang":_LANG}, timeout=10)
    r.raise_for_status()
    _cache.update(key=key, data=r.json(), ts=now)
    return _cache["data"]

def resolve_mood(w: dict, now: Optional[datetime]=None) -> dict:
    """날씨와 시간대를 분석하여 음악 분위기 결정"""
    # 한국 시간대(KST)로 현재 시간 가져오기
    now = now or datetime.now(KST)
    main = (w.get("weather",[{}])[0].get("main","Clear")).lower()
    feels = float(w.get("main",{}).get("feels_like", 18.0))
    wind = float(w.get("wind",{}).get("speed", 2.0))
    humidity = float(w.get("main",{}).get("humidity", 50))
    h = now.hour
    
    # 🌙 새벽 시간대 (0~6시)
    if 0 <= h < 6:
        if 12 <= feels <= 18:
            return {
                "rule": "dawn_cool",
                "keywords": ["새벽", "감성", "lofi", "잔잔한"]
            }
        elif feels < 12:
            return {
                "rule": "dawn_cold",
                "keywords": ["추운밤", "새벽", "잔잔한", "겨울밤"]
            }
        else:
            return {
                "rule": "dawn_warm",
                "keywords": ["밤", "휴식", "편안한", "새벽"]
            }
    
    # 🌅 아침 시간대 (6~12시)
    elif 6 <= h < 12:
        if "rain" in main:
            return {
                "rule": "morning_rain",
                "keywords": ["아침비", "잔잔한", "카페", "감성"]
            }
        elif "cloud" in main:
            return {
                "rule": "morning_cloudy",
                "keywords": ["아침", "브런치", "인디", "카페"]
            }
        elif feels >= 25:
            return {
                "rule": "morning_hot",
                "keywords": ["더운아침", "상쾌한", "여름", "밝은"]
            }
        else:
            return {
                "rule": "morning_clear",
                "keywords": ["아침", "상쾌한", "기분좋은", "활기찬"]
            }
    
    # ☀️ 낮 시간대 (12~18시)
    elif 12 <= h < 18:
        if "rain" in main or w.get("rain"):
            if wind >= 5:
                return {
                    "rule": "afternoon_storm",
                    "keywords": ["소나기", "비바람", "감성", "빗소리"]
                }
            else:
                return {
                    "rule": "afternoon_rain",
                    "keywords": ["오후비", "비오는날", "감성", "카페"]
                }
        
        elif "snow" in main or w.get("snow"):
            return {
                "rule": "afternoon_snow",
                "keywords": ["겨울", "눈오는날", "따뜻한", "감성"]
            }
        
        elif "cloud" in main:
            if humidity >= 70:
                return {
                    "rule": "afternoon_humid_cloudy",
                    "keywords": ["흐린날", "답답한", "lofi", "차분한"]
                }
            else:
                return {
                    "rule": "afternoon_cloudy",
                    "keywords": ["흐림", "구름", "차분한", "감성"]
                }
        
        # 맑은 날 세분화
        elif feels >= 30:
            return {
                "rule": "afternoon_very_hot",
                "keywords": ["폭염", "시원한", "여름", "밝은"]
            }
        elif feels >= 25:
            return {
                "rule": "afternoon_hot",
                "keywords": ["더운날", "여름", "활기찬", "신나는"]
            }
        elif 18 <= feels < 25:
            if wind >= 5:
                return {
                    "rule": "afternoon_windy",
                    "keywords": ["바람부는날", "시원한", "상쾌한", "산책"]
                }
            else:
                return {
                    "rule": "afternoon_perfect",
                    "keywords": ["좋은날씨", "산책", "나들이", "기분좋은"]
                }
        elif 10 <= feels < 18:
            return {
                "rule": "afternoon_cool",
                "keywords": ["가을", "선선한", "산책", "감성"]
            }
        else:
            return {
                "rule": "afternoon_cold",
                "keywords": ["추운날", "겨울", "포근한", "따뜻한"]
            }
    
    # 🌆 저녁 시간대 (18~24시)
    else:
        if "rain" in main or w.get("rain"):
            return {
                "rule": "evening_rain",
                "keywords": ["저녁비", "밤비", "감성", "잔잔한"]
            }
        
        elif "cloud" in main:
            return {
                "rule": "evening_cloudy",
                "keywords": ["저녁", "흐린밤", "차분한", "감성"]
            }
        
        elif feels >= 25:
            return {
                "rule": "evening_warm",
                "keywords": ["따뜻한저녁", "야경", "드라이브", "여름밤"]
            }
        
        elif 18 <= feels < 25:
            if wind >= 5:
                return {
                    "rule": "evening_breezy",
                    "keywords": ["저녁바람", "드라이브", "시원한", "밤"]
                }
            else:
                return {
                    "rule": "evening_perfect",
                    "keywords": ["좋은저녁", "산책", "여유", "밤"]
                }
        
        else:
            return {
                "rule": "evening_cold",
                "keywords": ["추운저녁", "겨울밤", "따뜻한", "집"]
            }