import os
import re
import time
import base64
import random
import hashlib
from typing import List, Dict, Optional
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from collections import Counter
from dotenv import load_dotenv
from app.routers.user_router import current_user
from app.core.database import get_db

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

_token = {"val": None, "exp": 0}

router = APIRouter(prefix="/lastfm", tags=["lastfm"])

# ====== Spotify (선택) ======
def spotify_token() -> str:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("No Spotify credentials")
    now = time.time()
    if _token["val"] and _token["exp"] - now > 20:
        return _token["val"]
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    with httpx.Client(timeout=15) as c:
        r = c.post(SPOTIFY_TOKEN_URL, data={"grant_type": "client_credentials"},
                   headers={"Authorization": f"Basic {auth}"})
        r.raise_for_status()
        js = r.json()
    _token["val"] = js["access_token"]
    _token["exp"] = now + js.get("expires_in", 3600)
    return _token["val"]


def parse_playlist_id(url: str) -> str:
    m = re.search(r"(playlist/|spotify:playlist:)([A-Za-z0-9]+)", url)
    if not m:
        raise ValueError("Invalid playlist URL")
    return m.group(2)


async def get_spotify_tracks_text(playlist_url: str) -> List[Dict]:
    """Spotify 플레이리스트에서 (곡명, 아티스트명)만 추출"""
    print(f"   🔍 Spotify 플레이리스트 접근 중...")
    try:
        pid = parse_playlist_id(playlist_url)
        print(f"   📝 플레이리스트 ID: {pid}")
        token = spotify_token()
        print(f"   🔑 Spotify 토큰 획득 완료")
    except Exception as e:
        print(f"   ❌ Spotify 접근 실패: {e}")
        return []
    
    out = []
    url = f"{SPOTIFY_API}/playlists/{pid}/tracks?limit=100"
    page_count = 0
    
    async with httpx.AsyncClient(timeout=20) as c:
        while url:
            page_count += 1
            print(f"   📄 페이지 {page_count} 로딩 중...")
            
            r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 404:
                print(f"   ❌ 플레이리스트를 찾을 수 없습니다 (404)")
                break
            r.raise_for_status()
            js = r.json()
            
            items_in_page = 0
            for it in js.get("items", []):
                t = (it or {}).get("track") or {}
                if t.get("type") == "track" and not t.get("is_local", False):
                    name = t.get("name")
                    arts = [a["name"] for a in t.get("artists", [])]
                    if name and arts:
                        out.append({"name": name, "artists": arts})
                        items_in_page += 1
            
            print(f"      ✓ {items_in_page}개 트랙 추출")
            url = js.get("next")
            
            if len(out) >= 200:  # 최대 200곡까지만
                print(f"   ⚠️  최대 곡 수 도달 (200개)")
                break
    
    print(f"   ✅ 총 {len(out)}개 트랙 추출 완료")
    return out


# ====== Last.fm ======
LASTFM = "https://ws.audioscrobbler.com/2.0/"


