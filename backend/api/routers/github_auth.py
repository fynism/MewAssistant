"""Same-origin browser login; GitHub tokens never leave the backend."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.api.dependencies import get_db
from backend.api.schemas.auth import AuthResponse, GitHubLoginRequest
from backend.core.config import settings
from backend.core.security import create_access_token
from backend.infrastructure.database.models import User
from backend.services import github_oauth as oauth

router = APIRouter()
BROWSER_COOKIE = "supermew_github_browser"
TICKET_COOKIE = "supermew_github_ticket"
COOKIE_PATH = "/auth/github"
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def require_github():
    if not settings.github_oauth_enabled:
        raise HTTPException(status_code=503, detail="GitHub 登录暂未启用")


def require_same_origin(request: Request):
    if request.headers.get("origin") != oauth.public_origin():
        raise HTTPException(status_code=403, detail="请从本站发起 GitHub 登录")


def set_cookie(response: Response, name: str, value: str, ttl: int):
    response.set_cookie(name, value, max_age=ttl, httponly=True, samesite="lax",
                        secure=settings.github_redirect_uri.startswith("https://"), path=COOKIE_PATH)


def clear_cookies(response: Response):
    for name in (BROWSER_COOKIE, TICKET_COOKIE):
        response.delete_cookie(name, path=COOKIE_PATH, httponly=True, samesite="lax",
                               secure=settings.github_redirect_uri.startswith("https://"))


def login_response(data: dict, status_code: int = 200):
    response = JSONResponse(data, status_code=status_code, headers=NO_STORE)
    clear_cookies(response)
    return response


@router.get("/auth/providers")
def providers():
    return JSONResponse({"github": settings.github_oauth_enabled}, headers=NO_STORE)


@router.post("/auth/github/start", dependencies=[Depends(require_github)])
def start_login(payload: GitHubLoginRequest, request: Request):
    require_same_origin(request)
    try:
        url, browser = oauth.begin_login(payload.next)
    except oauth.OAuthError:
        raise HTTPException(status_code=503, detail="GitHub 登录暂时不可用，请稍后重试") from None
    response = JSONResponse({"authorization_url": url}, headers=NO_STORE)
    set_cookie(response, BROWSER_COOKIE, browser, oauth.FLOW_TTL)
    response.delete_cookie(TICKET_COOKIE, path=COOKIE_PATH)
    return response


@router.get("/auth/github/callback", dependencies=[Depends(require_github)])
async def callback(request: Request, db: Session = Depends(get_db)):
    next_path = "/"
    browser = request.cookies.get(BROWSER_COOKIE, "")
    try:
        flow = await run_in_threadpool(oauth.consume_credential, "state",
                                      request.query_params.get("state", ""), browser)
        next_path = flow["next"]
        if request.query_params.get("error"):
            raise oauth.OAuthError("cancelled")
        code = request.query_params.get("code", "")
        if not code or len(code) > 1024:
            raise oauth.OAuthError("failed")
        profile = await run_in_threadpool(oauth.fetch_github_profile, code, flow["verifier"])
        user = oauth.github_user(db, profile)
        ticket = secrets.token_urlsafe(32)
        await run_in_threadpool(oauth.store_credential, "ticket", ticket, browser,
                                {"user_id": user.id}, oauth.TICKET_TTL)
        response = RedirectResponse(oauth.frontend_result(next_path), status_code=303, headers=NO_STORE)
        set_cookie(response, TICKET_COOKIE, ticket, oauth.TICKET_TTL)
    except oauth.OAuthError as error:
        response = RedirectResponse(oauth.frontend_result(next_path, error.reason), status_code=303, headers=NO_STORE)
        clear_cookies(response)
    return response


@router.post("/auth/github/complete", response_model=AuthResponse, dependencies=[Depends(require_github)])
def complete_login(request: Request, db: Session = Depends(get_db)):
    require_same_origin(request)
    try:
        ticket = oauth.consume_credential("ticket", request.cookies.get(TICKET_COOKIE, ""),
                                          request.cookies.get(BROWSER_COOKIE, ""))
    except oauth.OAuthError as error:
        status_code = 503 if error.reason == "unavailable" else 401
        return login_response({"detail": "GitHub 登录已失效，请重新登录" if status_code == 401
                               else "GitHub 登录暂时不可用，请稍后重试"}, status_code)
    user = db.get(User, ticket["user_id"])
    if user is None:
        return login_response({"detail": "账号不存在，请重新登录"}, 401)
    data = AuthResponse(access_token=create_access_token(user.username, user.role), username=user.username, role=user.role)
    return login_response(data.model_dump())
