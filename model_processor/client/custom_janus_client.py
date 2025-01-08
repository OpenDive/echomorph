# ----- Standard Library Imports -----
import abc
import asyncio
import logging
import random
import string
import time
from typing import Any, Callable, Dict, List, Optional

# ----- Third-Party Imports -----
import httpx
import numpy as np
from abc import abstractmethod
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer
from av import AudioFrame
from pyee.asyncio import AsyncIOEventEmitter
from pyee.base import EventEmitter

# ----- Local Imports -----
from janus_client import JanusSession, JanusVideoRoomPlugin
from janus_client.media import PlayerStreamTrack

logger = logging.getLogger(__name__)


def random_tid(length: int = 8) -> str:
    """
    Generate a random transaction ID consisting of ASCII lowercase letters and digits.

    :param length: The length of the transaction ID.
    :return: The generated transaction ID string.
    """
    characters = string.ascii_lowercase + string.digits
    return "".join(random.choice(characters) for _ in range(length))


class EventTarget:
    """
    A basic event target implementation similar to browser-based event targets.
    Allows adding, removing, and dispatching listeners.
    """

    def __init__(self):
        self._listeners: Dict[str, List[Callable[[Any], None]]] = {}

    def add_event_listener(self, event_type: str, listener: Callable[[Any], None]):
        """
        Register a listener for a specific event type.

        :param event_type: The event type name.
        :param listener:   The callback function to handle the event.
        """
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(listener)

    def remove_event_listener(self, event_type: str, listener: Callable[[Any], None]):
        """
        Remove a previously registered listener for an event type.

        :param event_type: The event type name.
        :param listener:   The callback function to remove.
        """
        if event_type in self._listeners:
            self._listeners[event_type].remove(listener)
            if not self._listeners[event_type]:
                del self._listeners[event_type]

    def dispatch_event(self, event_type: str, event_data: Any):
        """
        Dispatch an event to all registered listeners.

        :param event_type: The event type name.
        :param event_data: Data to pass to each event listener.
        """
        if event_type in self._listeners:
            for listener in self._listeners[event_type]:
                listener(event_data)


class EventManager:
    """
    A manager that keeps track of "waiters" – objects that store:
      - a predicate function to check if an event matches,
      - a resolve callback to call when the event matches,
      - a reject callback for errors.
    """

    def __init__(self):
        # Each waiter is a dict with keys: { predicate, resolve, reject }
        self.event_waiters: List[Dict[str, Callable[[Any], Any]]] = []

    def add_waiter(
        self,
        predicate: Callable[[Any], bool],
        resolve: Callable[[Any], None],
        reject: Callable[[Exception], None],
    ):
        """
        Add an event waiter. If the predicate returns True, the resolve callback is called.

        :param predicate: A function to determine if an event should be handled.
        :param resolve:   A callback to invoke if the event matches the predicate.
        :param reject:    A callback to invoke if an error occurs.
        """
        self.event_waiters.append(
            {
                "predicate": predicate,
                "resolve": resolve,
                "reject": reject,
            }
        )

    def process_event(self, evt: Any):
        """
        Process an incoming event by checking against each waiter's predicate.

        :param evt: The event to process.
        """
        to_remove = []
        for waiter in self.event_waiters:
            try:
                if waiter["predicate"](evt):
                    waiter["resolve"](evt)
                    to_remove.append(waiter)
            except Exception as error:
                waiter["reject"](error)
                to_remove.append(waiter)

        # Remove processed waiters
        for waiter in to_remove:
            self.event_waiters.remove(waiter)