async def lastfm_get(method: str, params: Dict) -> Dict:
    q = {"method": method, "api_key": LASTFM_API_KEY, "format": "json"}
    q.update(params)
    headers = {
        "User-Agent": "MusicRecommender/1.0",
        "Accept": "application/json"
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(LASTFM, params=q, headers=headers)
        r.raise_for_status()
        return r.json()


async def lf_track_tags(artist: str, track: str) -> List[str]:
    try:
        js = await lastfm_get("track.getTopTags", {"artist": artist, "track": track})
        tags = js.get("toptags", {}).get("tag", [])
        return [t.get("name", "").lower() for t in tags if isinstance(t, dict)]
    except Exception as e:
        print(f"[Last.fm] 태그 조회 실패 ({artist} - {track}): {e}")
        return []


async def lf_similar_tracks(artist: str, track: str, limit=20) -> List[Dict]:
    try:
        js = await lastfm_get("track.getSimilar", {"artist": artist, "track": track, "limit": limit})
        return [{"name": it.get("name"), "artist": it.get("artist", {}).get("name")}
                for it in js.get("similartracks", {}).get("track", []) if it.get("name")]
    except Exception as e:
        print(f"[Last.fm] 유사 트랙 조회 실패 ({artist} - {track}): {e}")
        return []


async def lf_top_by_tag(tag: str, limit=30) -> List[Dict]:
    try:
        js = await lastfm_get("tag.getTopTracks", {"tag": tag, "limit": limit})
        return [{"name": it.get("name"), "artist": it.get("artist", {}).get("name")}
                for it in js.get("tracks", {}).get("track", []) if it.get("name")]
    except Exception as e:
        print(f"[Last.fm] 태그별 트랙 조회 실패 ({tag}): {e}")
        return []


# ====== 개선된 무드 매핑 ======
# 1. 감정 축
# 긍정적/밝은 분위기
BRIGHT_HAPPY = {"happy", "upbeat", "cheerful", "fun", "party", "energetic", "positive", "uplifting", "feel good", "joyful", "euphoric"}
# 부정적/어두운 분위기  
DARK_SAD = {"sad", "melancholy", "depressing", "dark", "gloomy", "somber", "emotional", "tearjerker", "heartbreak", "lonely", "moody", "melancholic"}

# 2. 에너지 축
# 에너지 높음
HIGH_ENERGY = {"rock", "metal", "punk", "hardcore", "aggressive", "intense", "heavy", "hard rock", "energetic", "powerful", "explosive"}
# 에너지 낮음
LOW_ENERGY = {"ambient", "chillout", "downtempo", "sleep", "meditation", "peaceful", "tranquil", "slow", "calm", "relaxing", "soothing"}

# 3. 활동성 축
# 신나는 음악
DANCEABLE = {"dance", "edm", "house", "techno", "electro", "club", "disco", "electronic dance", "party", "upbeat", "groove"}
# 차분한 음악
CALM = {"acoustic", "piano", "classical", "jazz", "folk", "ballad", "soft", "gentle", "mellow", "chill", "smooth"}

# 4. 대중성 축
# 팝/메인스트림
MAINSTREAM = {"pop", "top 40", "chart", "radio", "mainstream", "commercial", "popular"}
# 실험적/언더그라운드
ALTERNATIVE = {"indie", "alternative", "experimental", "underground", "art rock", "avant-garde", "progressive"}

# 5. 계절 축 ⭐ 새로 추가!
# 여름 분위기
SUMMER = {"summer", "tropical", "beach", "sunshine", "vacation", "hot", "sunny", "reggae", "latin", "caribbean", "island"}
# 겨울 분위기
WINTER = {"winter", "cold", "snow", "christmas", "cozy", "warm", "fireplace", "melancholic", "nostalgic"}
# 봄 분위기
SPRING = {"spring", "fresh", "blossom", "renewal", "light", "cheerful", "bright", "new beginning"}
# 가을 분위기
AUTUMN = {"autumn", "fall", "mellow", "nostalgic", "rainy", "contemplative", "introspective", "cozy"}

# 6. 시간대 축 ⭐ 새로 추가!
# 아침 분위기
MORNING = {"morning", "wake up", "sunrise", "fresh", "energizing", "coffee", "starting", "bright"}
# 밤 분위기
NIGHT = {"night", "midnight", "nocturnal", "dreamy", "mysterious", "late night", "moonlight", "starry"}
# 저녁 분위기
EVENING = {"evening", "sunset", "twilight", "romantic", "dinner", "wine", "mellow", "golden hour"}

# 7. 활동 축 ⭐ 새로 추가!
# 운동/활동적
WORKOUT = {"workout", "gym", "running", "exercise", "training", "fitness", "motivation", "power"}
# 공부/집중
STUDY = {"study", "focus", "concentration", "work", "productive", "reading", "background", "instrumental"}
# 휴식/수면
SLEEP = {"sleep", "lullaby", "bedtime", "rest", "peaceful", "quiet", "serene", "dreamy"}
# 파티/사교
PARTY = {"party", "celebration", "social", "fun", "festive", "drinking", "club", "dance"}

# 8. 감성 축 ⭐ 새로 추가!
# 로맨틱
ROMANTIC = {"romantic", "love", "sweet", "tender", "intimate", "passionate", "sensual", "loving"}
# 향수/추억
NOSTALGIC = {"nostalgic", "memories", "throwback", "retro", "vintage", "old school", "reminiscent", "sentimental"}
# 몽환적
DREAMY = {"dreamy", "ethereal", "atmospheric", "floating", "surreal", "psychedelic", "spacey", "hypnotic"}
# 강렬한
INTENSE = {"intense", "dramatic", "epic", "powerful", "emotional", "passionate", "raw", "visceral"}

# 9. 문화/지역 축 ⭐ 새로 추가!
# K-POP
KPOP = {"k-pop", "kpop", "korean", "idol", "korean pop"}
# J-POP  
JPOP = {"j-pop", "jpop", "japanese", "anime", "japanese pop"}
# 라틴
LATIN = {"latin", "spanish", "salsa", "reggaeton", "bachata", "brazilian", "samba"}
# 힙합/랩
HIPHOP = {"hip-hop", "hip hop", "rap", "trap", "underground rap", "boom bap"}

# 10. 악기/사운드 축 ⭐ 새로 추가!
# 보컬 중심
VOCAL = {"vocal", "singing", "acapella", "choir", "voices", "harmonies"}
# 악기 중심
INSTRUMENTAL = {"instrumental", "no vocals", "orchestral", "symphony", "beats", "background"}

# 🆕 장르별 분위기 추론 (감정 태그가 없을 때 사용)
GENRE_TO_MOOD = {
    # 차분한 장르들
    "rnb": "calm", "r&b": "calm", "soul": "calm", "neo-soul": "calm",
    "lo-fi": "calm", "lofi": "calm", "chillhop": "calm",
    "singer-songwriter": "calm", "indie folk": "calm",
    "trip-hop": "calm", "downtempo": "calm",
    
    # 신나는 장르들  
    "house": "energetic", "techno": "energetic", "trance": "energetic",
    "drum and bass": "energetic", "dubstep": "energetic",
    "hardstyle": "energetic", "big room": "energetic",
    
    # 우울/어두운 장르들
    "emo": "dark", "gothic": "dark", "doom": "dark",
    "trap": "dark",  # 트랩은 보통 어두운 분위기
    
    # 밝은 장르들
    "bubblegum pop": "bright", "k-pop": "bright", "j-pop": "bright",
    "disco": "bright", "funk": "bright"
}


def invert_tagset(tags: List[str]) -> List[str]:
    """태그를 분석해서 주된 분위기의 반대 생성 - 10가지 축 지원"""
    s = set(t.lower() for t in tags)
    
    print(f"   🔍 태그 분석 (총 {len(s)}개): {', '.join(list(s)[:15])}")
    
    # 모든 카테고리별 점수 계산
    scores = {
        "bright": len(s & BRIGHT_HAPPY),
        "dark": len(s & DARK_SAD),
        "high_energy": len(s & HIGH_ENERGY),
        "low_energy": len(s & LOW_ENERGY),
        "danceable": len(s & DANCEABLE),
        "calm": len(s & CALM),
        "summer": len(s & SUMMER),
        "winter": len(s & WINTER),
        "spring": len(s & SPRING),
        "autumn": len(s & AUTUMN),
        "morning": len(s & MORNING),
        "night": len(s & NIGHT),
        "evening": len(s & EVENING),
        "workout": len(s & WORKOUT),
        "study": len(s & STUDY),
        "sleep": len(s & SLEEP),
        "party": len(s & PARTY),
        "romantic": len(s & ROMANTIC),
        "nostalgic": len(s & NOSTALGIC),
        "dreamy": len(s & DREAMY),
        "intense": len(s & INTENSE),
        "kpop": len(s & KPOP),
        "jpop": len(s & JPOP),
        "latin": len(s & LATIN),
        "hiphop": len(s & HIPHOP),
        "vocal": len(s & VOCAL),
        "instrumental": len(s & INSTRUMENTAL),
    }
    
    # 장르 힌트 추가
    genre_hints = {"calm": 0, "energetic": 0, "dark": 0, "bright": 0}
    for tag in s:
        if tag in GENRE_TO_MOOD:
            mood = GENRE_TO_MOOD[tag]
            genre_hints[mood] += 1
    
    if any(genre_hints.values()):
        scores["calm"] += genre_hints["calm"]
        scores["danceable"] += genre_hints["energetic"]
        scores["dark"] += genre_hints["dark"]
        scores["bright"] += genre_hints["bright"]
    
    # 점수 출력 (의미있는 것만)
    print(f"   📊 분위기 점수:")
    meaningful = {k: v for k, v in scores.items() if v > 0}
    if meaningful:
        for k, v in sorted(meaningful.items(), key=lambda x: x[1], reverse=True)[:8]:
            print(f"      {k}: {v}")
    
    opposite = []
    reason = ""
    
    # 우선순위별 체크
    
    # 1️⃣ 계절 축 (가장 구체적!)
    season_scores = {
        "summer": scores["summer"],
        "winter": scores["winter"],
        "spring": scores["spring"],
        "autumn": scores["autumn"]
    }
    max_season = max(season_scores.items(), key=lambda x: x[1])
    if max_season[1] >= 2:
        if max_season[0] == "summer":
            reason = "여름 분위기 → 겨울/차분한 분위기로"
            opposite = ["winter", "cold", "cozy", "calm", "acoustic", "piano", "mellow", "warm"]
        elif max_season[0] == "winter":
            reason = "겨울 분위기 → 여름/신나는 분위기로"
            opposite = ["summer", "tropical", "beach", "upbeat", "sunny", "dance", "energetic", "fun"]
        elif max_season[0] == "spring":
            reason = "봄 분위기 → 가을/성숙한 분위기로"
            opposite = ["autumn", "mellow", "nostalgic", "contemplative", "jazz", "folk"]
        elif max_season[0] == "autumn":
            reason = "가을 분위기 → 봄/밝은 분위기로"
            opposite = ["spring", "fresh", "bright", "cheerful", "uplifting", "new"]
    
    # 2️⃣ 시간대 축
    if not opposite:
        time_scores = {
            "morning": scores["morning"],
            "night": scores["night"],
            "evening": scores["evening"]
        }
        max_time = max(time_scores.items(), key=lambda x: x[1])
        if max_time[1] >= 2:
            if max_time[0] == "morning":
                reason = "아침 분위기 → 밤 분위기로"
                opposite = ["night", "midnight", "dreamy", "mysterious", "dark", "ambient"]
            elif max_time[0] == "night":
                reason = "밤 분위기 → 아침 분위기로"
                opposite = ["morning", "fresh", "energizing", "bright", "upbeat", "wake up"]
            elif max_time[0] == "evening":
                reason = "저녁 분위기 → 낮 분위기로"
                opposite = ["daytime", "energetic", "active", "bright", "uplifting"]
    
    # 3️⃣ 활동 축
    if not opposite:
        activity_scores = {
            "workout": scores["workout"],
            "study": scores["study"],
            "sleep": scores["sleep"],
            "party": scores["party"]
        }
        max_activity = max(activity_scores.items(), key=lambda x: x[1])
        if max_activity[1] >= 2:
            if max_activity[0] == "workout":
                reason = "운동 음악 → 휴식 음악으로"
                opposite = ["sleep", "relaxing", "calm", "peaceful", "ambient", "soft"]
            elif max_activity[0] == "study":
                reason = "공부 음악 → 파티 음악으로"
                opposite = ["party", "dance", "fun", "energetic", "upbeat", "club"]
            elif max_activity[0] == "sleep":
                reason = "수면 음악 → 운동 음악으로"
                opposite = ["workout", "energetic", "power", "intense", "motivation", "rock"]
            elif max_activity[0] == "party":
                reason = "파티 음악 → 집중 음악으로"
                opposite = ["study", "focus", "calm", "peaceful", "instrumental", "background"]
    
    # 4️⃣ 감성 축
    if not opposite:
        emotion_styles = {
            "romantic": scores["romantic"],
            "nostalgic": scores["nostalgic"],
            "dreamy": scores["dreamy"],
            "intense": scores["intense"]
        }
        max_emotion = max(emotion_styles.items(), key=lambda x: x[1])
        if max_emotion[1] >= 2:
            if max_emotion[0] == "romantic":
                reason = "로맨틱 → 강렬한 음악으로"
                opposite = ["intense", "powerful", "aggressive", "rock", "metal", "dramatic"]
            elif max_emotion[0] == "nostalgic":
                reason = "향수적 → 미래적/현대적 음악으로"
                opposite = ["modern", "electronic", "edm", "futuristic", "techno", "progressive"]
            elif max_emotion[0] == "dreamy":
                reason = "몽환적 → 현실적/직설적 음악으로"
                opposite = ["raw", "realistic", "rock", "punk", "aggressive", "direct"]
            elif max_emotion[0] == "intense":
                reason = "강렬함 → 부드러운 음악으로"
                opposite = ["soft", "gentle", "calm", "peaceful", "mellow", "smooth"]
    
    # 5️⃣ 감정 축 (밝음 vs 어두움)
    if not opposite:
        emotion_diff = abs(scores["bright"] - scores["dark"])
        if emotion_diff >= 2:
            if scores["bright"] > scores["dark"]:
                reason = "밝고 행복함 → 어두운 음악으로"
                opposite = ["sad", "melancholy", "dark", "emotional", "depressing", "somber"]
            else:
                reason = "어둡고 우울함 → 밝은 음악으로"
                opposite = ["happy", "upbeat", "cheerful", "positive", "uplifting", "joyful"]
    
    # 6️⃣ 활동성 축 (신남 vs 차분)
    if not opposite:
        activity_diff = abs(scores["danceable"] - scores["calm"])
        if activity_diff >= 2:
            if scores["danceable"] > scores["calm"]:
                reason = "신나고 활동적 → 차분한 음악으로"
                opposite = ["acoustic", "piano", "ballad", "soft", "calm", "peaceful"]
            else:
                reason = "차분하고 조용함 → 신나는 음악으로"
                opposite = ["dance", "party", "energetic", "upbeat", "edm", "house"]
    
    # 7️⃣ 에너지 축
    if not opposite:
        energy_diff = abs(scores["high_energy"] - scores["low_energy"])
        if energy_diff >= 2:
            if scores["high_energy"] > scores["low_energy"]:
                reason = "에너지 높음 → 차분한 음악으로"
                opposite = ["ambient", "chillout", "downtempo", "relaxing", "meditation"]
            else:
                reason = "에너지 낮음 → 강한 음악으로"
                opposite = ["rock", "energetic", "powerful", "intense", "metal"]
    
    # 8️⃣ 문화/장르 특화
    if not opposite:
        culture_scores = {
            "kpop": scores["kpop"],
            "jpop": scores["jpop"],
            "latin": scores["latin"],
            "hiphop": scores["hiphop"]
        }
        max_culture = max(culture_scores.items(), key=lambda x: x[1])
        if max_culture[1] >= 1:
            if max_culture[0] == "kpop":
                reason = "K-POP → 서양 인디/얼터너티브로"
                opposite = ["indie", "alternative", "rock", "folk", "singer-songwriter"]
            elif max_culture[0] == "jpop":
                reason = "J-POP → 서양 팝/댄스로"
                opposite = ["pop", "dance", "edm", "house", "western"]
            elif max_culture[0] == "latin":
                reason = "라틴 → 북유럽/차분한 음악으로"
                opposite = ["nordic", "calm", "folk", "acoustic", "mellow"]
            elif max_culture[0] == "hiphop":
                reason = "힙합 → 어쿠스틱/클래식으로"
                opposite = ["acoustic", "classical", "folk", "piano", "strings"]
    
    # 9️⃣ 악기 축
    if not opposite:
        sound_diff = abs(scores["vocal"] - scores["instrumental"])
        if sound_diff >= 2:
            if scores["vocal"] > scores["instrumental"]:
                reason = "보컬 중심 → 악기 중심으로"
                opposite = ["instrumental", "beats", "orchestral", "electronic", "ambient"]
            else:
                reason = "악기 중심 → 보컬 중심으로"
                opposite = ["vocal", "singing", "pop", "ballad", "choir"]
    
    # 🔟 기본 전략
    if not opposite:
        reason = "분위기 혼재 → 기본 반전 전략"
        if "pop" in s or "hip-hop" in s or "hip hop" in s or "rap" in s:
            opposite = ["dance", "edm", "house", "party", "energetic"]
        else:
            opposite = ["sad", "melancholy", "acoustic", "piano", "ballad"]
    
    print(f"   ✅ {reason}")
    print(f"   🎯 최종 반대 태그 ({len(opposite)}개): {', '.join(opposite[:8])}")
    
    return opposite


# ====== Deezer ======
async def deezer_search(artist: str, track: str) -> Optional[Dict]:
    q = f'artist:"{artist}" track:"{track}"'
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.deezer.com/search", params={"q": q})
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            d = data[0]
            
            # 매칭 정확도 체크 (옵션)
            matched_artist = d.get("artist", {}).get("name", "")
            matched_track = d.get("title", "")
            
            return {
                "name": matched_track,
                "artists": [matched_artist],
                "preview_url": d.get("preview"),
                "external_url": d.get("link"),
                "album": {
                    "name": d.get("album", {}).get("title"),
                    "image": f'https://e-cdns-images.dzcdn.net/images/cover/{d.get("album", {}).get("md5_image")}/250x250-000000-80-0-0.jpg' if d.get("album") else None
                }
            }
    except Exception as e:
        return None


# ====== 랜덤 결정 ======
def rng_from(*vals) -> random.Random:
    s = "|".join(str(v) for v in vals)
    h = hashlib.sha256(s.encode()).hexdigest()
    return random.Random(int(h[:16], 16))


# ====== 추천 파이프라인 ======
async def recommend_from_lastfm(url: str, invert: bool, limit: int, variant: int, playlist_name: str = "") -> Dict:
    print(f"\n{'='*70}")
    print(f"🎵 [Last.fm 추천 시작]")
    print(f"   - 플레이리스트 URL: {url}")
    if playlist_name:
        print(f"   - 플레이리스트 이름: {playlist_name}")
    print(f"   - 추천 모드: {'반대 분위기' if invert else '유사한 곡'}")
    print(f"   - 목표 곡 수: {limit}")
    print(f"   - Variant: {variant}")
    print(f"{'='*70}\n")
    
    rng = rng_from(url, "inv" if invert else "sim", variant)
    
    # Step 1: Spotify 플레이리스트 분석
    print(f"[Step 1] Spotify 플레이리스트 분석 중...")
    base_tracks = await get_spotify_tracks_text(url)
    print(f"   ✓ 플레이리스트에서 {len(base_tracks)}개 트랙 추출")
    
    if not base_tracks:
        print(f"   ❌ 플레이리스트가 비어있거나 접근할 수 없습니다")
        return {"tracks": []}
    
    # 처음 3곡 출력
    print(f"   📋 샘플 트랙:")
    for i, t in enumerate(base_tracks[:3], 1):
        print(f"      {i}. {t['artists'][0]} - {t['name']}")
    
    pairs = [(t["artists"][0], t["name"]) for t in base_tracks[:10] if t.get("artists")]
    rng.shuffle(pairs)
    seed_pairs = pairs[:rng.randint(3, 6)]
    
    print(f"\n   🎲 랜덤 선택된 시드 곡 ({len(seed_pairs)}개):")
    for i, (artist, track) in enumerate(seed_pairs, 1):
        print(f"      {i}. {artist} - {track}")
    
    collected = []
    used_tags = []  # 사용된 태그를 저장
    
    # Step 2: Last.fm 데이터 수집
    print(f"\n[Step 2] Last.fm API 호출 중...")
    
    if seed_pairs:
        if not invert:
            # 유사 추천 모드
            print(f"   📡 유사 트랙 검색 (Similar Tracks API)")
            
            # 시드 곡들의 태그도 수집 (표시용)
            print(f"   🏷️  시드 곡 태그 수집 중...")
            seed_tags = []
            for a, n in seed_pairs[:3]:  # 처음 3곡만 태그 수집
                track_tags = await lf_track_tags(a, n)
                if track_tags:
                    seed_tags += track_tags[:5]  # 각 곡당 최대 5개 태그
            
            if seed_tags:
                # 빈도수 높은 태그 추출
                tag_counter = Counter(seed_tags)
                top_tags = [tag for tag, _ in tag_counter.most_common(5)]
                used_tags = top_tags
                print(f"   ✓ 추출된 주요 태그: {', '.join(top_tags)}")
            
            success_count = 0
            fail_count = 0
            
            for idx, (a, n) in enumerate(seed_pairs, 1):
                print(f"   [{idx}/{len(seed_pairs)}] 검색 중: {a} - {n}")
                sim = await lf_similar_tracks(a, n, limit=50)
                
                if sim:
                    selected = rng.randint(10, 20)
                    rng.shuffle(sim)
                    collected += sim[:selected]
                    success_count += 1
                    print(f"      ✓ {len(sim)}개 발견 → {selected}개 선택")
                else:
                    fail_count += 1
                    print(f"      ❌ 유사 트랙 없음 (Last.fm 데이터 부족)")
            
            print(f"\n   📊 유사 트랙 검색 결과:")
            print(f"      성공: {success_count}/{len(seed_pairs)}")
            print(f"      실패: {fail_count}/{len(seed_pairs)}")
            print(f"      총 수집: {len(collected)}개")
            
            # 수집된 곡이 너무 적으면 보완
            if len(collected) < 10:
                print(f"\n   ⚠️  수집된 곡이 부족함 ({len(collected)}개)")
                print(f"   💡 대안: 인기 태그로 보완")
                
                supplement_tags = ["k-pop", "korean", "pop", "indie", "ballad"]
                rng.shuffle(supplement_tags)
                
                for idx, tg in enumerate(supplement_tags[:3], 1):
                    print(f"   [보완 {idx}/3] '{tg}' 태그로 검색 중...")
                    top = await lf_top_by_tag(tg, limit=40)
                    
                    if top:
                        selected = rng.randint(15, 25)
                        rng.shuffle(top)
                        collected += top[:selected]
                        print(f"      ✓ {len(top)}개 발견 → {selected}개 선택")
                        
                        if len(collected) >= 30:
                            print(f"   ✓ 충분한 후보 확보 ({len(collected)}개)")
                            break
            
        else:
            # 반대 추천 모드
            print(f"   🏷️  태그 기반 반대 분위기 검색")
            tags = []
            
            # 🆕 플레이리스트 이름 기반 태그 추가 (우선순위!)
            name_lower = (playlist_name or "").lower()
            print(f"   🔍 플레이리스트 이름 분석: '{playlist_name}'")
            
            inferred_tags = []
            # 계절 키워드
            if any(k in name_lower for k in ["여름", "summer", "더워", "hot", "beach", "tropical"]):
                inferred_tags.extend(["summer", "tropical", "hot", "beach", "sunny"])
                print(f"      → 여름 분위기 감지!")
            elif any(k in name_lower for k in ["겨울", "winter", "추워", "cold", "snow", "크리스마스", "christmas"]):
                inferred_tags.extend(["winter", "cold", "snow", "cozy"])
                print(f"      → 겨울 분위기 감지!")
            elif any(k in name_lower for k in ["봄", "spring", "벚꽃", "blossom"]):
                inferred_tags.extend(["spring", "fresh", "blossom"])
                print(f"      → 봄 분위기 감지!")
            elif any(k in name_lower for k in ["가을", "autumn", "fall"]):
                inferred_tags.extend(["autumn", "fall", "nostalgic"])
                print(f"      → 가을 분위기 감지!")
            
            # 시간대 키워드
            if any(k in name_lower for k in ["아침", "morning", "wake"]):
                inferred_tags.extend(["morning", "fresh", "energizing"])
                print(f"      → 아침 분위기 감지!")
            elif any(k in name_lower for k in ["밤", "night", "midnight"]):
                inferred_tags.extend(["night", "midnight", "nocturnal"])
                print(f"      → 밤 분위기 감지!")
            
            # 활동 키워드
            if any(k in name_lower for k in ["운동", "workout", "gym", "fitness"]):
                inferred_tags.extend(["workout", "energetic", "power"])
                print(f"      → 운동 분위기 감지!")
            elif any(k in name_lower for k in ["공부", "study", "집중", "focus"]):
                inferred_tags.extend(["study", "focus", "concentration"])
                print(f"      → 공부 분위기 감지!")
            elif any(k in name_lower for k in ["잠", "수면", "sleep", "lullaby"]):
                inferred_tags.extend(["sleep", "peaceful", "calm"])
                print(f"      → 수면 분위기 감지!")
            elif any(k in name_lower for k in ["파티", "party", "club"]):
                inferred_tags.extend(["party", "dance", "club"])
                print(f"      → 파티 분위기 감지!")
            
            # 감성 키워드
            if any(k in name_lower for k in ["로맨틱", "romantic", "사랑", "love"]):
                inferred_tags.extend(["romantic", "love", "sweet"])
                print(f"      → 로맨틱 분위기 감지!")
            elif any(k in name_lower for k in ["우울", "sad", "슬픈", "melancholy"]):
                inferred_tags.extend(["sad", "melancholy", "emotional"])
                print(f"      → 우울한 분위기 감지!")
            elif any(k in name_lower for k in ["신나는", "happy", "밝은", "upbeat", "cheerful"]):
                inferred_tags.extend(["happy", "upbeat", "cheerful"])
                print(f"      → 신나는 분위기 감지!")
            
            if inferred_tags:
                tags.extend(inferred_tags * 3)  # 가중치 부여 (3배)
                print(f"   ✅ 플레이리스트 이름 기반 태그 추가: {', '.join(set(inferred_tags))}")
            
            success_count = 0
            fail_count = 0
            
            for idx, (a, n) in enumerate(seed_pairs, 1):
                print(f"   [{idx}/{len(seed_pairs)}] 태그 분석 중: {a} - {n}")
                track_tags = await lf_track_tags(a, n)
                
                if track_tags:
                    tags += track_tags
                    success_count += 1
                    print(f"      ✓ 태그 발견: {', '.join(track_tags[:5])}")
                else:
                    fail_count += 1
                    print(f"      ❌ 태그 없음")
            
            print(f"\n   📊 태그 분석 결과:")
            print(f"      성공: {success_count}/{len(seed_pairs)}")
            print(f"      실패: {fail_count}/{len(seed_pairs)}")
            print(f"      총 태그: {len(tags)}개")
            
            if tags:
                opp = invert_tagset(tags)
                print(f"   🔄 반대 태그 생성: {', '.join(opp[:10])}")
                rng.shuffle(opp)
                
                selected_tags = opp[:rng.randint(3, 5)]
                used_tags = selected_tags.copy()  # 사용된 태그 저장
                print(f"   🎯 선택된 태그 ({len(selected_tags)}개): {', '.join(selected_tags)}")
                
                for idx, tg in enumerate(selected_tags, 1):
                    print(f"   [{idx}/{len(selected_tags)}] '{tg}' 태그로 검색 중...")
                    top = await lf_top_by_tag(tg, limit=50)
                    
                    if top:
                        selected = rng.randint(10, 20)
                        rng.shuffle(top)
                        collected += top[:selected]
                        print(f"      ✓ {len(top)}개 발견 → {selected}개 선택")
                    else:
                        print(f"      ❌ 트랙 없음")
            else:
                # 태그를 찾지 못한 경우 - 플레이리스트 이름/설명으로 추론
                print(f"   ⚠️  태그를 찾지 못함 → 플레이리스트 정보로 분위기 추론 시도")
                
                # 플레이리스트 이름에서 키워드 추출하여 반대 분위기 결정
                name_to_check = (playlist_name or url or "").lower()
                
                # 에너지 높은 음악의 반대 -> 차분한 음악
                high_energy_keywords = ["신나는", "랩", "힙합", "edm", "party", "club", "dance", "workout", "gym", "rock", "metal", "에너지", "빠른"]
                # 차분한 음악의 반대 -> 에너지 있는 음악  
                calm_keywords = ["차분", "잔잔", "수면", "sleep", "relaxing", "calm", "study", "chill", "lofi"]
                # 슬픈 음악의 반대 -> 밝은 음악
                sad_keywords = ["슬픈", "sad", "melancholy", "breakup", "이별"]
                
                is_high_energy = any(kw in name_to_check for kw in high_energy_keywords)
                is_calm = any(kw in name_to_check for kw in calm_keywords)
                is_sad = any(kw in name_to_check for kw in sad_keywords)
                
                if is_high_energy:
                    # 신나는 음악의 반대 -> 차분하고 감성적인 음악
                    print(f"   💡 추론: 에너지 높은 음악 → 반대로 차분한 음악 추천")
                    alternative_tags = ["acoustic", "piano", "ballad", "jazz", "classical", "ambient", "singer-songwriter", "indie folk"]
                elif is_calm:
                    # 차분한 음악의 반대 -> 신나는 음악
                    print(f"   💡 추론: 차분한 음악 → 반대로 에너지 있는 음악 추천")
                    alternative_tags = ["dance", "electronic", "pop", "upbeat", "energetic", "party", "house", "edm"]
                elif is_sad:
                    # 슬픈 음악의 반대 -> 밝고 긍정적인 음악
                    print(f"   💡 추론: 슬픈 음악 → 반대로 밝은 음악 추천")
                    alternative_tags = ["happy", "upbeat", "summer", "feel good", "cheerful", "pop", "funk", "disco"]
                else:
                    # 기본 대체: 다양한 차분한 태그
                    print(f"   💡 기본 대체: 다양한 감성 음악 추천")
                    alternative_tags = ["sad", "melancholy", "acoustic", "piano", "ballad", "emotional", "indie folk", "singer-songwriter"]
                
                rng.shuffle(alternative_tags)
                selected_tags = alternative_tags[:rng.randint(4, 6)]
                used_tags = selected_tags.copy()  # 사용된 태그 저장
                print(f"   🎯 대체 태그 ({len(selected_tags)}개): {', '.join(selected_tags)}")
                
                for idx, tg in enumerate(selected_tags, 1):
                    print(f"   [{idx}/{len(selected_tags)}] '{tg}' 태그로 검색 중...")
                    top = await lf_top_by_tag(tg, limit=60)
                    
                    if top:
                        selected = rng.randint(12, 20)
                        rng.shuffle(top)
                        collected += top[:selected]
                        print(f"      ✓ {len(top)}개 발견 → {selected}개 선택")
                    else:
                        print(f"      ❌ 트랙 없음")
    else:
        print(f"   ⚠️  시드 곡 없음 - 기본 태그로 검색")
        base_tags = ["pop", "rock", "indie", "k-pop", "dance", "chill", "house", "hip-hop", "ambient", "metal"]
        rng.shuffle(base_tags)
        tags_src = ["ambient", "sad", "lofi"] if invert else base_tags
        selected_tags = tags_src[:rng.randint(3, 5)]
        used_tags = selected_tags.copy()  # 사용된 태그 저장
        
        for tg in selected_tags:
            print(f"   검색 중: '{tg}' 태그")
            top = await lf_top_by_tag(tg, limit=60)
            if top:
                selected = rng.randint(12, 24)
                rng.shuffle(top)
                collected += top[:selected]
                print(f"      ✓ {len(top)}개 발견 → {selected}개 선택")

    print(f"\n   📦 Last.fm 수집 완료: 총 {len(collected)}개 후보")
    
    # Step 3: Deezer 매칭
    print(f"\n[Step 3] Deezer 음원 매칭 중...")
    seen, out = set(), []
    rng.shuffle(collected)
    
    match_success = 0
    match_fail = 0
    
    for idx, it in enumerate(collected, 1):
        if len(out) >= limit:
            print(f"   ✓ 목표 달성 ({limit}개)")
            break
            
        key = (it["artist"].lower(), it["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        
        if idx <= 5 or idx % 10 == 0:
            print(f"   [{idx}/{min(len(collected), limit*2)}] 매칭 시도: {it['artist']} - {it['name']}")
        
        dz = await deezer_search(it["artist"], it["name"])
        if dz:
            out.append(dz)
            match_success += 1
            if idx <= 5:
                print(f"      ✓ Deezer 매칭 성공")
        else:
            match_fail += 1
            if idx <= 5:
                print(f"      ❌ Deezer에서 찾을 수 없음")
    
    print(f"\n   📊 Deezer 매칭 결과:")
    print(f"      매칭 성공: {match_success}개")
    print(f"      매칭 실패: {match_fail}개")
    print(f"      최종 결과: {len(out)}개")
    
    print(f"\n{'='*70}")
    print(f"✅ [추천 완료] {len(out)}개 트랙 반환")
    if used_tags:
        print(f"   🏷️  사용된 태그: {', '.join(used_tags)}")
    print(f"{'='*70}\n")
    
    return {"tracks": out, "used_tags": used_tags}


# ====== API ======
class RecommendRequest(BaseModel):
    playlist_name: str  # 플레이리스트 이름으로 검색
    invert: bool = False
    limit: int = Field(default=24, ge=1, le=100)
    variant: int = 0


@router.get("/health")
def health():
    return {"ok": True, "lastfm": bool(LASTFM_API_KEY)}


@router.post("/recommend")
async def recommend(req: RecommendRequest, u = Depends(current_user), db = Depends(get_db)):
    if not LASTFM_API_KEY:
        raise HTTPException(500, "LASTFM_API_KEY 미설정")
    
    # 로그인 필요
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    
    try:
        from app.services.spotify import playlist_search, playlist_tracks
        from app.services import user as user_service
        import random
        
        access_token = u.access_token
        
        # 1. 플레이리스트 이름으로 검색 (토큰 갱신 로직 포함)
        print(f"\n[Last.fm 추천] 플레이리스트 검색: '{req.playlist_name}'")
        
        try:
            search_results = playlist_search(access_token, req.playlist_name, market="KR", limit=8)
        except Exception as e:
            error_str = str(e)
            # 401 에러이고 refresh_token이 있으면 갱신 시도
            if "401" in error_str and u.refresh_token:
                print(f"[lastfm_router] Token expired, attempting refresh...")
                try:
                    new_token_data = user_service.refresh_access_token(u.refresh_token)
                    access_token = new_token_data.get("access_token")
                    
                    if not access_token:
                        raise HTTPException(401, "토큰 갱신 실패. 다시 로그인해주세요.")
                    
                    # DB 업데이트
                    u.access_token = access_token
                    if new_token_data.get("refresh_token"):
                        u.refresh_token = new_token_data["refresh_token"]
                    db.add(u)
                    db.commit()
                    
                    print(f"[lastfm_router] Token refreshed successfully, retrying search...")
                    
                    # 갱신된 토큰으로 재시도
                    search_results = playlist_search(access_token, req.playlist_name, market="KR", limit=8)
                    
                except Exception as refresh_error:
                    print(f"[lastfm_router] Refresh failed: {refresh_error}")
                    raise HTTPException(401, "토큰이 만료되었습니다. 로그아웃 후 다시 로그인해주세요.")
            else:
                raise
        
        if not search_results:
            raise HTTPException(404, f"'{req.playlist_name}' 플레이리스트를 찾을 수 없습니다")
        
        # 검색 결과 출력
        print(f"[Last.fm 추천] 검색 결과: {len(search_results)}개 플레이리스트 발견")
        for idx, pl in enumerate(search_results[:5], 1):
            print(f"   {idx}. {pl.get('name', 'Unknown')} (트랙: {pl.get('tracks', {}).get('total', '?')}개)")
        
        # variant 값을 시드로 사용하여 랜덤하게 선택 (같은 variant면 같은 결과)
        # variant가 증가할 때마다 다른 플레이리스트 선택
        rng = random.Random(f"{req.playlist_name}_{req.variant}")
        selected_playlist = rng.choice(search_results[:min(5, len(search_results))])
        
        playlist_id = selected_playlist.get("id")
        playlist_name_found = selected_playlist.get("name", "Unknown")
        playlist_track_count = selected_playlist.get("tracks", {}).get("total", "?")
        
        print(f"[Last.fm 추천] ✨ 선택된 플레이리스트: {playlist_name_found} (트랙: {playlist_track_count}개)")
        
        # 2. 플레이리스트의 Spotify URL 구성
        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        
        # 3. 기존 Last.fm 추천 로직 사용 (플레이리스트 이름 전달)
        data = await recommend_from_lastfm(playlist_url, req.invert, req.limit, req.variant, playlist_name_found)
        
        if not data["tracks"]:
            raise HTTPException(502, "후보를 찾지 못했습니다.")
        
        # 플레이리스트 정보 추가
        data["source_playlist"] = {
            "id": playlist_id,
            "name": playlist_name_found,
            "url": playlist_url
        }
        
        return data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Last.fm 추천] 오류: {e}")
        raise HTTPException(500, f"Internal error: {e!r}")

# 플레이리스트 저장 요청 모델
class SaveLastfmPlaylistRequest(BaseModel):
    track_names: List[Dict[str, str]]  # [{"name": "song", "artist": "artist"}]
    playlist_name: str
    description: str = ""


@router.post("/recommend/save")
async def save_lastfm_playlist(
    request: SaveLastfmPlaylistRequest,
    u = Depends(current_user),
    db = Depends(get_db)
):
    """
    Last.fm 추천곡을 Spotify 플레이리스트로 저장
    Deezer의 곡 이름/아티스트로 Spotify에서 검색 후 저장
    """
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    
    if not request.track_names:
        raise HTTPException(400, "트랙 정보가 필요합니다")
    
    access_token = u.access_token
    
    print(f"\n[Last.fm 플레이리스트 저장 시작]")
    print(f"  - 플레이리스트명: {request.playlist_name}")
    print(f"  - 트랙 수: {len(request.track_names)}개")
    print(f"  - 설명: {request.description}")
    
    try:
        from app.services.spotify import track_search, create_playlist, add_tracks_to_playlist
        
        # 1단계: Spotify에서 각 곡 검색
        print(f"\n  [1단계] Spotify에서 트랙 검색 중...")
        spotify_track_ids = []
        not_found = []
        
        for idx, track_info in enumerate(request.track_names, 1):
            track_name = track_info.get("name", "")
            artist_name = track_info.get("artist", "")
            
            if not track_name or not artist_name:
                continue
            
            # Spotify 검색 쿼리 구성
            query = f"{track_name} {artist_name}"
            
            try:
                # Spotify에서 검색
                found_ids = track_search(access_token, query, market="KR", limit=1)
                
                if found_ids:
                    spotify_track_ids.append(found_ids[0])
                    if idx <= 5:
                        print(f"    ✓ [{idx}] {track_name} - {artist_name}")
                else:
                    not_found.append(f"{track_name} - {artist_name}")
                    if idx <= 5:
                        print(f"    ✗ [{idx}] {track_name} - {artist_name} (Spotify에서 찾을 수 없음)")
                        
            except Exception as e:
                print(f"    ✗ [{idx}] 검색 오류: {e}")
                not_found.append(f"{track_name} - {artist_name}")
        
        print(f"\n  📊 검색 결과:")
        print(f"    - 찾은 곡: {len(spotify_track_ids)}개")
        print(f"    - 못 찾은 곡: {len(not_found)}개")
        
        if not spotify_track_ids:
            raise HTTPException(404, "Spotify에서 해당 곡들을 찾을 수 없습니다")
        
        # 2단계: 플레이리스트 생성
        print(f"\n  [2단계] Spotify 플레이리스트 생성 중...")
        playlist_id = create_playlist(
            access_token,
            u.spotify_id,
            request.playlist_name,
            request.description,
            public=False
        )
        
        # 3단계: 트랙 추가
        print(f"\n  [3단계] 트랙 추가 중...")
        add_tracks_to_playlist(access_token, playlist_id, spotify_track_ids)
        
        print(f"  ✓ 플레이리스트 저장 완료: {playlist_id}\n")
        
        return {
            "success": True,
            "playlist_id": playlist_id,
            "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
            "tracks_added": len(spotify_track_ids),
            "tracks_not_found": len(not_found),
            "message": f"플레이리스트가 생성되었습니다! ({len(spotify_track_ids)}곡 추가)"
        }
        
    except HTTPException:
        raise
    except RuntimeError as e:
        error_str = str(e)
        
        # 401 에러이고 refresh_token이 있으면 갱신 시도
        if "401" in error_str and u.refresh_token:
            print(f"[lastfm_router] Token error during playlist creation, attempting refresh...")
            
            try:
                from app.services import user
                new_token_data = user.refresh_access_token(u.refresh_token)
                new_access_token = new_token_data.get("access_token")
                
                if not new_access_token:
                    raise HTTPException(401, "토큰 갱신 실패. 다시 로그인해주세요.")
                
                # DB 업데이트
                u.access_token = new_access_token
                if new_token_data.get("refresh_token"):
                    u.refresh_token = new_token_data["refresh_token"]
                db.add(u)
                db.commit()
                
                print(f"[lastfm_router] Token refreshed, retrying playlist creation...")
                
                # 갱신된 토큰으로 재시도
                from app.services.spotify import track_search, create_playlist, add_tracks_to_playlist
                
                spotify_track_ids = []
                for track_info in request.track_names:
                    track_name = track_info.get("name", "")
                    artist_name = track_info.get("artist", "")
                    if not track_name or not artist_name:
                        continue
                    query = f"{track_name} {artist_name}"
                    try:
                        found_ids = track_search(new_access_token, query, market="KR", limit=1)
                        if found_ids:
                            spotify_track_ids.append(found_ids[0])
                    except:
                        pass
                
                if not spotify_track_ids:
                    raise HTTPException(404, "Spotify에서 해당 곡들을 찾을 수 없습니다")
                
                playlist_id = create_playlist(
                    new_access_token,
                    u.spotify_id,
                    request.playlist_name,
                    request.description,
                    public=False
                )
                
                add_tracks_to_playlist(new_access_token, playlist_id, spotify_track_ids)
                
                return {
                    "success": True,
                    "playlist_id": playlist_id,
                    "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
                    "tracks_added": len(spotify_track_ids),
                    "message": "플레이리스트가 생성되었습니다!"
                }
                
            except Exception as refresh_error:
                print(f"[lastfm_router] Playlist creation failed: {refresh_error}")
                raise HTTPException(
                    401,
                    "토큰이 만료되었습니다. 다시 로그인해주세요."
                )
        else:
            raise HTTPException(500, f"플레이리스트 생성 실패: {error_str}")
    
    except Exception as e:
        print(f"[lastfm_router] Unexpected error: {e}")
        raise HTTPException(500, f"플레이리스트 생성 중 오류 발생: {str(e)}")