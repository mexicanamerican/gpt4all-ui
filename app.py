"""
File: lollms_web_ui.py
Author: ParisNeo
Description: Singleton class for the LoLLMS web UI.

This file is the entry point to the webui.
"""

import os
import sys
# verify pipmaster
import verify_pipmaster
import pipmaster as pm
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

pm.ensure_packages({
    "ascii_colors":">=0.10.0",
    "safe_store":">=2.6.0",
    "freedom_search": ">=0.2.2",
    "scrapemaster": ">=0.4.2",
    "lollms_client": ">=0.8.0",
    "einops": "",
    "datasets": "",
    "Pillow":"",
    "PyQt5":""
})

from ascii_colors import ASCIIColors, LogLevel


import argparse
import os
import socket
import sys
import webbrowser
from pathlib import Path

import psutil
import socketio
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from lollms.main_config import LOLLMSConfig
from lollms.paths import LollmsPaths
from lollms.security import sanitize_path
from lollms.utilities import trace_exception
from pydantic import ValidationError
from socketio import ASGIApp
from starlette.responses import FileResponse

from lollms_webui import LOLLMSWebUI
import asyncio

def get_ip_addresses():
    hostname = socket.gethostname()
    ip_addresses = [socket.gethostbyname(hostname)]

    for interface_name, interface_addresses in psutil.net_if_addrs().items():
        for address in interface_addresses:
            if str(address.family) == "AddressFamily.AF_INET":
                ip_addresses.append(address.address)

    return ip_addresses


app = FastAPI(title="LoLLMS", description="This is the LoLLMS-Webui API documentation")


try:
    from lollms.security import MultipartBoundaryCheck

    # Add the MultipartBoundaryCheck middleware
    app.add_middleware(MultipartBoundaryCheck)
except:
    print("Couldn't activate MultipartBoundaryCheck")

# app.mount("/socket.io", StaticFiles(directory="path/to/socketio.js"))

