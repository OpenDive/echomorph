import asyncio
import json
import logging
from typing import Callable, Any, Dict, List, Optional

import websockets
from websockets import WebSocketClientProtocol

logger = logging.getLogger(__name__)


def safe_json(text: str) -> Any:
    """Safe JSON parse, returns None on parse error."""
    try:
        return json.loads(text)
    except:
        return None


class EventEmitter:
    """
    A minimal event-emitter style interface for Python.
    """
    def __init__(self):
        self._listeners: Dict[str, List[Callable[..., None]]] = {}

    def on(self, event: str, callback: Callable[..., None]):
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Callable[..., None]):
        if event in self._listeners:
            self._listeners[event] = [
                cb for cb in self._listeners[event] if cb != callback
            ]

    def emit(self, event: str, *args, **kwargs):
        if event in self._listeners:
            for callback in self._listeners[event]:
                callback(*args, **kwargs)


class ChatClient(EventEmitter):
    """
    Python version of the TypeScript ChatClient.  
    Uses websockets to connect to the remote chat API.
    """

    def __init__(self, space_id: str, access_token: str, endpoint: str):
        super().__init__()
        self.space_id = space_id
        self.access_token = access_token
        self.endpoint = endpoint

        self.ws: Optional[WebSocketClientProtocol] = None
        self.connected = False
        self._task: Optional[asyncio.Task] = None  # reading loop task

    async def connect(self):
        """
        Opens a WebSocket connection to the server, sends auth & join messages,
        and starts a background task to read incoming messages.
        """
        ws_url = self.endpoint.replace("https://", "wss://") + "/chatapi/v1/chatnow"
        logger.info("[ChatClient] Connecting => %s", ws_url)

        # Create the websocket connection
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
        # Once connected, send authentication & join messages
        await self._send_auth_and_join()

        # Start the read loop in a background task
        self._task = asyncio.create_task(self._read_loop())

    async def _send_auth_and_join(self):
        """
        Sends the authentication token and join message over the WS connection.
        """
        if not self.ws:
            return

        # Auth
        msg_auth = {
            "payload": json.dumps({"access_token": self.access_token}),
            "kind": 3,
        }
        await self.ws.send(json.dumps(msg_auth))

        # Join
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
        When the socket closes or errors, emit 'disconnected'.
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

        # Example of speaker request detection
        if body.get("guestBroadcastingEvent") == 1:
            # A speaker request
            speaker_req = {
                "userId": body.get("guestRemoteID"),
                "username": body.get("guestUsername"),
                "displayName": payload.get("sender", {}).get("display_name")
                             or body.get("guestUsername"),
                "sessionUUID": body.get("sessionUUID"),
            }
            self.emit("speakerRequest", speaker_req)

        # Example of occupancy update
        if isinstance(body.get("occupancy"), int):
            occupancy_update = {
                "occupancy": body["occupancy"],
                "totalParticipants": body.get("total_participants", 0),
            }
            self.emit("occupancyUpdate", occupancy_update)

        # Example of mute state
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
        Close the WebSocket connection.
        """
        if self.ws:
            logger.info("[ChatClient] Disconnecting...")
            await self.ws.close()
            self.ws = None
            self.connected = False

        # Cancel the reading task if it's still running
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    async def main():
        client = ChatClient(
            space_id="my-space-id",
            access_token="abc123",
            endpoint="https://example.com",
        )

        def on_speaker_request(req):
            print("[EVENT] Speaker request =>", req)

        def on_occupancy_update(data):
            print("[EVENT] Occupancy update =>", data)

        def on_mute_state_changed(info):
            print("[EVENT] Mute state changed =>", info)

        def on_disconnected():
            print("[EVENT] Disconnected")

        # Register event listeners
        client.on("speakerRequest", on_speaker_request)
        client.on("occupancyUpdate", on_occupancy_update)
        client.on("muteStateChanged", on_mute_state_changed)
        client.on("disconnected", on_disconnected)

        await client.connect()

        # Keep running for a while to test
        await asyncio.sleep(30)

        await client.disconnect()

    asyncio.run(main())
