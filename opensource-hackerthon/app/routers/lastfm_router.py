import os
import re
import time
import base64
import random
import hashlib
from typing import List, Dict, Optional
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, HttpUrl
from collections import Counter
from dotenv import load_dotenv

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
# 긍정적/밝은 분위기
BRIGHT_HAPPY = {"happy", "upbeat", "cheerful", "fun", "party", "summer", "energetic", "positive", "uplifting", "feel good", "joyful", "sunny"}
# 부정적/어두운 분위기  
DARK_SAD = {"sad", "melancholy", "depressing", "dark", "gloomy", "somber", "emotional", "tearjerker", "heartbreak", "lonely", "moody", "melancholic"}

# 에너지 높음
HIGH_ENERGY = {"rock", "metal", "punk", "hardcore", "aggressive", "intense", "heavy", "hard rock", "energetic", "powerful"}
# 에너지 낮음
LOW_ENERGY = {"ambient", "chillout", "downtempo", "sleep", "meditation", "peaceful", "tranquil", "slow", "calm", "relaxing"}

# 신나는 음악
DANCEABLE = {"dance", "edm", "house", "techno", "electro", "club", "disco", "electronic dance", "party", "upbeat"}
# 차분한 음악
CALM = {"acoustic", "piano", "classical", "jazz", "folk", "ballad", "soft", "gentle", "mellow", "chill"}