if __name__ == "__main__":
    # desired_version = (3, 11)
    # if not sys.version_info >= desired_version:
    #     ASCIIColors.error(
    #         f"Your Python version is {sys.version_info.major}.{sys.version_info.minor}, but version {desired_version[0]}.{desired_version[1]} or higher is required."
    #     )
    #     sys.exit(1)
    # Parsong parameters
    parser = argparse.ArgumentParser(description="Start the chatbot FastAPI app.")

    parser.add_argument(
        "--host", type=str, default=None, help="the hostname to listen on"
    )
    parser.add_argument("--port", type=int, default=None, help="the port to listen on")
    parser.add_argument("--force-accept-remote-access", action='store_true', help="force to accept remote access")

    args = parser.parse_args()
    root_path = Path(__file__).parent
    lollms_paths = LollmsPaths.find_paths(
        force_local=True, custom_default_cfg_path="configs/config.yaml"
    )
    config = LOLLMSConfig.autoload(lollms_paths)

    if config.debug_log_file_path != "":
        ASCIIColors.log_path = config.debug_log_file_path
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    # Define the path to your custom CA bundle file
    ca_bundle_path = lollms_paths.personal_certificates / "truststore.pem"

    if ca_bundle_path.exists():
        # Set the environment variable
        os.environ["REQUESTS_CA_BUNDLE"] = str(ca_bundle_path)

    cert_file_path = lollms_paths.personal_certificates / "cert.pem"
    key_file_path = lollms_paths.personal_certificates / "key.pem"
    if os.path.exists(cert_file_path) and os.path.exists(key_file_path):
        is_https = True
    else:
        is_https = False

    # Create a Socket.IO server
    if config["host"] != "localhost":
        if config["host"] != "0.0.0.0":
            config.allowed_origins.append(
                f"https://{config['host']}:{config['port']}"
                if is_https
                else f"http://{config['host']}:{config['port']}"
            )
        else:
            config.allowed_origins += [
                (
                    f"https://{ip}:{config['port']}"
                    if is_https
                    else f"http://{ip}:{config['port']}"
                )
                for ip in get_ip_addresses()
            ]
    allowed_origins = config.allowed_origins + [
        (
            f"https://localhost:{config['port']}"
            if is_https
            else f"http://localhost:{config['port']}"
        )
    ]

    if args.force_accept_remote_access: # Used for docker
        config.force_accept_remote_access = True
        #this is turned off 
        config.turn_on_setting_update_validation = False
        config.turn_on_code_validation = False
        config.turn_on_open_file_validation = False
        config.turn_on_setting_update_validation = False
        

    # class EndpointSpecificCORSMiddleware(BaseHTTPMiddleware):
    #     async def dispatch(self, request: Request, call_next):
    #         if request.url.path == "/v1/completions":
    #             # For /v1/completions, allow all origins
    #             response = await call_next(request)
    #             response.headers["Access-Control-Allow-Origin"] = "*"
    #             response.headers["Access-Control-Allow-Methods"] = "*"
    #             response.headers["Access-Control-Allow-Headers"] = "*"
    #             return response
    #         else:
    #             # For other endpoints, use the restricted CORS policy
    #             origin = request.headers.get("origin")
    #             if origin in allowed_origins:
    #                 response = await call_next(request)
    #                 response.headers["Access-Control-Allow-Origin"] = origin
    #                 response.headers["Access-Control-Allow-Credentials"] = "true"
    #                 response.headers["Access-Control-Allow-Methods"] = "*"
    #                 response.headers["Access-Control-Allow-Headers"] = "*"
    #                 return response
    #             else:
    #                 return await call_next(request)

    # # Add the custom middleware
    # app.add_middleware(EndpointSpecificCORSMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=allowed_origins,
        ping_timeout=1200,
        ping_interval=30,
    )  # Enable CORS for selected origins
    # Patch for V20
    if config.rag_vectorizer in ["semantic", "tfidf", "ollama", "openai"]:
        config.rag_vectorizer = "st:all-MiniLM-L6-v2"
        config.save_config()


    LOLLMSWebUI.build_instance(
        config=config, lollms_paths=lollms_paths, args=args, sio=sio
    )
    lollmsElfServer: LOLLMSWebUI = LOLLMSWebUI.get_instance()
    lollmsElfServer.verbose = True

    # Import all endpoints
    from lollms.server.endpoints.lollms_files_server import \
        router as lollms_binding_files_server_router
    from lollms.server.endpoints.lollms_binding_infos import \
        router as lollms_binding_infos_router
    from lollms.server.endpoints.lollms_configuration_infos import \
        router as lollms_configuration_infos_router
    from lollms.server.endpoints.lollms_discussion import \
        router as lollms_discussion_router
    from lollms.server.endpoints.lollms_file_system import \
        router as lollms_file_system_router
    from lollms.server.endpoints.lollms_generator import \
        router as lollms_generator_router
    from lollms.server.endpoints.lollms_hardware_infos import \
        router as lollms_hardware_infos_router
    from lollms.server.endpoints.lollms_infos import \
        router as lollms_infos_router
    from lollms.server.endpoints.lollms_models_infos import \
        router as lollms_models_infos_router
    from lollms.server.endpoints.lollms_personalities_infos import \
        router as lollms_personalities_infos_router
    from lollms.server.endpoints.lollms_rag import router as lollms_rag_router
    from lollms.server.endpoints.lollms_skills_library import \
        router as lollms_skills_library_router
    from lollms.server.endpoints.lollms_tti import router as lollms_tti_router
    
    from lollms.server.endpoints.lollms_stt import \
        router as lollms_stt_add_router
    from lollms.server.endpoints.lollms_tts import \
        router as lollms_tts_add_router
    from lollms.server.endpoints.lollms_ttm import \
        router as lollms_ttm_add_router
    from lollms.server.endpoints.lollms_ttv import \
        router as lollms_ttv_router
    
    from lollms.server.endpoints.lollms_user import \
        router as lollms_user_router
    from lollms.server.events.lollms_files_events import \
        add_events as lollms_files_events_add
    from lollms.server.events.lollms_generation_events import \
        add_events as lollms_generation_events_add
    from lollms.server.events.lollms_model_events import \
        add_events as lollms_model_events_add
    from lollms.server.events.lollms_personality_events import \
        add_events as lollms_personality_events_add
    
    from lollms.server.endpoints.lollms_function_calls import router as lollms_function_calls
    from lollms.server.endpoints.lollms_thinking import router as lollms_thinking
    

    from lollms.server.endpoints.chat_bar import router as chat_bar_router
    from lollms.server.endpoints.lollms_advanced import router as lollms_advanced_router
    from lollms.server.endpoints.lollms_apps import router as lollms_apps_router
    from lollms.server.endpoints.lollms_help import router as help_router
    from lollms.server.endpoints.lollms_message import router as lollms_message_router
    from lollms.server.endpoints.lollms_playground import router as lollms_playground_router
    from lollms.server.endpoints.lollms_webui_infos import \
        router as lollms_webui_infos_router
    from events.lollms_chatbox_events import \
        add_events as lollms_chatbox_events_add
    from events.lollms_discussion_events import \
        add_events as lollms_webui_discussion_events_add
    # from lollms.server.events.lollms_rag_events import add_events as lollms_rag_events_add
    from events.lollms_generation_events import \
        add_events as lollms_webui_generation_events_add
    from events.lollms_interactive_events import \
        add_events as lollms_interactive_events_add

    # endpoints for remote access
    app.include_router(lollms_generator_router)

    # Endpoints reserved for local access
    if (
        not config.headless_server_mode
    ) or config.force_accept_remote_access:  # Be aware that forcing force_accept_remote_access can expose the server to attacks
        app.include_router(lollms_infos_router)
        app.include_router(lollms_binding_files_server_router)
        app.include_router(lollms_hardware_infos_router)
        app.include_router(lollms_binding_infos_router)
        app.include_router(lollms_models_infos_router)
        app.include_router(lollms_personalities_infos_router)
        app.include_router(lollms_skills_library_router)
        app.include_router(lollms_tti_router)

        app.include_router(lollms_webui_infos_router)
        app.include_router(lollms_discussion_router)
        app.include_router(lollms_message_router)
        app.include_router(lollms_user_router)
        app.include_router(lollms_advanced_router)
        app.include_router(lollms_apps_router)

        app.include_router(chat_bar_router)
        app.include_router(help_router)

        app.include_router(lollms_stt_add_router)
        app.include_router(lollms_tts_add_router)
        app.include_router(lollms_ttm_add_router)
        app.include_router(lollms_ttv_router)
        
        app.include_router(lollms_function_calls)
        app.include_router(lollms_thinking)

        app.include_router(lollms_rag_router)

        app.include_router(lollms_file_system_router)

        app.include_router(lollms_playground_router)
        app.include_router(lollms_configuration_infos_router)

    @sio.event
    async def disconnect(sid):
        ASCIIColors.yellow(f"Disconnected: {sid}")

    @sio.event
    async def message(sid, data):
        ASCIIColors.yellow(f"Message from {sid}: {data}")
        await sio.send(sid, "Message received!")

    lollms_generation_events_add(sio)

    if (
        not config.headless_server_mode
    ) or config.force_accept_remote_access:  # Be aware that forcing force_accept_remote_access can expose the server to attacks
        lollms_personality_events_add(sio)
        lollms_files_events_add(sio)
        lollms_model_events_add(sio)
        # lollms_rag_events_add(sio)

        lollms_webui_generation_events_add(sio)
        lollms_webui_discussion_events_add(sio)
        lollms_chatbox_events_add(sio)
        lollms_interactive_events_add(sio)

    app.mount(
        "/extensions",
        StaticFiles(directory=Path(__file__).parent / "web" / "dist", html=True),
        name="extensions",
    )
    app.mount(
        "/playground",
        StaticFiles(directory=Path(__file__).parent / "web" / "dist", html=True),
        name="playground",
    )
    app.mount(
        "/settings",
        StaticFiles(directory=Path(__file__).parent / "web" / "dist", html=True),
        name="settings",
    )

    # Custom route to serve JavaScript files with the correct MIME type
    @app.get("/{path:path}")
    async def serve_js(path: str):
        sanitize_path(path)
        if path == "":
            return FileResponse(
                Path(__file__).parent / "web" / "dist" / "index.html",
                media_type="text/html",
            )
        file_path = Path(__file__).parent / "web" / "dist" / path
        if file_path.suffix == ".js":
            return FileResponse(file_path, media_type="application/javascript")
        return FileResponse(file_path)

    app.mount(
        "/",
        StaticFiles(directory=Path(__file__).parent / "web" / "dist", html=True),
        name="static",
    )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError):
        print(f"Error: {exc.errors()}")  # Print the validation error details
        if hasattr(exc, "body"):
            return JSONResponse(
                status_code=422,
                content=jsonable_encoder(
                    {"detail": exc.errors(), "body": await exc.body}
                ),  # Send the error details and the original request body
            )
        else:
            return JSONResponse(
                status_code=422,
                content=jsonable_encoder(
                    {"detail": exc.errors(), "body": ""}
                ),  # Send the error details and the original request body
            )

    @app.on_event("startup")
    async def startup_event():
        """
        This function will be called when the application starts.
        It's the perfect place to get the running event loop and pass it to our class.
        """
        loop = asyncio.get_running_loop()
        lollmsElfServer.set_loop(loop)


    app = ASGIApp(socketio_server=sio, other_asgi_app=app)

    lollmsElfServer.app = app
    try:
        sio.reboot = False
        # if config.enable_lollms_service:
        #     ASCIIColors.yellow("Starting Lollms service")
        #     #uvicorn.run(app, host=config.host, port=6523)
        #     def run_lollms_server():
        #         parts = config.lollms_base_url.split(":")
        #         host = ":".join(parts[0:2])
        #         port = int(parts[2])
        #         uvicorn.run(app, host=host, port=port)
        # New thread
        #     thread = threading.Thread(target=run_lollms_server)

        # start thread
        #   thread.start()

        # if autoshow
        if config.debug:
            ASCIIColors.set_log_level(LogLevel.DEBUG)


        if config.auto_show_browser and not config.headless_server_mode:
            if config["host"] == "0.0.0.0":
                webbrowser.open(
                    f"https://localhost:{config['port']}"
                    if is_https
                    else f"http://localhost:{config['port']}"
                )
                # webbrowser.open(f"http://localhost:{6523}") # needed for debug (to be removed in production)
            else:
                webbrowser.open(
                    f"https://{config['host']}:{config['port']}"
                    if is_https
                    else f"http://{config['host']}:{config['port']}"
                )
                # webbrowser.open(f"http://{config['host']}:{6523}") # needed for debug (to be removed in production)

        if is_https:
            uvicorn.run(
                app,
                host=config.host,
                port=config.port,
                ssl_certfile=cert_file_path,
                ssl_keyfile=key_file_path,
            )
        else:
            uvicorn.run(app, host=config.host, port=config.port)

    except Exception as ex:
        trace_exception(ex)
