import os
import requests
from datetime import datetime, timedelta
from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv
from app.routers.user_router import current_user
from app.models.user import User
from app.core.database import get_db

load_dotenv()

router = APIRouter(prefix="/podcast", tags=["podcast"])

SP_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SP_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

# 필터링 기준
MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 90
MAX_RECENCY_DAYS = 365


def get_spotify_client():
    """Spotify 클라이언트 생성"""
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SP_CLIENT_ID,
            client_secret=SP_CLIENT_SECRET
        ))
        return sp
    except Exception as e:
        raise HTTPException(500, f"Spotify 인증 실패: {e}")


async def get_similar_artists_from_lastfm(artist_name: str, limit: int = 7) -> List[Dict]:
    """Last.fm에서 유사 아티스트 조회"""
    if not LASTFM_API_KEY:
        raise HTTPException(500, "LASTFM_API_KEY 미설정")
    
    print(f"\n{'='*80}")
    print(f"🎶 1단계: Last.fm에서 '{artist_name}' 유사 아티스트 조회")
    print(f"{'='*80}")
    
    try:
        LASTFM_URL = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getsimilar",
            "artist": artist_name,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": limit
        }
        response = requests.get(LASTFM_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        artists = data.get("similarartists", {}).get("artist", [])
        related_artists = [{"name": artist["name"]} for artist in artists]
        
        print(f"✅ {artist_name}의 유사 아티스트 {len(related_artists)}명 조회 성공")
        for idx, artist in enumerate(related_artists, 1):
            print(f"   {idx}. {artist['name']}")
        
        return related_artists
        
    except Exception as e:
        print(f"❌ Last.fm API 조회 실패: {e}")
        raise HTTPException(502, f"Last.fm API 오류: {e}")


async def search_podcasts_by_artists(sp, artists: List[Dict]) -> List[Dict]:
    """아티스트별 팟캐스트 에피소드 검색"""
    print(f"\n{'='*80}")
    print(f"🎙️ 2단계: {len(artists)}명 아티스트의 '팟캐스트 에피소드' 검색")
    print(f"{'='*80}")
    
    all_episodes = []
    processed_episode_ids = set()
    
    for artist in artists:
        artist_name = artist["name"]
        print(f"  • {artist_name} 검색 중...")
        
        try:
            results = sp.search(
                q=f"{artist_name} interview",
                type="episode",
                limit=10
            )
            
            episodes = results.get("episodes", {}).get("items", [])
            
            for ep in episodes:
                if ep['id'] not in processed_episode_ids:
                    all_episodes.append(ep)
                    processed_episode_ids.add(ep['id'])
                    
        except Exception as e:
            print(f"    └─ ❌ {artist_name} 검색 실패: {e}")
    
    print(f"\n✅ 총 {len(all_episodes)}개의 고유한 에피소드 수집 완료")
    return all_episodes


def filter_episodes(episodes: List[Dict]) -> List[Dict]:
    """에피소드 필터링 (길이 & 업로드 날짜)"""
    print(f"\n{'='*80}")
    print(f"🔍 3단계: {len(episodes)}개 에피소드 필터링")
    print(f"  (조건: {MIN_DURATION_MINUTES}~{MAX_DURATION_MINUTES}분 / 최근 {MAX_RECENCY_DAYS}일 이내)")
    print(f"{'='*80}")
    
    filtered_episodes = []
    today = datetime.now()
    recency_limit_date = today - timedelta(days=MAX_RECENCY_DAYS)
    
    for ep in episodes:
        try:
            # 1. 길이 필터링
            duration_ms = ep.get("duration_ms", 0)
            duration_min = duration_ms / 60000
            
            if not (MIN_DURATION_MINUTES <= duration_min <= MAX_DURATION_MINUTES):
                continue
            
            # 2. 날짜 필터링
            release_date_str = ep.get("release_date", "1900-01-01")
            if ep.get("release_date_precision") != "day":
                continue
                
            release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
            
            if release_date < recency_limit_date:
                continue
                
            filtered_episodes.append(ep)
            
        except Exception as e:
            print(f"    └─ ⚠️ 에피소드 '{ep.get('name', 'Unknown')}' 파싱 중 오류: {e}")
    
    print(f"\n✅ 총 {len(filtered_episodes)}개의 에피소드가 필터를 통과했습니다.")
    return filtered_episodes


def format_episodes(episodes: List[Dict], limit: int = 5) -> List[Dict]:
    """에피소드 정렬 및 포맷팅"""
    print(f"\n{'='*80}")
    print("🎧 4단계: 최종 추천 플레이리스트 (최신순 정렬)")
    print(f"{'='*80}")
    
    # 최신순 정렬
    sorted_episodes = sorted(
        episodes,
        key=lambda ep: ep["release_date"],
        reverse=True
    )
    
    final_playlist = sorted_episodes[:limit]
    
    result = []
    for idx, ep in enumerate(final_playlist, 1):
        duration_min = ep['duration_ms'] / 60000
        show_name = ep.get('show', {}).get('name', 'Unknown Show')
        show_publisher = ep.get('show', {}).get('publisher', 'Unknown Publisher')
        
        # 이미지 선택 (에피소드 이미지 or 쇼 이미지)
        images = ep.get('images', [])
        show_images = ep.get('show', {}).get('images', [])
        
        image_url = None
        if images:
            image_url = images[0]['url']
        elif show_images:
            image_url = show_images[0]['url']
        
        formatted = {
            "rank": idx,
            "id": ep['id'],
            "name": ep['name'],
            "show_name": show_name,
            "publisher": show_publisher,
            "release_date": ep['release_date'],
            "duration_minutes": round(duration_min),
            "description": ep.get('description', ''),
            "url": ep.get('external_urls', {}).get('spotify', ''),
            "image": image_url
        }
        
        result.append(formatted)
        
        print(f"\n👑 추천 #{idx}")
        print(f"  • 에피소드: {ep['name']}")
        print(f"  • 팟캐스트: {show_name}")
        print(f"  • 날짜: {ep['release_date']} (길이: {duration_min:.0f}분)")
    
    print(f"\n{'='*80}")
    return result


class PodcastRequest(BaseModel):
    artist_name: str = Field(..., description="검색할 아티스트 이름")
    limit: int = Field(default=5, ge=1, le=10, description="추천 개수")


@router.post("/recommend")
async def recommend_podcasts(
    req: PodcastRequest,
    u: User | None = Depends(current_user),
    db = Depends(get_db)
):
    """
    아티스트 이름으로 관련 팟캐스트 에피소드 추천
    
    1. Last.fm에서 유사 아티스트 찾기
    2. Spotify에서 각 아티스트의 인터뷰 에피소드 검색
    3. 길이와 날짜 필터링
    4. 최신순으로 정렬하여 반환
    """
    
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    
    try:
        # 1. Last.fm에서 유사 아티스트 조회
        related_artists = await get_similar_artists_from_lastfm(req.artist_name, limit=7)
        
        if not related_artists:
            raise HTTPException(404, f"'{req.artist_name}'의 유사 아티스트를 찾을 수 없습니다")
        
        # 2. Spotify 클라이언트 생성 및 에피소드 검색
        sp = get_spotify_client()
        all_episodes = await search_podcasts_by_artists(sp, related_artists)
        
        if not all_episodes:
            raise HTTPException(404, "팟캐스트 에피소드를 찾을 수 없습니다")
        
        # 3. 필터링
        filtered_episodes = filter_episodes(all_episodes)
        
        if not filtered_episodes:
            raise HTTPException(
                404,
                f"필터 조건을 만족하는 에피소드가 없습니다. "
                f"(조건: {MIN_DURATION_MINUTES}~{MAX_DURATION_MINUTES}분, 최근 {MAX_RECENCY_DAYS}일 이내)"
            )
        
        # 4. 정렬 및 포맷팅
        recommendations = format_episodes(filtered_episodes, limit=req.limit)
        
        return {
            "artist": req.artist_name,
            "related_artists": [a["name"] for a in related_artists],
            "total_episodes_found": len(all_episodes),
            "total_filtered": len(filtered_episodes),
            "recommendations": recommendations
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Podcast 추천] 오류: {e}")
        raise HTTPException(500, f"팟캐스트 추천 중 오류 발생: {e}")


@router.get("/health")
def health():
    """헬스체크"""
    return {
        "ok": True,
        "spotify": bool(SP_CLIENT_ID and SP_CLIENT_SECRET),
        "lastfm": bool(LASTFM_API_KEY)
    }