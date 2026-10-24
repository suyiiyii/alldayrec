"""Which tenant a request or a WebSocket is: its token (Bearer or the cookie)."""
from fastapi.responses import RedirectResponse, Response
from starlette.requests import HTTPConnection

import core
import tenant


class Tenants:
    """Every request and WebSocket runs as the tenant its token is (tenant.current), Bearer or the cookie; none: 401
    (a WebSocket closed with 4401). Open any page once with ?token=<its token>: a year-long cookie replaces the browser's
    basic-auth prompt that Android Chrome forgets on every reload. A process of one tenant without a token: no auth
    (loopback only)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope["path"] == "/api/health":  # lifespan; health: no token
            return await self.app(scope, receive, send)
        c = HTTPConnection(scope)
        q = c.query_params.get("token")
        if scope["type"] == "http" and q and (t := tenant.by_token(core.TENANTS, q)) and t.token:
            u = c.url.remove_query_params("token")
            resp = RedirectResponse(u.path + (f"?{u.query}" if u.query else ""), status_code=303)  # relative: the proxy owns the scheme
            resp.set_cookie("token", t.token, max_age=365 * 86400, httponly=True, samesite="lax")
            return await resp(scope, receive, send)
        t = tenant.by_token(core.TENANTS, c.headers.get("authorization", "")[7:]) or tenant.by_token(core.TENANTS, c.cookies.get("token"))
        if t is None:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            return await Response("token required: open /?token=<your token> once", 401)(scope, receive, send)
        tenant.enter(t)  # this request's task and what it starts
        await self.app(scope, receive, send)
