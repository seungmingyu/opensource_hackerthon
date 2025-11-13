# test.py
import os, sys, argparse, requests
from dotenv import load_dotenv

load_dotenv()  # 🔹 .env 파일 자동 로드

API = "https://api.openweathermap.org/data/2.5/weather"

def main():
    p = argparse.ArgumentParser(description="Check current temperature & weather")
    p.add_argument("--lat", type=float, default=35.6462)
    p.add_argument("--lon", type=float, default=126.5051)
    args = p.parse_args()

    key = os.getenv("OPENWEATHERMAP")
    if not key:
        print("ERROR: .env 안에 OPENWEATHERMAP 키가 없습니다.", file=sys.stderr)
        sys.exit(1)

    r = requests.get(
        API,
        params={
            "lat": args.lat,
            "lon": args.lon,
            "appid": key,
            "units": "metric",
            "lang": "kr"
        },
        timeout=8
    )
    r.raise_for_status()
    d = r.json()
    name = d.get("name") or f"{args.lat},{args.lon}"
    weather = d["weather"][0]["description"]
    temp = d["main"]["temp"]
    print(f"{name} | {temp:.1f}°C | {weather}")

if __name__ == "__main__":
    main()
