from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from app.routers import user_router, weather_router
from app.routers import lastfm_router, podcast_router

app = FastAPI()
templates = Jinja2Templates(directory="templates")

from app.core.database import Base, engine
Base.metadata.create_all(bind=engine)

@app.get("/", response_class=HTMLResponse)
def main_page(request: Request):
    return templates.TemplateResponse("spotify.html", {"request": request})

@app.get("/weather", response_class=HTMLResponse)
def weather_page(request: Request):
    return templates.TemplateResponse("weather.html", {"request": request})


app.include_router(user_router.router)
app.include_router(weather_router.router)
app.include_router(lastfm_router.router)
app.include_router(podcast_router.router)
