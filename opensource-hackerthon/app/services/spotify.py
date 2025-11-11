import requests
from typing import List, Dict, Tuple
import random
from collections import Counter

API = "https://api.spotify.com/v1"

def _h(tok:str):
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/json"
    }

def me_recent(tok:str, limit:int=50) -> List[str]:
    r = requests.get(f"{API}/me/player/recently-played", headers=_h(tok), params={"limit":limit}, timeout=10)
    if r.status_code == 204: return []
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized")
    r.raise_for_status()
    return [i["track"]["id"] for i in r.json().get("items",[]) if i.get("track") and i["track"].get("id")]

def me_top(tok:str, time_range:str="short_term", limit:int=50) -> List[str]:
    r = requests.get(f"{API}/me/top/tracks", headers=_h(tok), params={"time_range":time_range,"limit":limit}, timeout=10)
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized")
    r.raise_for_status()
    return [t["id"] for t in r.json().get("items",[]) if t.get("id")]

def get_spotify_recommendations(tok:str, seed_tracks:List[str], market:str="KR", limit:int=50) -> List[str]:
    if not seed_tracks:
        return []
    seeds = seed_tracks[:5]
    try:
        url = f"{API}/recommendations"
        params = {"seed_tracks": ",".join(seeds), "limit": limit, "market": market}
        r = requests.get(url, headers=_h(tok), params=params, timeout=10)
        if r.status_code == 401:
            raise RuntimeError("401 Unauthorized")
        r.raise_for_status()
        tracks = r.json().get("tracks", [])
        return [t["id"] for t in tracks if t and t.get("id")]
    except Exception as e:
        print(f"[spotify] Spotify recommendations 실패: {e}")
        return []

def get_related_artists(tok:str, artist_id:str) -> List[str]:
    try:
        r = requests.get(f"{API}/artists/{artist_id}/related-artists", headers=_h(tok), timeout=10)
        if r.ok:
            artists = r.json().get("artists", [])
            return [a["id"] for a in artists[:5] if a and a.get("id")]
    except Exception as e:
        print(f"[spotify] 유사 아티스트 조회 실패: {e}")
    return []

def get_artist_top_tracks(tok:str, artist_id:str, market:str="KR") -> List[str]:
    try:
        r = requests.get(f"{API}/artists/{artist_id}/top-tracks", headers=_h(tok), params={"market": market}, timeout=10)
        if r.ok:
            tracks = r.json().get("tracks", [])
            return [t["id"] for t in tracks if t and t.get("id")]
    except Exception as e:
        print(f"[spotify] 아티스트 인기곡 조회 실패: {e}")
    return []

def get_artist_ids_from_tracks(tok:str, track_ids:List[str]) -> List[str]:
    if not track_ids:
        return []
    artist_ids = []
    for i in range(0, len(track_ids), 50):
        chunk = track_ids[i:i+50]
        try:
            r = requests.get(f"{API}/tracks", headers=_h(tok), params={"ids": ",".join(chunk)}, timeout=10)
            if r.ok:
                tracks = r.json().get("tracks", [])
                for t in tracks:
                    if t and t.get("artists"):
                        for artist in t["artists"]:
                            if artist and artist.get("id"):
                                artist_ids.append(artist["id"])
        except Exception as e:
            print(f"[spotify] 아티스트 ID 추출 실패: {e}")
    return list(dict.fromkeys(artist_ids))

def playlist_search(tok:str, q:str, market:str="KR", limit:int=8) -> List[Dict]:
    r = requests.get(f"{API}/search", headers=_h(tok),
                     params={"q":q,"type":"playlist","market":market,"limit":limit}, timeout=10)
    r.raise_for_status()
    items = (r.json().get("playlists") or {}).get("items") or []
    return [it for it in items if it]

def playlist_tracks(tok:str, pid:str, limit:int=100) -> List[str]:
    ids=[]; url=f"{API}/playlists/{pid}/tracks"; params={"limit":limit}
    while url:
        r = requests.get(url, headers=_h(tok), params=params, timeout=10)
        if r.status_code in (401,403):
            break
        r.raise_for_status()
        j=r.json()
        for x in j.get("items", []) or []:
            tr = x.get("track")
            if not tr: 
                continue
            tid = tr.get("id")
            if tid:
                ids.append(tid)
        url = j.get("next"); params=None
        if len(ids)>=600: break
    return list(dict.fromkeys(ids))

def track_search(tok:str, q:str, market:str="KR", limit:int=50) -> List[str]:
    r = requests.get(f"{API}/search", headers=_h(tok),
                     params={"q":q, "type":"track", "market":market, "limit":limit}, timeout=10)
    r.raise_for_status()
    items = (r.json().get("tracks") or {}).get("items") or []
    return [t["id"] for t in items if t and t.get("id")]

