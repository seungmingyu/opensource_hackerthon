from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select
from app.core.database import get_db
from app.models.user import User
from app.services import user
import requests


router = APIRouter()


def _set_sid(resp: Response, spotify_id: str):
    resp.set_cookie("sid", spotify_id, httponly=True, samesite="Lax")

def current_user(req: Request, db=Depends(get_db)) -> User | None:
    sid = req.cookies.get("sid")
    if not sid: return None
    return db.execute(select(User).where(User.spotify_id==sid)).scalar_one_or_none()

@router.get("/login")
def login():
    return RedirectResponse(user.build_login_redirect())

@router.get("/callback")
def callback(code: str | None = None, state: str | None = None, error: str | None = None, db=Depends(get_db)):
    if error: return JSONResponse({"error": error}, status_code=400)
    if not state or state not in user.STATE_PKCE: return JSONResponse({"error":"bad_state"}, status_code=400)
    
    token_data = user.exchange_token(code, state) 
    me = user.get_me(token_data["access_token"])
    sp_id = me["id"]

    u = db.execute(select(User).where(User.spotify_id==sp_id)).scalar_one_or_none()
    if not u: u = User(spotify_id=sp_id)
    u.name = me.get("display_name") or sp_id
    u.access_token = token_data["access_token"]
    u.refresh_token = token_data.get("refresh_token") 
    db.add(u); db.commit()

    resp = RedirectResponse(url="/")  
    _set_sid(resp, sp_id)   
    return resp

@router.get("/current_user")
def whoami(u: User|None = Depends(current_user)):
    if not u: return {"logged_in": False}
    return {"logged_in": True, "spotify_id": u.spotify_id, "name": u.name}

@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/")
    resp.delete_cookie("sid")
    return resp


