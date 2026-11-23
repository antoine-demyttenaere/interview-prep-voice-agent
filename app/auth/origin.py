"""Origin check: refuses requests that change something when they come from another website's page.

app/main.py adds it as a middleware (code that runs around every request). It only looks at
requests that can change data: POST, PUT, PATCH and DELETE.

Why it's needed: the login cookie is sent automatically by the browser. If you are logged in and
open a malicious page, that page could try to make your browser send, say, DELETE /documents/...
to this server, with your cookie: a "cross-site request forgery" (CSRF). SameSite=Lax on the
cookie stops other *sites*, but browsers count every port of the same host as the same site, so
any other app running on localhost (or a sibling subdomain later) would still get through.

How it decides: browsers add an `Origin` header (scheme, host and port of the page that sent the
request) to every POST / PUT / PATCH / DELETE, and a page can't fake it. If it is present and its
host:port isn't this server's own (the `Host` header), the answer is 403 and the route never runs.
Requests without an Origin are let through: they don't come from a web page (the terminal CLI,
curl, the tests), and they carry no browser cookie unless their author put one there on purpose.
The scheme (http / https) is not compared, because behind a load balancer the server sees http
while the browser used https.
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


async def check_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method in UNSAFE and origin is not None:
        if urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
            return JSONResponse(status_code=403, content={"detail": "Request refused: it came from another website."})
    return await call_next(request)