class JanusConfig:
    """
    Configuration object containing connection and room details for Janus.
    """

    def __init__(
        self,
        webrtc_url: str,
        room_id: str,
        credential: str,
        user_id: str,
        stream_name: str,
        turn_servers: Dict[str, Any],
    ):
        """
        :param webrtc_url:  The Janus server URL.
        :param room_id:     The ID of the room to create/join.
        :param credential:  Authentication or authorization token/key.
        :param user_id:     An identifier for the user/publisher.
        :param stream_name: Name to associate with the published stream.
        :param turn_servers: Dictionary containing TURN server details (uris, username, password).
        """
        self.webrtc_url = webrtc_url
        self.room_id = room_id
        self.credential = credential
        self.user_id = user_id
        self.stream_name = stream_name
        self.turn_servers = turn_servers


class RTCAudioSink:
    """
    Custom RTCAudioSink to receive audio frames from a MediaStreamTrack.
    Converts incoming frames to NumPy arrays, then passes them to an optional ondata callback.
    """

    def __init__(self, track: MediaStreamTrack):
        """
        Initialize the audio sink with a specific track and begin consuming it asynchronously.

        :param track: The MediaStreamTrack (audio) to consume.
        """
        self.track = track
        self.active = True
        self._ondata: Optional[Callable[[Dict], None]] = None

        # Start consuming the track
        asyncio.create_task(self._consume_audio())

    async def _consume_audio(self):
        """
        Internal coroutine that continuously consumes audio frames from the track
        and emits them via the ondata callback (if assigned).
        """
        async for frame in self.track:
            if not self.active:
                break

            # Convert the frame to a NumPy array
            pcm_data = frame.to_ndarray()
            frame_data = {
                "samples": pcm_data,
                "sampleRate": frame.sample_rate,
                "bitsPerSample": 16,
                "channelCount": pcm_data.shape[0] if pcm_data.ndim > 1 else 1,
            }

            if self._ondata:
                self._ondata(frame_data)

    @property
    def ondata(self) -> Optional[Callable[[Dict], None]]:
        """
        Get the callback that handles incoming audio frames.
        """
        return self._ondata

    @ondata.setter
    def ondata(self, callback: Callable[[Dict], None]):
        """
        Set the callback that handles incoming audio frames.
        """
        self._ondata = callback

    def stop(self):
        """
        Stop consuming audio frames.
        """
        self.active = False