# 팝/메인스트림
MAINSTREAM = {"pop", "top 40", "chart", "radio", "mainstream", "commercial"}
# 실험적/언더그라운드
ALTERNATIVE = {"indie", "alternative", "experimental", "underground", "art rock", "avant-garde"}

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
    """태그를 분석해서 주된 분위기의 반대만 생성 (다수결 방식)"""
    s = set(t.lower() for t in tags)
    
    print(f"   🔍 태그 분석 (총 {len(s)}개): {', '.join(list(s)[:15])}")
    
    # 각 카테고리별 점수 계산
    bright_score = len(s & BRIGHT_HAPPY)
    dark_score = len(s & DARK_SAD)
    
    dance_score = len(s & DANCEABLE)
    calm_score = len(s & CALM)
    
    high_energy_score = len(s & HIGH_ENERGY)
    low_energy_score = len(s & LOW_ENERGY)
    
    # 🆕 장르를 보고 분위기 추론
    genre_hints = {"calm": 0, "energetic": 0, "dark": 0, "bright": 0}
    for tag in s:
        if tag in GENRE_TO_MOOD:
            mood = GENRE_TO_MOOD[tag]
            genre_hints[mood] += 1
    
    if any(genre_hints.values()):
        print(f"   💡 장르 기반 분위기 추론: {dict((k,v) for k,v in genre_hints.items() if v > 0)}")
        # 장르 힌트를 점수에 반영
        calm_score += genre_hints["calm"]
        dance_score += genre_hints["energetic"]
        dark_score += genre_hints["dark"]
        bright_score += genre_hints["bright"]
    
    print(f"   📊 분위기 점수:")
    print(f"      밝음: {bright_score} vs 어두움: {dark_score}")
    print(f"      신남: {dance_score} vs 차분: {calm_score}")
    print(f"      강함: {high_energy_score} vs 약함: {low_energy_score}")
    
    opposite = []
    
    # 1순위: 감정 (밝음 vs 어두움) - 차이가 2개 이상일 때만 반영
    emotion_diff = abs(bright_score - dark_score)
    if emotion_diff >= 2:
        if bright_score > dark_score:
            print(f"   ✅ 주요 분위기: 밝고 행복함 → 어두운 음악으로 반전")
            opposite = ["sad", "melancholy", "dark", "emotional", "depressing", "somber", "gloomy"]
        else:
            print(f"   ✅ 주요 분위기: 어둡고 우울함 → 밝은 음악으로 반전")
            opposite = ["happy", "upbeat", "cheerful", "positive", "uplifting", "feel good", "joyful"]
    
    # 2순위: 활동성 (신남 vs 차분) - 감정이 중립이면
    elif emotion_diff < 2:
        activity_diff = abs(dance_score - calm_score)
        if activity_diff >= 2:
            if dance_score > calm_score:
                print(f"   ✅ 주요 분위기: 신나고 활동적 → 차분한 음악으로 반전")
                opposite = ["acoustic", "piano", "ballad", "soft", "calm", "peaceful", "relaxing"]
            else:
                print(f"   ✅ 주요 분위기: 차분하고 조용함 → 신나는 음악으로 반전")
                opposite = ["dance", "party", "energetic", "upbeat", "club", "edm", "house", "electro"]
        
        # 3순위: 에너지 레벨
        else:
            energy_diff = abs(high_energy_score - low_energy_score)
            if energy_diff >= 2:
                if high_energy_score > low_energy_score:
                    print(f"   ✅ 주요 분위기: 에너지 높음 → 차분한 음악으로 반전")
                    opposite = ["ambient", "chillout", "downtempo", "relaxing", "meditation"]
                else:
                    print(f"   ✅ 주요 분위기: 에너지 낮음 → 강한 음악으로 반전")
                    opposite = ["rock", "energetic", "powerful", "intense"]
    
    # 분위기가 정말 애매하면
    if not opposite:
        print(f"   ⚠️  분위기가 혼재됨 (명확한 경향 없음)")
        
        # 🆕 pop이나 hip-hop 같은 중립 장르면 신나는 음악으로
        if "pop" in s or "hip-hop" in s or "hip hop" in s or "rap" in s:
            print(f"   💡 팝/힙합 감지 → 신나는 댄스 음악으로 반전")
            opposite = ["dance", "edm", "house", "party", "energetic", "club", "upbeat", "electro"]
        else:
            print(f"   💡 기본 전략: 차분하고 감성적인 음악 선택")
            opposite = ["sad", "melancholy", "acoustic", "piano", "ballad", "emotional"]
    
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
async def recommend_from_lastfm(url: str, invert: bool, limit: int, variant: int) -> Dict:
    print(f"\n{'='*70}")
    print(f"🎵 [Last.fm 추천 시작]")
    print(f"   - 플레이리스트 URL: {url}")
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
    
    # Step 2: Last.fm 데이터 수집
    print(f"\n[Step 2] Last.fm API 호출 중...")
    
    if seed_pairs:
        if not invert:
            # 유사 추천 모드
            print(f"   📡 유사 트랙 검색 (Similar Tracks API)")
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
                print(f"   ⚠️  태그를 찾지 못함 → 마이너/언더그라운드 음악으로 추정")
                print(f"   💡 대안: 차분하고 감성적인 태그 사용 (반대 분위기)")
                
                # 시끄럽지 않고 감성적인 반대 태그
                alternative_tags = ["sad", "melancholy", "acoustic", "piano", "ballad", "emotional", "indie folk", "singer-songwriter"]
                rng.shuffle(alternative_tags)
                selected_tags = alternative_tags[:rng.randint(4, 6)]
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
        
        for tg in tags_src[:rng.randint(3, 5)]:
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
    print(f"{'='*70}\n")
    
    return {"tracks": out}


# ====== API ======
class RecommendRequest(BaseModel):
    playlist_url: HttpUrl
    invert: bool = False
    limit: int = Field(default=24, ge=1, le=100)
    variant: int = 0


@router.get("/health")
def health():
    return {"ok": True, "lastfm": bool(LASTFM_API_KEY)}


@router.post("/recommend")
async def recommend(req: RecommendRequest):
    if not LASTFM_API_KEY:
        raise HTTPException(500, "LASTFM_API_KEY 미설정")
    try:
        data = await recommend_from_lastfm(str(req.playlist_url), req.invert, req.limit, req.variant)
        if not data["tracks"]:
            raise HTTPException(502, "후보를 찾지 못했습니다.")
        return data
    except Exception as e:
        raise HTTPException(500, f"Internal error: {e!r}")