def get_track_info(tok: str, track_ids: List[str], market: str = "KR") -> List[Dict]:
    """
    트랙 정보를 조회합니다. market 파라미터를 사용하여 한국어 제목을 가져옵니다.
    """
    if not track_ids:
        return []
    all_tracks = []
    
    print(f"[spotify] get_track_info 시작: {len(track_ids)}개 트랙, market={market}")
    
    for start in range(0, len(track_ids), 50):
        chunk = track_ids[start:start+50]
        try:
            # market 파라미터 명시적으로 전달
            params = {"ids": ",".join(chunk), "market": market}
            r = requests.get(f"{API}/tracks", headers=_h(tok), params=params, timeout=10)
            
            if not r.ok:
                print(f"[spotify] API 오류: {r.status_code} - {r.text[:200]}")
                continue
                
            tracks = r.json().get("tracks", [])
            
            for t in tracks:
                if not t or not t.get("id"):
                    continue
                    
                track_id = t["id"]
                
                # 트랙 이름 (한국어 우선)
                track_name = t.get("name") or "Unknown Track"
                
                # 아티스트 이름들 수집
                artists = t.get("artists", [])
                artist_names_list = []
                for artist in artists:
                    if artist and artist.get("name"):
                        artist_names_list.append(artist["name"])
                
                artist_names = ", ".join(artist_names_list) if artist_names_list else "Unknown Artist"
                
                # 앨범 정보
                album = t.get("album", {}) or {}
                album_name = album.get("name", "")
                album_images = album.get("images", []) or []
                album_image_url = album_images[0].get("url", "") if album_images else ""
                
                # Spotify URL
                spotify_url = t.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/track/{track_id}"
                
                # 인기도
                popularity = t.get("popularity", 0)
                
                track_info = {
                    "id": track_id,
                    "name": track_name,
                    "artists": artist_names,
                    "album": album_name,
                    "album_image": album_image_url,
                    "url": spotify_url,
                    "popularity": popularity
                }
                
                all_tracks.append(track_info)
                
                # 디버깅: 첫 3개 트랙만 출력
                if len(all_tracks) <= 3:
                    print(f"  샘플 {len(all_tracks)}: {track_name} - {artist_names}")
                    
        except Exception as e:
            print(f"[spotify] get_track_info 에러: {e}")
            continue
    
    print(f"[spotify] get_track_info 완료: {len(all_tracks)}개 트랙 로드")
    return all_tracks

# 간단 유사도: 아티스트 겹침 + 제목 토큰 유사도 + 인기도
def _name_tokens(s:str) -> set:
    return set(x for x in (s or "").lower().replace(",", " ").split() if len(x) > 1)

def _rank_playlist_by_user_similarity(tok:str, playlist_track_ids:List[str], user_track_ids:List[str], take:int=30, market:str="KR") -> List[Dict]:
    if not playlist_track_ids or not user_track_ids:
        return []
    
    print(f"[spotify] 유사도 랭킹 시작: 후보 {len(playlist_track_ids)}개, 사용자 기록 {len(user_track_ids)}개")
    
    cand_meta = get_track_info(tok, playlist_track_ids, market=market)
    user_meta = get_track_info(tok, user_track_ids[:50], market=market)

    user_artist_names = Counter()
    for um in user_meta:
        user_artist_names[um["artists"]] += 1

    user_title_tokens = [_name_tokens(um["name"]) for um in user_meta if um.get("name")]

    # 스코어 계산
    scored = []
    for t in cand_meta:
        popularity = (t.get("popularity") or 0) / 100.0
        artist_overlap = 1.0 if t["artists"] in user_artist_names else 0.0
        title_sim = 0.0
        tok_t = _name_tokens(t["name"])
        if tok_t and user_title_tokens:
            # 최대 Jaccard
            for utok in user_title_tokens:
                if not utok: 
                    continue
                inter = len(tok_t & utok)
                if inter == 0:
                    continue
                union = len(tok_t | utok)
                title_sim = max(title_sim, inter/union)
        score = 1.0*artist_overlap + 0.2*popularity + 0.1*title_sim
        scored.append((score, t))

    # 정렬 + 아티스트 다양성(최대 2곡)
    scored.sort(key=lambda x: x[0], reverse=True)
    picked, artist_cnt = [], Counter()
    for s, t in scored:
        a = t["artists"]
        if artist_cnt[a] >= 2:
            continue
        picked.append(t)
        artist_cnt[a] += 1
        if len(picked) >= take:
            break
    
    print(f"[spotify] 유사도 랭킹 완료: {len(picked)}개 선택")
    return picked

