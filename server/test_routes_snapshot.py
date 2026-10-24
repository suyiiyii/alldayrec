"""The app's routes in the order they are matched, each with its handler's name, and its middleware: moving handlers
between modules must leave both as they are (a route lost, added, moved ahead of another or answered by another function
shows here). A change on purpose (a new route) updates ROUTES with it."""
import server

ROUTES = [  # (methods, path, handler); app.include_router's routes in its place
    ("GET,HEAD", "/openapi.json", "openapi"),
    ("GET,HEAD", "/docs", "swagger_ui_html"),
    ("GET,HEAD", "/docs/oauth2-redirect", "swagger_ui_redirect"),
    ("GET,HEAD", "/redoc", "redoc_html"),
    ("POST", "/api/firmware", "upload"),
    ("GET", "/api/firmware", "versions"),
    ("PATCH", "/api/firmware/{model}/{version}", "edit"),
    ("GET", "/api/firmware/latest", "latest"),
    ("GET", "/api/firmware/{model}/{version}.zip", "download"),
    ("GET", "/api/addresses", "addresses"),
    ("GET", "/device/{name}", "page"),
    ("GET", "/p/{name}", "page"),
    ("GET", "/connect", "page"),
    ("GET", "/progress", "page"),
    ("GET", "/calendar", "page"),
    ("GET", "/speakers", "page"),
    ("GET", "/record", "page"),
    ("GET", "/c/{conv_id}", "page"),
    ("GET", "/conversations", "page"),
    ("GET", "/todos", "page"),
    ("GET", "/me", "page"),
    ("GET", "/live", "page"),
    ("GET", "/", "page"),
    ("GET", "/assets/{name}", "asset"),
    ("GET", "/app.apk", "app_apk"),
    ("GET", "/api/app/latest", "apk_latest"),
    ("POST", "/api/chunks", "chunk"),
    ("POST", "/api/chunks/end", "chunk_end"),
    ("POST", "/api/hold/release", "hold_release"),
    ("POST", "/api/device-status", "device_status_report"),
    ("POST", "/api/device-log", "device_log_upload"),
    ("GET", "/api/device-log", "device_log"),
    ("GET", "/api/calls", "calls"),
    ("GET", "/api/upgrades", "upgrades"),
    ("GET", "/api/client-config", "client_config"),
    ("GET", "/api/app-setup", "app_setup"),
    ("GET", "/api/events", "events"),
    ("GET", "/api/live", "live_now"),
    ("POST", "/api/live/{device}", "live_ask"),
    ("GET", "/api/health", "health"),
    ("GET", "/api/status", "status_now"),
    ("GET", "/api/lang", "lang_get"),
    ("PUT", "/api/lang", "lang_put"),
    ("GET", "/api/sentences", "sentences"),
    ("GET", "/api/conversations", "conversations"),
    ("GET", "/api/days", "days"),
    ("GET", "/api/calendar", "calendar"),
    ("GET", "/api/search", "search"),
    ("POST", "/api/ask", "ask_ai"),
    ("POST", "/api/ask/voice", "ask_voice"),
    ("POST", "/mcp", "mcp"),
    ("DELETE,GET", "/mcp", "mcp_stream"),
    ("GET", "/api/recap", "recap_get"),
    ("GET", "/api/todos/due", "todos_due"),
    ("PUT", "/api/todos/{todo_id}/mark", "todo_mark"),
    ("DELETE", "/api/todos/{todo_id}/mark", "todo_unmark"),
    ("POST", "/api/todos/marks", "todo_marks"),
    ("POST", "/api/recap/{date}", "recap_redo"),
    ("GET", "/api/tags", "tags"),
    ("GET", "/api/conversation/{conv_id}", "conversation"),
    ("POST", "/api/conversation/{conv_id}/feedback", "add_feedback"),
    ("GET", "/api/feedback", "feedback"),
    ("POST", "/api/conversation/{conv_id}/summarize", "resummarize"),
    ("GET", "/api/names", "names"),
    ("POST", "/api/conversation/{conv_id}/rename", "rename"),
    ("POST", "/api/speakers/undo", "speaker_undo"),
    ("GET", "/api/speakers", "speaker_list"),
    ("GET", "/api/speakers/{gid}", "speaker_one"),
    ("POST", "/api/speakers/name", "speaker_name"),
    ("POST", "/api/speakers/detach", "speaker_detach"),
    ("GET", "/api/conversation/{conv_id}/speakers", "conversation_speakers"),
    ("GET", "/api/aliases", "alias_list"),
    ("POST", "/api/aliases", "alias_set"),
    ("GET", "/api/person/{name}", "person"),
    ("POST", "/api/person/{name}/profile", "person_profile"),
    ("GET", "/api/voice-clip/{name}", "voice_clip"),
    ("GET", "/api/self", "self_get"),
    ("POST", "/api/self", "self_answer"),
    ("GET", "/api/audio", "audio_wav"),
    ("GET", "/api/listen", "listen"),
    ("WS", "/ws", "ws_endpoint"),
]


def flat(routes):
    for r in routes:
        if hasattr(r, "original_router"):  # an included router (FastAPI keeps it as one entry): its routes, in order
            yield from flat(r.original_router.routes)
        else:
            yield ",".join(sorted(getattr(r, "methods", None) or {"WS"})), r.path, r.endpoint.__name__


def test_routes():
    assert list(flat(server.app.routes)) == ROUTES


def test_middleware():
    assert [m.cls.__name__ for m in server.app.user_middleware] == ["Tenants"]
