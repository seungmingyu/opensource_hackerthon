from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import List
from app.services.weather import get_current_weather, resolve_mood, DEFAULT_LAT, DEFAULT_LON
from app.services.spotify import recommend_by_weather, create_playlist, add_tracks_to_playlist
from app.routers.user_router import current_user
from app.models.user import User
from app.services import user
from app.core.database import get_db

router = APIRouter(prefix="/recommend", tags=["recommend"])

# rule을 한국어로 변환하는 맵
RULE_KR = {
    "dawn_cool": "선선한 새벽",
    "dawn_cold": "추운 새벽",
    "dawn_warm": "따뜻한 새벽",
    "morning_rain": "비 오는 아침",
    "morning_cloudy": "흐린 아침",
    "morning_hot": "더운 아침",
    "morning_clear": "맑은 아침",
    "afternoon_storm": "소나기",
    "afternoon_rain": "오후 비",
    "afternoon_snow": "눈 오는 오후",
    "afternoon_humid_cloudy": "답답한 흐림",
    "afternoon_cloudy": "흐린 오후",
    "afternoon_very_hot": "폭염",
    "afternoon_hot": "더운 오후",
    "afternoon_windy": "바람 부는 오후",
    "afternoon_perfect": "완벽한 오후",
    "afternoon_cool": "선선한 오후",
    "afternoon_cold": "추운 오후",
    "evening_rain": "비 오는 저녁",
    "evening_cloudy": "흐린 저녁",
    "evening_warm": "따뜻한 저녁",
    "evening_breezy": "바람 부는 저녁",
    "evening_perfect": "완벽한 저녁",
    "evening_cold": "추운 저녁"
}


