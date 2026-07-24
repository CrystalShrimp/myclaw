from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from config.settings import settings
from app.feishu.client import feishu_client
from app.feishu.ws import FeishuWsClient, create_ws_client
from app.feishu import events
from app.hooks.router import router as hooks_router
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "myclaw.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("myclaw")

# Build event dispatcher
event_handler = (
    EventDispatcherHandler.builder(
        encrypt_key=settings.feishu_encrypt_key,
        verification_token=settings.feishu_verification_token,
    )
    .register_p2_im_message_receive_v1(events.on_message_receive)
    .register_p2_card_action_trigger(events.on_card_action)
    .build()
)

ws_client: FeishuWsClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ws_client
    workspace = Path(settings.get_default_workspace())
    if workspace.exists() and not workspace.is_dir():
        raise NotADirectoryError(
            f"DEFAULT_WORKSPACE exists but is not a directory: {workspace}"
        )
    workspace.mkdir(parents=True, exist_ok=True)

    logger.info("myclaw starting...")
    logger.info("Default workspace: %s", workspace)
    logger.info("Allowed users: %s", settings.get_allowed_users() or "(all)")
    logger.info("Claude CLI: %s", settings.claude_cli_path)

    # Start Feishu WebSocket long-connection
    ws_client = create_ws_client(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        event_handler=event_handler,
    )
    ws_task = ws_client.start_async()
    logger.info("Feishu WS client connecting...")

    # Profiles are selected per Feishu user and persisted outside Claude sessions.
    from app.profiles import discover_profiles
    profiles = discover_profiles()
    if not profiles:
        logger.error("No model profiles found in config/settings_*.json")
    else:
        logger.info("Available model profiles: %s", list(profiles.keys()))
    yield

    await feishu_client.close()
    logger.info("myclaw stopped.")


app = FastAPI(
    title="myclaw",
    description="Feishu Bot backed by Claude Code CLI",
    version="0.2.0",
    lifespan=lifespan,
)

# Register hook callback routes
app.include_router(hooks_router)


@app.get("/health")
async def health():
    info = {"status": "ok", "ws_connected": ws_client._conn is not None if ws_client else False}
    if ws_client:
        info["ws_diagnostics"] = {
            "msg_count": ws_client._msg_count,
            "last_msg_time": ws_client._last_msg_time,
            "last_msg_type": ws_client._last_msg_type,
            "last_msg_error": ws_client._last_msg_error,
            "dispatch_count": ws_client._dispatch_count,
        }
    from app.agent.cli_loop import claude_cli_loop
    info["cli_last_error"] = claude_cli_loop._last_error or "(none)"
    return info


if __name__ == "__main__":
    import sys

    dev_mode = "--dev" in sys.argv
    if dev_mode:
        logger.info("DEV mode: hot-reload enabled")
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            reload=True,
            reload_includes=["*.py"],
            reload_excludes=["audit.log", ".env", "*.log"],
        )
    else:
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            reload=False,
        )
