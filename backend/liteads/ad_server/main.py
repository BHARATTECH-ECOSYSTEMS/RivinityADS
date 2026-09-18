"""
LiteAds Ad Server - FastAPI Application

Main entry point for the ad serving API.
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from liteads.ad_server.middleware.metrics import MetricsMiddleware, metrics_endpoint
from liteads.ad_server.routers import ad, auth, event, health, management, targeting, reference
from liteads.common.cache import redis_client
from liteads.common.config import get_settings
from liteads.common.database import close_db, create_tables, init_db
from liteads.common.exceptions import LiteAdsError
from liteads.common.logger import clear_log_context, get_logger, log_context
from liteads.common.utils import generate_request_id
from liteads.schemas.response import ErrorResponse

logger = get_logger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Startup
    logger.info(
        "Starting LiteAds server",
        version=settings.app_version,
        env=settings.env,
    )

    # Initialize database
    await init_db()
    if settings.debug:
        await create_tables()

    # Initialize Redis
    await redis_client.connect()

    logger.info("LiteAds server started successfully")

    yield

    # Shutdown
    logger.info("Shutting down LiteAds server")
    await redis_client.close()
    await close_db()
    logger.info("LiteAds server stopped")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="RivinityADS",
        description=(
            "A lightweight computational advertising system with multi-format delivery "
            "and a public API for third-party and AI-platform integrations."
        ),
        version=settings.app_version,
        # Always expose /docs so third parties can explore the API
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    import os

    cors_env = os.getenv("LITEADS_CORS_ORIGINS")
    if cors_env:
        allowed = [o.strip() for o in cors_env.split(",")]
        allow_credentials = True
    else:
        allowed = ["*"]
        allow_credentials = False

    # CORS middleware — allows the JS SDK to call from allowed origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prometheus metrics middleware
    app.add_middleware(MetricsMiddleware)

    # Prometheus metrics endpoint
    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], tags=["monitoring"])

    # Request logging middleware
    @app.middleware("http")
    async def logging_middleware(request: Request, call_next: Any) -> Any:
        """Log all requests with timing."""
        request_id = generate_request_id()
        log_context(request_id=request_id)

        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "Request completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        # Add headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

        clear_log_context()

        return response

    # Exception handlers
    @app.exception_handler(LiteAdsError)
    async def liteads_error_handler(
        request: Request,
        exc: LiteAdsError,
    ) -> JSONResponse:
        """Handle LiteAds errors."""
        logger.warning(
            "LiteAds error",
            error=exc.__class__.__name__,
            message=exc.message,
            details=exc.details,
        )

        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=exc.__class__.__name__,
                message=exc.message,
                details=exc.details,
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Handle unexpected errors."""
        logger.error(
            "Unexpected error",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="InternalServerError",
                message="An unexpected error occurred",
            ).model_dump(),
        )

    # Include routers
    app.include_router(health.router, tags=["health"])
    app.include_router(ad.router, prefix="/api/v1/ad", tags=["ad"])
    app.include_router(event.router, prefix="/api/v1/event", tags=["event"])
    app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
    app.include_router(management.router, prefix="/api/v1", tags=["management"])
    app.include_router(targeting.router, prefix="/api/v1", tags=["targeting"])
    app.include_router(reference.router, prefix="/api/v1/reference", tags=["reference"])

    # Mount JS SDK & static assets so rivinity.js and liteads.js are served at /static/
    static_dir = _STATIC_DIR if _STATIC_DIR.exists() else Path(os.path.join(os.path.dirname(__file__), "..", "static"))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Public API documentation landing page
    @app.get(
        "/public-api-docs",
        response_class=HTMLResponse,
        include_in_schema=False,
        summary="Public API surface overview",
    )
    async def public_api_docs() -> HTMLResponse:
        return HTMLResponse(_PUBLIC_DOCS_HTML)

    return app


