# test.py
import requests

TOKEN = "your_token"
API = "https://api.spotify.com/v1"

def h(tok): 
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}

# 1단계: 먼저 사용 가능한 장르 확인
def check_genres(tok):
    r = requests.get(
        f"{API}/recommendations/available-genre-seeds",
        headers=h(tok),
        timeout=10
    )
    print(f"장르 조회 상태: {r.status_code}")
    if r.ok:
        genres = r.json()["genres"]
        print(f"사용 가능한 장르 수: {len(genres)}")
        print(f"앞 10개: {genres[:10]}")
    else:
        print(f"에러: {r.text}")
    return r.ok

# 2단계: 최소한의 파라미터로 테스트
def simple_test(tok):
    params = {
        "seed_genres": "pop",  # 쉼표 없이 1개만
        "limit": 10,
        "market": "KR"
    }
    
    print(f"\n요청 URL: {API}/recommendations")
    print(f"파라미터: {params}")
    
    r = requests.get(
        f"{API}/recommendations",
        headers=h(tok),
        params=params,
        timeout=10
    )
    
    print(f"응답 상태: {r.status_code}")
    print(f"응답: {r.text[:200]}")
    
    if r.ok:
        data = r.json()
        print(f"\n✅ 성공! 추천곡 {len(data['tracks'])}개")
        for t in data["tracks"][:3]:
            print(f"  - {t['name']} by {t['artists'][0]['name']}")
    else:
        print(f"\n❌ 실패")

if __name__ == "__main__":
    print("=== 1단계: 장르 목록 확인 ===")
    if check_genres(TOKEN):
        print("\n=== 2단계: 간단한 추천 테스트 ===")
        simple_test(TOKEN)