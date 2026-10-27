"""Model Gallery – Webserver für Foto-Alben mit Userverwaltung, Permalinks und Auswahl-Workflow."""
import logging
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config, db
from .routes_admin import api as admin_api
from .routes_admin import router as admin_router
from .routes_gallery import router as gallery_router
from .routes_plugin import router as plugin_router
from .security import hash_password
from .web import BASE_DIR, Redirect, render

log = logging.getLogger("gallery")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

config.ensure_dirs()
db.init()

app = FastAPI(title="Model Gallery", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.secret_key(),
    session_cookie="gallery_session",
    max_age=config.SESSION_DAYS * 86400,
    same_site="lax",
    https_only=config.COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(gallery_router)
app.include_router(admin_router)
app.include_router(admin_api)
app.include_router(plugin_router)


@app.exception_handler(Redirect)
async def _redirect_handler(request, exc):
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request, exc):
    path = request.url.path
    if path.startswith(("/api/", "/media/")) or path.endswith("/upload"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", {"code": exc.status_code, "message": exc.detail}, status=exc.status_code)


@app.on_event("startup")
def bootstrap_admin():
    with db.get() as conn:
        if conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]:
            return
        pw = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, display_name, password_hash, role) VALUES (?,?,?, 'admin')",
            (config.ADMIN_USER, "Administrator", hash_password(pw)),
        )
        if not cur.rowcount:  # anderer Worker war schneller
            return
    if not config.ADMIN_PASSWORD:
        path = config.DATA_DIR / "initial-admin-password.txt"
        path.write_text("Benutzer: {}\nPasswort: {}\n".format(config.ADMIN_USER, pw))
        path.chmod(0o600)
        log.warning("Admin-Konto '%s' angelegt. Passwort steht in %s", config.ADMIN_USER, path)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