# ── Public API docs page ─────────────────────────────────────────────────────
_PUBLIC_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>RivinityADS — Public API Reference</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f0f14;color:#e2e2f0;min-height:100vh}
    header{background:linear-gradient(135deg,#6c3fff,#2c7be5);padding:40px 32px 32px}
    header h1{font-size:2rem;font-weight:700;letter-spacing:-0.5px}
    header p{margin-top:8px;opacity:.85;font-size:1rem;max-width:640px}
    main{max-width:900px;margin:40px auto;padding:0 24px 80px}
    h2{font-size:1.25rem;font-weight:600;margin:36px 0 12px;color:#a78bfa}
    .card{background:#1a1a28;border:1px solid #2d2d44;border-radius:10px;padding:20px 24px;margin-bottom:16px}
    .card h3{font-size:1rem;font-weight:600;margin-bottom:6px;display:flex;align-items:center;gap:8px}
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
    .post{background:#1a3a2a;color:#4ade80}.get{background:#1a2a3a;color:#60a5fa}
    .del{background:#3a1a1a;color:#f87171}
    code{font-family:'Fira Code',monospace;font-size:0.82rem;background:#12121e;padding:10px 14px;border-radius:6px;display:block;margin-top:8px;overflow-x:auto;white-space:pre;border:1px solid #2d2d44}
    p.desc{font-size:0.875rem;color:#9ca3af;margin-top:4px;line-height:1.6}
    .tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:0.7rem;background:#2d2d44;color:#a78bfa;margin-left:4px}
    a{color:#818cf8;text-decoration:none}a:hover{text-decoration:underline}
    .sdk-note{background:#1e1a38;border:1px solid #4c1d95;border-radius:10px;padding:16px 20px;margin-top:8px}
    .sdk-note h3{color:#c4b5fd;margin-bottom:6px}
    footer{text-align:center;padding:24px;font-size:0.8rem;color:#555}
  </style>
</head>
<body>
<header>
  <h1>⚡ RivinityADS Public API</h1>
  <p>Multi-format ad delivery for websites, mobile apps, and AI-platform server-to-server integrations.</p>
  <p style="margin-top:12px"><a href="/docs" style="color:#fff;background:rgba(255,255,255,.15);padding:6px 14px;border-radius:6px;font-size:.875rem">OpenAPI Docs →</a>&nbsp;<a href="/redoc" style="color:#fff;background:rgba(255,255,255,.15);padding:6px 14px;border-radius:6px;font-size:.875rem">ReDoc →</a></p>
</header>
<main>

  <h2>🔑 Authentication</h2>
  <div class="card">
    <p class="desc">Obtain a publisher API key from your dashboard: <code style="display:inline;background:none;border:none;padding:0">POST /api/v1/publisher/keys</code>.</p>
    <p class="desc" style="margin-top:8px">Pass it on every request as an HTTP header:</p>
    <code>X-API-Key: rk_live_&lt;your-key&gt;</code>
    <p class="desc" style="margin-top:8px"><strong>AI-platform / S2S callers</strong> additionally sign each request with HMAC-SHA256:</p>
    <code>X-API-Key: rk_live_...
X-Timestamp: 1700000000          # Unix epoch (integer)
X-Signature: &lt;hex&gt;               # HMAC-SHA256(api_key, "{key}|{ts}|{body_sha256}")</code>
  </div>

  <h2>📡 Endpoints</h2>

  <div class="card">
    <h3><span class="badge post">POST</span> /api/v1/ad/request <span class="tag">core</span></h3>
    <p class="desc">Main ad decision endpoint. Returns ad creatives with signed tracking URLs.</p>
    <code>{
  "slot_id": "banner_home_top",
  "device": { "os": "web" },
  "num_ads": 1,
  "format": 2,            // 1=banner 2=native 3=video 4=interstitial
  "context": { "sectors": ["tech"] }
}
// → { "ads": [{ "creative": {...}, "tracking": {...} }] }</code>
    <p class="desc" style="margin-top:8px">💡 <strong>AI platforms:</strong> set <code style="display:inline;background:none;border:none;padding:0">format=2</code> (native) to receive <code style="display:inline;background:none;border:none;padding:0">title / description / landing_url</code> for rendering as chat-inline, sidebar, or conversation-starter blocks. Billing is server-to-server via the tracking URLs.</p>
  </div>

  <div class="card">
    <h3><span class="badge get">GET</span> /api/v1/ad/serve?slot_id= <span class="tag">iframe</span></h3>
    <p class="desc">Zero-integration iframe for classic web publishers. No JS or API key required.</p>
    <code>&lt;iframe
  src="https://your-domain/api/v1/ad/serve?slot_id=banner_home_top"
  width="300" height="250"
  frameborder="0" scrolling="no"
&gt;&lt;/iframe&gt;</code>
  </div>

  <div class="card">
    <h3><span class="badge post">POST</span> /api/v1/event/track <span class="tag">billing</span></h3>
    <p class="desc">Server-to-server event tracking (impression / click / conversion). The tracking URLs in the ad response are pre-built for you — fire them as GET pixel requests or POST for S2S.</p>
    <code>{
  "request_id": "req_abc123",
  "ad_id": "ad_1_2",
  "event_type": "click",
  "user_id": "u_xyz"
}</code>
  </div>

  <div class="card">
    <h3><span class="badge post">POST</span> /api/v1/classify <span class="tag">context</span></h3>
    <p class="desc">Classify free-form text into ad-relevant sectors without sending raw page content to the ad engine. Use the returned <code style="display:inline;background:none;border:none;padding:0">sectors</code> in your next ad request.</p>
    <code>// Request
{ "text": "Fed raises rates, tech stocks tumble" }

// Response
{ "sectors": ["finance", "tech"], "scores": { "finance": 1.0, "tech": 0.6 } }</code>
  </div>

  <h2>🌐 JS SDK (websites)</h2>
  <div class="sdk-note">
    <h3>Drop-in script tag</h3>
    <p class="desc">Serves banner, native, and video ads with automatic impression/click tracking.</p>
    <code>&lt;script
  src="https://your-domain/static/rivinity.js"
  data-slot="banner_home_top"
  data-api-key="rk_live_..."
  data-format="1"
&gt;&lt;/script&gt;</code>
  </div>

  <h2>🔮 Roadmap</h2>
  <div class="card">
    <p class="desc"><strong>Webhooks (coming soon):</strong> Conversion postback URL per campaign and publisher event stream — table <code style="display:inline;background:none;border:none;padding:0">webhooks(id, owner_type, owner_id, url, events, secret)</code> with a dispatcher fired from <code style="display:inline;background:none;border:none;padding:0">track_event</code>.</p>
  </div>

</main>
<footer>RivinityADS • <a href="/docs">API Docs</a> • Apache 2.0</footer>
</body>
</html>"""


# Create app instance
app = create_app()


def main() -> None:
    """Run the server using uvicorn."""
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "liteads.ad_server.main:app",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
        reload=settings.server.reload,
        log_level="info" if not settings.debug else "debug",
    )


if __name__ == "__main__":
    main()
