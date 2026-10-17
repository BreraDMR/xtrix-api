from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send


class TrailingSlashMiddleware:
    """Serve "/clients" and "/clients/" alike, without a redirect.

    Starlette's redirect_slashes answers a 307 to an absolute URL of the API host. Behind a TLS
    proxy that URL comes out as http://, and for a browser on another origin the redirect drops
    the Authorization header - so a POST without the slash just failed. Instead, when the path
    matches no route as written but does with the slash added (or removed), we route it that way.
    """

    def __init__(self, app: ASGIApp, router) -> None:
        self.app = app
        self.router = router

    def _matches(self, scope: Scope, path: str) -> bool:
        probe = {**scope, "path": path}
        return any(route.matches(probe)[0] != Match.NONE for route in self.router.routes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope["path"]
            if path != "/" and not self._matches(scope, path):
                other = path.rstrip("/") if path.endswith("/") else path + "/"
                if self._matches(scope, other):
                    scope = {**scope, "path": other, "raw_path": other.encode()}
        await self.app(scope, receive, send)
