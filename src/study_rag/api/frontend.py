"""Serve the packaged GUI without obscuring REST, AG-UI, or Swagger routes."""

import asyncio
import pathlib
import typing

import fastapi
import fastapi.responses
import fastapi.staticfiles


frontend_directory: typing.Final = pathlib.Path(__file__).resolve().parent.parent / "web"
missing_frontend_page: typing.Final = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Учебные материалы — сборка интерфейса</title>
<style>body{font:17px/1.6 system-ui,sans-serif;background:#f7f6f2;color:#25304b;margin:0;padding:8vw}
main{max-width:680px;margin:auto}pre{padding:20px;background:#e9e8f1;border-radius:12px;overflow:auto}
a{color:#5145bc}h1{line-height:1.2}</style></head>
<body><main><h1>Интерфейс ещё не собран</h1>
<p>Откройте терминал в каталоге проекта, выполните команды и обновите страницу:</p>
<pre>npm --prefix frontend ci
npm --prefix frontend run build</pre>
<p>API доступен в <a href="/docs">Swagger</a>.</p></main></body></html>"""


def mount_frontend_routes(*, application: fastapi.FastAPI) -> None:
    application.add_api_route("/", show_frontend, methods=["GET"], include_in_schema=False)
    application.add_api_route("/favicon.svg", show_favicon, methods=["GET"], include_in_schema=False)
    application.mount(
        "/assets",
        fastapi.staticfiles.StaticFiles(directory=frontend_directory / "assets", check_dir=False),
        name="frontend-assets",
    )


async def show_frontend() -> fastapi.responses.Response:
    index_filename: typing.Final = frontend_directory / "index.html"
    index_available: typing.Final = await asyncio.to_thread(index_filename.is_file)
    if not index_available:
        return fastapi.responses.HTMLResponse(missing_frontend_page, headers={"Cache-Control": "no-store"})
    return fastapi.responses.FileResponse(
        index_filename,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def show_favicon() -> fastapi.responses.Response:
    favicon_filename: typing.Final = frontend_directory / "favicon.svg"
    favicon_available: typing.Final = await asyncio.to_thread(favicon_filename.is_file)
    if not favicon_available:
        return fastapi.responses.Response(status_code=404)
    return fastapi.responses.FileResponse(favicon_filename, media_type="image/svg+xml")