def create_playlist(tok: str, user_id: str, name: str, description: str = "", public: bool = False) -> str:
    url = f"{API}/users/{user_id}/playlists"
    data = {"name": name, "description": description, "public": public}
    r = requests.post(url, headers=_h(tok), json=data, timeout=10)
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized")
    r.raise_for_status()
    playlist = r.json()
    print(f"[spotify] 플레이리스트 생성 완료: {playlist['id']} - {name}")
    return playlist["id"]

def add_tracks_to_playlist(tok: str, playlist_id: str, track_ids: List[str]):
    if not track_ids:
        return
    url = f"{API}/playlists/{playlist_id}/tracks"
    for i in range(0, len(track_ids), 100):
        chunk = track_ids[i:i+100]
        uris = [f"spotify:track:{tid}" for tid in chunk]
        r = requests.post(url, headers=_h(tok), json={"uris": uris}, timeout=10)
        if r.status_code == 401:
            raise RuntimeError("401 Unauthorized")
        r.raise_for_status()
        print(f"[spotify] 플레이리스트에 {len(chunk)}개 트랙 추가 완료")

def recommend_by_weather(tok:str, keywords:List[str], market:str="KR", take:int=30,
                         seed_source:str="both") -> Tuple[List[Dict], Dict]:
    print(f"\n{'='*60}")
    print(f"[🎵 추천 시작] 날씨 키워드: {keywords}")
    print(f"[🎵 추천 시작] 마켓: {market}, 목표 곡 수: {take}")
    print(f"{'='*60}\n")

    # 사용자 시드(최근 청취 우선)
    print(f"[1단계] 사용자 청취 기록 수집 중...")
    seed_tracks = me_recent(tok, 50)
    print(f"  ✓ 최근 재생 기록: {len(seed_tracks)}개")
    if not seed_tracks:
        seed_tracks = me_top(tok, "short_term", 50)
        print(f"  ✓ Top tracks (대체): {len(seed_tracks)}개")

    # 키워드 기반 플레이리스트 검색
    print(f"\n[2단계] 날씨/무드 키워드 기반 플레이리스트 검색 중...")
    pls_kr = []
    for k in keywords:
        try:
            res = playlist_search(tok, k, market=market, limit=6)
            if res: pls_kr += res
            print(f"  ✓ '{k}' 검색: {len(res or [])}개")
        except Exception as e:
            print(f"  ✗ '{k}' 검색 실패: {e}")

    # 플레이리스트 중복 제거
    pl_dict = {p["id"]: p for p in pls_kr if p and p.get("id")}
    pids = list(pl_dict.items())[:12]
    print(f"\n  📋 총 {len(pids)}개 플레이리스트에서 트랙 수집 중...")

    # 플레이리스트 내 트랙만 후보
    playlist_candidate_ids = []
    for pid, pl_info in pids:
        try:
            name = pl_info.get("name","Unknown")
            owner = (pl_info.get("owner") or {}).get("display_name","Unknown")
            tracks = playlist_tracks(tok, pid, 50)
            playlist_candidate_ids.extend(tracks)
            print(f"  ✓ '{name}' (by {owner}): {len(tracks)}곡")
        except Exception as e:
            print(f"  ✗ 플레이리스트 수집 실패: {e}")

    playlist_candidate_ids = list(dict.fromkeys(playlist_candidate_ids))
    if not playlist_candidate_ids:
        print("  ⚠️ 플레이리스트 기반 후보가 없습니다.")
        return [], {"error":"playlist_empty"}

    # 최근 들은 곡 제외
    user_recent_set = set(seed_tracks)
    playlist_candidate_ids = [tid for tid in playlist_candidate_ids if tid not in user_recent_set]
    print(f"\n  📊 플레이리스트 후보(최근 제외): {len(playlist_candidate_ids)}개")

    if not playlist_candidate_ids:
        return [], {"error":"no_candidates_after_filter"}

    # 플레이리스트 내부에서 '사용자와 유사한' 곡 순위화
    print(f"\n[3단계] 플레이리스트 내부 유사도 랭킹...")
    if len(playlist_candidate_ids) > 500:
        playlist_candidate_ids = random.sample(playlist_candidate_ids, 500)
    
    # market 파라미터 명시적으로 전달
    ranked = _rank_playlist_by_user_similarity(tok, playlist_candidate_ids, seed_tracks, take=take, market=market)

    if not ranked:
        return [], {"error":"ranking_failed"}

    print(f"\n  ✓ 최종 선택: {len(ranked)}개")
    print(f"{'='*60}\n")

    return ranked, {
        "seeds_used": len(seed_tracks),
        "total_candidates": len(playlist_candidate_ids),
        "playlists_searched": len(pids),
        "method": "playlist_only_user_similarity",
        "diversity": len(set(t['artists'] for t in ranked))
    }