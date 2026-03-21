import datetime
import json
import mimetypes
import os
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
HOST = os.getenv("HOST", "0.0.0.0") 
PORT = int(os.getenv("PORT", "10000"))
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "todemy-voice-agent").strip()


def sanitize_room_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return cleaned[:64] or f"todemy-{secrets.token_hex(4)}"


def build_token_payload(mode: str, choice: str, room_name: str | None = None) -> dict:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"single", "multi"}:
        raise ValueError("mode must be 'single' or 'multi'")

    if not LIVEKIT_URL:
        raise ValueError("LIVEKIT_URL is not configured in .env")

    room_name = sanitize_room_name(
        room_name or f"todemy-{normalized_mode}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    identity = f"web-{normalized_mode}-{secrets.token_hex(5)}"
    dispatch_metadata = json.dumps({"mode": normalized_mode, "choice": choice})

    token = (
        api.AccessToken()
        .with_identity(identity)
        .with_name(f"{choice.title()} User")
        .with_metadata(json.dumps({"ui_choice": choice, "mode": normalized_mode}))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            RoomConfiguration(
                agents=[
                    RoomAgentDispatch(
                        agent_name=LIVEKIT_AGENT_NAME,
                        metadata=dispatch_metadata,
                    )
                ]
            )
        )
        .with_ttl(datetime.timedelta(hours=1))
        .to_jwt()
    )

    return {
        "serverUrl": LIVEKIT_URL,
        "roomName": room_name,
        "participantIdentity": identity,
        "agentName": LIVEKIT_AGENT_NAME,
        "mode": normalized_mode,
        "token": token,
    }


class VoiceUIHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        content = file_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(file_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_file(BASE_DIR / "index.html")
            return

        if self.path == "/health":
            self._send_json({"ok": True, "agentName": LIVEKIT_AGENT_NAME})
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/token":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            payload = build_token_payload(
                mode=str(body.get("mode", "single")),
                choice=str(body.get("choice", "avatar")),
                room_name=body.get("roomName"),
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            self._send_json({"error": "Failed to create token"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json(payload)

    def log_message(self, format: str, *args) -> None:
        print(f"[ui-server] {self.address_string()} - {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), VoiceUIHandler)
    print(f"UI server running at http://{HOST}:{PORT}")
    server.serve_forever()
