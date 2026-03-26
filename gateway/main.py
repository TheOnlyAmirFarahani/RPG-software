import os
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AUTH_URL   = os.getenv("AUTH_SERVICE_URL",   "http://localhost:8081")
PVE_URL    = os.getenv("PVE_SERVICE_URL",    "http://localhost:8082")
BATTLE_URL = os.getenv("BATTLE_SERVICE_URL", "http://localhost:8083")
PVP_URL    = os.getenv("PVP_SERVICE_URL",    "http://localhost:8084")

PUBLIC_PREFIXES = ("/auth/register", "/auth/login", "/scores/hall-of-fame", "/pvp/league")

def target(path: str) -> str:
    if path.startswith(("/auth", "/profile", "/scores", "/internal")): return AUTH_URL
    if path.startswith("/pve"):    return PVE_URL
    if path.startswith("/battle"): return BATTLE_URL
    if path.startswith("/pvp"):    return PVP_URL
    return AUTH_URL

@app.api_route("/{path:path}", methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
async def proxy(path: str, request: Request):
    full_path = "/" + path
    qs = request.url.query

    # Session validation
    user_id, username = None, None
    is_public = any(full_path.startswith(p) for p in PUBLIC_PREFIXES)
    if not is_public:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Session "):
            return JSONResponse({"error": "missing Authorization: Session <token>"}, status_code=401)
        session_id = auth[8:].strip()
        try:
            r = httpx.get(f"{AUTH_URL}/internal/session/{session_id}", timeout=5)
            if r.status_code != 200:
                return JSONResponse({"error": "invalid or expired session"}, status_code=401)
            user_id  = r.json().get("userId")
            username = r.json().get("username")
        except Exception:
            return JSONResponse({"error": "session validation failed"}, status_code=401)

    # Forward request
    url = target(full_path) + full_path + (f"?{qs}" if qs else "")
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "authorization", "content-length")}
    if user_id:   headers["x-user-id"]  = user_id
    if username:  headers["x-username"] = username
    if body and "content-type" not in headers:
        headers["content-type"] = "application/json"

    try:
        resp = httpx.request(
            method=request.method, url=url, content=body,
            headers=headers, timeout=10, follow_redirects=True
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.ConnectError:
        return JSONResponse({"error": "service unavailable"}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=503)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