@router.get("/weather")
def recommend_weather(
    take:int=30, market:str="KR",
    lat:float=DEFAULT_LAT, lon:float=DEFAULT_LON,
    u: User | None = Depends(current_user),
    db = Depends(get_db)
):
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")

    access_token = u.access_token

    # 날씨 정보는 먼저 가져오기
    print(f"\n{'='*60}")
    print(f"[날씨 API] 위치: lat={lat}, lon={lon}")
    w = get_current_weather(lat, lon)
    
    # 날씨 원본 데이터 출력
    print(f"[날씨 원본 데이터]")
    print(f"  - 기온(temp): {w.get('main', {}).get('temp')}°C")
    print(f"  - 체감온도(feels_like): {w.get('main', {}).get('feels_like')}°C")
    print(f"  - 날씨(weather): {w.get('weather', [{}])[0].get('main')}")
    print(f"  - 날씨 상세: {w.get('weather', [{}])[0].get('description')}")
    print(f"  - 습도(humidity): {w.get('main', {}).get('humidity')}%")
    print(f"  - 풍속(wind): {w.get('wind', {}).get('speed')}m/s")
    print(f"{'='*60}\n")
    
    mood = resolve_mood(w, datetime.now())
    
    print(f"[분위기 분석 결과]")
    print(f"  - Rule: {mood['rule']}")
    print(f"  - Keywords: {mood['keywords']}")
    print(f"  - 현재 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 첫 시도
    try:
        tracks, meta = recommend_by_weather(
            access_token, mood["keywords"],
            market=market, take=take, seed_source="recent"
        )
    except RuntimeError as e:
        error_str = str(e)
        
        # 403 또는 401 에러이고 refresh_token이 있으면 갱신 시도
        if ("401" in error_str or "403" in error_str) and u.refresh_token:
            print(f"[weather_router] Token error detected, attempting refresh...")
            
            try:
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
                
                print(f"[weather_router] Token refreshed successfully, retrying recommendation...")
                
                # 갱신된 토큰으로 재시도
                tracks, meta = recommend_by_weather(
                    new_access_token, mood["keywords"],
                    market=market, take=take, seed_source="recent"
                )
                
            except Exception as refresh_error:
                print(f"[weather_router] Refresh failed: {refresh_error}")
                raise HTTPException(
                    401, 
                    "토큰이 만료되었습니다. 로그아웃 후 다시 로그인해주세요. "
                    "(Spotify 권한이 취소되었을 수 있습니다)"
                )
        else:
            # refresh_token이 없거나 다른 에러
            raise HTTPException(
                500, 
                f"추천 생성 실패: {error_str}"
            )

    feels_like_temp = w.get("main", {}).get("feels_like")
    location_name = "소노벨 변산"  # 장소명 고정
    rule_kr = RULE_KR.get(mood["rule"], mood["rule"])  # rule을 한국어로 변환
    
    print(f"\n[최종 응답 데이터]")
    print(f"  - 장소: {location_name}")
    print(f"  - 체감온도: {feels_like_temp}°C")
    print(f"  - 트랙 수: {len(tracks)}개")
    print(f"  - Rule (한국어): {rule_kr}")
    print(f"{'='*60}\n")

    return {
        "location": {"name": location_name, "lat": lat, "lon": lon},
        "trigger": {
            "time_band": "dawn" if 0 <= datetime.now().hour < 6 else "other",
            "feels_like": feels_like_temp,
            "weather": w.get("weather", [{}])[0].get("main"),
            "rule": mood["rule"],
            "rule_kr": rule_kr  # 한국어 rule 추가
        },
        "keywords": mood["keywords"],
        "meta": meta,
        "tracks": tracks
    }


# 플레이리스트 저장 요청 모델
class SavePlaylistRequest(BaseModel):
    track_ids: List[str]
    playlist_name: str
    description: str = ""


@router.post("/weather/save")
def save_weather_playlist(
    request: SavePlaylistRequest,
    u: User | None = Depends(current_user),
    db = Depends(get_db)
):
    """
    추천받은 곡들로 Spotify 플레이리스트 생성
    """
    if not u:
        raise HTTPException(401, "로그인이 필요합니다")
    
    if not request.track_ids:
        raise HTTPException(400, "트랙 ID가 필요합니다")
    
    access_token = u.access_token
    
    print(f"\n[플레이리스트 저장 시작]")
    print(f"  - 플레이리스트명: {request.playlist_name}")
    print(f"  - 트랙 수: {len(request.track_ids)}개")
    print(f"  - 설명: {request.description}")
    
    try:
        # 플레이리스트 생성
        playlist_id = create_playlist(
            access_token,
            u.spotify_id,
            request.playlist_name,
            request.description,
            public=False
        )
        
        # 트랙 추가
        add_tracks_to_playlist(access_token, playlist_id, request.track_ids)
        
        print(f"  ✓ 플레이리스트 저장 완료: {playlist_id}\n")
        
        return {
            "success": True,
            "playlist_id": playlist_id,
            "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
            "tracks_added": len(request.track_ids),
            "message": "플레이리스트가 생성되었습니다!"
        }
        
    except RuntimeError as e:
        error_str = str(e)
        
        # 401 에러이고 refresh_token이 있으면 갱신 시도
        if "401" in error_str and u.refresh_token:
            print(f"[weather_router] Token error during playlist creation, attempting refresh...")
            
            try:
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
                
                print(f"[weather_router] Token refreshed, retrying playlist creation...")
                
                # 갱신된 토큰으로 재시도
                playlist_id = create_playlist(
                    new_access_token,
                    u.spotify_id,
                    request.playlist_name,
                    request.description,
                    public=False
                )
                
                add_tracks_to_playlist(new_access_token, playlist_id, request.track_ids)
                
                return {
                    "success": True,
                    "playlist_id": playlist_id,
                    "playlist_url": f"https://open.spotify.com/playlist/{playlist_id}",
                    "tracks_added": len(request.track_ids),
                    "message": "플레이리스트가 생성되었습니다!"
                }
                
            except Exception as refresh_error:
                print(f"[weather_router] Playlist creation failed: {refresh_error}")
                raise HTTPException(
                    401,
                    "토큰이 만료되었습니다. 다시 로그인해주세요."
                )
        else:
            raise HTTPException(500, f"플레이리스트 생성 실패: {error_str}")
    
    except Exception as e:
        print(f"[weather_router] Unexpected error: {e}")
        raise HTTPException(500, f"플레이리스트 생성 중 오류 발생: {str(e)}")