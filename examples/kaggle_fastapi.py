from __future__ import annotations

import logging
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from webtunnel import Cloudflared

# Настраиваем простое логирование для примера.
# В реальном проекте формат и уровни обычно задаются централизованно.
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="WebTunnel Kaggle Example")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    """Простейший маршрут проверки доступности приложения."""
    return {"message": "Hello World"}


@app.post("/test")
async def test() -> dict[str, str]:
    """Простейший POST-маршрут для демонстрации."""
    return {"status": "ok"}


def run_server() -> None:
    """
    Запускает локальный Uvicorn-сервер.

    В Kaggle и обычных ноутбуках такой сервер удобно поднимать
    в отдельном daemon-потоке.
    """
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    tunnel = Cloudflared()
    tunnel.install()
    session = tunnel.start(port=8000)

    print(f"Public URL: {session.url}")