class JanusClient(EventEmitter):
    """
    A Python version of the TypeScript JanusClient, leveraging aiortc + janus-client libraries.
    Provides methods to create/join/destroy rooms, handle publishing/subscribing, etc.
    """

    def __init__(self, config: JanusConfig):
        """
        :param config: JanusConfig object with the necessary configuration for the connection.
        """
        super().__init__()
        self.config = config
        self.poll_active = False

        self.session_id: Optional[int] = None
        self.handle_id: Optional[int] = None
        self.publisher_id: Optional[int] = None

        self.event_waiters = EventManager()
        self.pc: Optional[RTCPeerConnection] = None
        self.subscribers: Dict[str, RTCPeerConnection] = {}
        self.local_audio_track: Optional[PlayerStreamTrack] = None

        self._room_created = False

    async def initialize(self):
        """
        Perform initial setup:
          1. Create a Janus session
          2. Attach to the VideoRoom plugin
          3. Start the polling loop
          4. Create the room
          5. Join the room as publisher
          6. Create the main aiortc RTCPeerConnection
        TODO: Enable local audio and configure the publisher after this basic flow is confirmed.
        """
        logger.info("Creating Janus client...")

        # 1) Create Session ID
        self.session_id = await self.create_session()

        # 2) Attach plugin
        logger.info("Attaching VideoRoom plugin...")
        self.handle_id = await self.attach_plugin()
        logger.info("VideoRoom plugin attached")

        # 3) Start polling
        self.poll_active = True
        asyncio.create_task(self.start_polling())

        # 4) Create room
        await self.create_room()

        # 5) Join room as publisher
        self.publisher_id = await self.join_room()

        # 6) Create local aiortc RTCPeerConnection
        ice_server = RTCIceServer(
            {
                "urls": self.config.turn_servers["uris"],
                "username": self.config.turn_servers["username"],
                "credential": self.config.turn_servers["password"],
            }
        )
        rtc_config = RTCConfiguration()
        rtc_config.iceServers = [ice_server]
        self.pc = RTCPeerConnection(configuration=rtc_config)

        self.setup_peer_events()

        logger.info("[JanusClient] Initialization complete")

        # TODO:
        # - enable_local_audio() if needed
        # - configure_publisher() to finalize negotiation.

    async def create_session(self) -> int:
        """
        Create a new Janus session on the server.

        :return: The session ID assigned by Janus.
        """
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
            "Referer": "https://x.com",
        }
        body = {
            "janus": "create",
            "transaction": transaction,
        }

        response = await client.post(self.config.webrtc_url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        return data["data"]["id"]

    async def attach_plugin(self) -> int:
        """
        Attach the VideoRoom plugin to the current Janus session.

        :return: The plugin handle ID.
        """
        if self.session_id is None:
            raise Exception("[JanusClient] attachPlugin => no sessionId")

        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}"

        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
        }
        body = {
            "janus": "attach",
            "plugin": "janus.plugin.videoroom",
            "transaction": transaction,
        }

        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        return data["data"]["id"]

    async def create_room(self):
        """
        Attempt to create the specified room on Janus. 
        If the room already exists, logs a warning but won’t raise an error.
        """
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}/{self.handle_id}"

        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
            "Referer": "https://x.com",
        }
        inner_body = {
            "request": "create",
            "room": self.config.room_id,
            "periscope_user_id": self.config.user_id,
            "audiocodec": "opus",
            "videocodec": "h264",
            "transport_wide_cc_ext": True,
            "app_component": "audio-room",
            "h264_profile": "42e01f",
            "dummy_publisher": False,
        }
        outer_body = {
            "janus": "message",
            "transaction": transaction,
            "body": inner_body,
        }

        response = await client.post(url, headers=headers, json=outer_body)
        response.raise_for_status()
        data = response.json()

        logger.info(f"[JanusClient] createRoom => {data}")
        logger.info(f"[JanusClient] Room {self.config.room_id} created successfully")

    async def join_room(self) -> int:
        """
        Join the room as a publisher. Wait for the 'joined' event to confirm.

        :return: The publisher ID assigned by Janus.
        """
        logger.info("[JanusClient] joinRoom => start")

        def has_joined_event(e: Dict[str, Any]) -> bool:
            # Checking if the event indicates we've joined the room
            return (
                e.get("janus") == "event"
                and e.get("plugindata", {}).get("plugin") == "janus.plugin.videoroom"
                and e["plugindata"].get("data", {}).get("videoroom") == "joined"
            )

        # Wait up to 12 seconds for the event
        evt_promise = self.wait_for_janus_event(has_joined_event, 12000, "Host Joined Event")

        body = {
            "request": "join",
            "room": self.config.room_id,
            "ptype": "publisher",
            "display": self.config.user_id,
            "periscope_user_id": self.config.user_id,
        }
        await self.send_janus_message(self.handle_id, body)
        event = await evt_promise
        publisher_id = int(event["plugindata"]["data"]["id"])
        logger.info(f"[JanusClient] joined room => publisher_id={publisher_id}")
        return publisher_id

    async def send_janus_message(
        self,
        handle_id: int,
        body: Dict[str, Any],
        jsep: Optional[Dict[str, Any]] = None,
    ):
        """
        Send a generic 'message' to Janus for the given plugin handle.

        :param handle_id: The plugin handle ID to send the message to.
        :param body:      The request body.
        :param jsep:      Optional JSEP object if there’s a WebRTC offer/answer.
        """
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}/{handle_id}"

        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
        }
        outer_body = {
            "janus": "message",
            "transaction": transaction,
            "body": body,
        }

        if jsep is not None:
            outer_body["jsep"] = jsep

        response = await client.post(url, headers=headers, json=outer_body)
        response.raise_for_status()

    def setup_peer_events(self):
        """
        Set up aiortc peer connection event handlers such as ontrack.
        """
        if self.pc is None:
            return

        def track(event: Dict[str, Any]):
            kind = event["track"]["kind"]
            logger.info(f"[JanusClient] track => {kind}")

        # NOTE: aiortc typically provides 'pc.on("track", some_callback)'
        # For demonstration, we do a simplified approach:
        self.pc.add_listener("track", track)

    async def wait_for_janus_event(
        self,
        predicate: Callable[[Any], bool],
        timeout_ms: int = 5000,
        description: str = "untitled event",
    ) -> Dict[str, Any]:
        """
        Wait for a Janus event that satisfies a given predicate.

        :param predicate:   A function taking the event and returning True if it matches.
        :param timeout_ms:  How long to wait (in milliseconds) before timing out.
        :param description: A short description of what event we're waiting for, used for logging.
        :return:            The event data if found.
        :raises Exception:  If the timeout is reached without seeing the desired event.
        """
        future = asyncio.get_event_loop().create_future()
        waiter = {
            "predicate": predicate,
            "resolve": future.set_result,
            "reject": future.set_exception,
        }
        self.event_waiters.event_waiters.append(waiter)

        try:
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            # Remove the waiter if it times out
            if waiter in self.event_waiters.event_waiters:
                self.event_waiters.event_waiters.remove(waiter)
            logger.warning(
                f"[JanusClient] wait_for_janus_event => timed out waiting for: {description}"
            )
            raise Exception(
                f"[JanusClient] wait_for_janus_event (expecting '{description}') "
                f"timed out after {timeout_ms}ms"
            )

    async def start_polling(self):
        """
        Continuously poll the Janus server for events related to this session.
        Breaks out of the loop if poll_active is set to False or session_id is None.
        """
        logger.info("[JanusClient] Starting polling...")

        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=30)) as client:
            while self.poll_active and self.session_id:
                try:
                    url = f"{self.config.webrtc_url}/{self.session_id}?maxev=1&_={int(time.time())}"
                    headers = {"Authorization": self.config.credential}
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()

                    data = response.json()
                    self.handle_janus_event(data)

                except httpx.RequestError as e:
                    logger.error(f"[JanusClient] Polling error: {e}")
                except httpx.HTTPStatusError as e:
                    logger.warning(f"[JanusClient] HTTP error: {e.response.status_code}")
                except Exception as e:
                    logger.error(f"[JanusClient] Unexpected error: {e}")

                await asyncio.sleep(0.5)

    def handle_janus_event(self, event: Dict[str, Any]):
        """
        Dispatch or handle incoming Janus events (keepalive, webrtcup, JSEP answers, etc.).
        Also feeds events into the EventManager’s process_event.
        
        :param event: The event data from Janus.
        """
        if event.get("janus") == "keepalive":
            logger.info("[JanusClient] keepalive received")
            return

        if event.get("janus") == "webrtcup":
            sender = event["sender"]
            logger.info(f"[JanusClient] webrtcup => {sender}")

        if event.get("jsep") and event["jsep"].get("type") == "answer":
            asyncio.create_task(self.on_received_answer(event["jsep"]))

        # Update publisher_id if present in the event
        if (
            event.get("plugindata")
            and event["plugindata"].get("data")
            and "id" in event["plugindata"]["data"]
        ):
            self.publisher_id = int(event["plugindata"]["data"]["id"])

        self.event_waiters.process_event(event)

    async def on_received_answer(self, answer: Dict[str, Any]):
        """
        Handle an incoming JSEP 'answer' from Janus.

        :param answer: The JSEP answer dictionary with 'type' and 'sdp'.
        """
        if self.pc is None:
            return
        logger.info("[JanusClient] got answer => setRemoteDescription")
        desc = RTCSessionDescription(type=answer["type"], sdp=answer["sdp"])
        await self.pc.setRemoteDescription(desc)

    # TODO: Add additional methods (e.g. subscribe_speaker, enable_local_audio, configure_publisher)
    # to complete Janus client logic.
