# ----- Standard Library Imports -----
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

# ----- Third-Party Imports -----
import websockets
from pyee.base import EventEmitter
from websockets import WebSocketClientProtocol

logger = logging.getLogger(__name__)


def safe_json(text: str) -> Any:
    """
    Safely parse JSON from a string. 
    Returns None if parsing fails.

    :param text: The string to parse as JSON.
    :return:     The parsed JSON object or None on error.
    """
    try:
        return json.loads(text)
    except:
        return None


class ChatClient(EventEmitter):
    """
    Python version of the TypeScript ChatClient.
    Uses websockets to connect to a remote chat API.
    """

    def __init__(self, space_id: str, access_token: str, endpoint: str):
        """
        :param space_id:     The unique identifier of the Space or room to join.
        :param access_token: Token used for authentication with the chat service.
        :param endpoint:     The base URL (HTTPS) of the chat server.
        """
        super().__init__()
        self.space_id = space_id
        self.access_token = access_token
        self.endpoint = endpoint

        self.ws: Optional[WebSocketClientProtocol] = None
        self.connected = False
        self._task: Optional[asyncio.Task] = None  # Background task for reading messages

    async def connect(self):
        """
        Opens a WebSocket connection to the server, sends authentication & join messages,
        and starts a background task to read incoming messages.
        """
        ws_url = self.endpoint.replace("https://", "wss://") + "/chatapi/v1/chatnow"
        logger.info("[ChatClient] Connecting => %s", ws_url)

        try:
            self.ws = await websockets.connect(
                ws_url,
                extra_headers={
                    "Origin": "https://x.com",
                    "User-Agent": "Mozilla/5.0",
                },
            )
        except Exception as e:
            logger.error("[ChatClient] WebSocket connect failed => %s", e)
            raise

        self.connected = True
        logger.info("[ChatClient] Connected")

        await self._send_auth_and_join()
        self._task = asyncio.create_task(self._read_loop())

    async def _send_auth_and_join(self):
        """
        Sends authentication token and join messages over the WebSocket connection.
        """
        if not self.ws:
            return

        # Send auth message
        msg_auth = {
            "payload": json.dumps({"access_token": self.access_token}),
            "kind": 3,
        }
        await self.ws.send(json.dumps(msg_auth))

        # Send join message
        msg_join = {
            "payload": json.dumps({
                "body": json.dumps({"room": self.space_id}),
                "kind": 1,
            }),
            "kind": 2,
        }
        await self.ws.send(json.dumps(msg_join))

        logger.info("[ChatClient] Auth & Join messages sent")

    async def _read_loop(self):
        """
        Continuously reads messages from the WebSocket.
        If the socket closes or errors, emits 'disconnected'.
        """
        try:
            async for raw_message in self.ws:
                await self.handle_message(raw_message)
        except websockets.ConnectionClosedOK:
            logger.info("[ChatClient] Connection closed (OK)")
        except websockets.ConnectionClosedError as e:
            logger.error("[ChatClient] Connection closed with error => %s", e)
        except Exception as e:
            logger.error("[ChatClient] Unexpected error => %s", e)
        finally:
            self.connected = False
            self.emit("disconnected")

    async def handle_message(self, raw: str):
        """
        Parse and handle incoming messages from the server.

        :param raw: The raw JSON string received from the chat server.
        """
        logger.debug("[ChatClient] Received message => %s", raw)

        msg = safe_json(raw)
        if not msg or "payload" not in msg:
            return

        payload = safe_json(msg["payload"])
        if not payload or "body" not in payload:
            return

        body = safe_json(payload["body"])
        if not body:
            return

        # Handle speaker request
        if body.get("guestBroadcastingEvent") == 1:
            speaker_req = {
                "userId": body.get("guestRemoteID"),
                "username": body.get("guestUsername"),
                "displayName": payload.get("sender", {}).get("display_name")
                               or body.get("guestUsername"),
                "sessionUUID": body.get("sessionUUID"),
            }
            self.emit("speakerRequest", speaker_req)

        # Handle occupancy update
        if isinstance(body.get("occupancy"), int):
            occupancy_update = {
                "occupancy": body["occupancy"],
                "totalParticipants": body.get("total_participants", 0),
            }
            self.emit("occupancyUpdate", occupancy_update)

        # Handle mute state changes
        if body.get("guestBroadcastingEvent") == 16:
            self.emit("muteStateChanged", {
                "userId": body.get("guestRemoteID"),
                "muted": True,
            })
        if body.get("guestBroadcastingEvent") == 17:
            self.emit("muteStateChanged", {
                "userId": body.get("guestRemoteID"),
                "muted": False,
            })

    async def disconnect(self):
        """
        Close the WebSocket connection and stop the read loop task.
        """
        if self.ws:
            logger.info("[ChatClient] Disconnecting...")
            await self.ws.close()
            self.ws = None
            self.connected = False

        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
