import asyncio
import logging
import time
import random
import string
from typing import Optional, Dict, Any, Callable, List

from janus_client import JanusSession, JanusVideoRoomPlugin, PlayerStreamTrack
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, MediaStreamTrack, MediaPlayer, AudioFrame
from aiortc.contrib.media import MediaBlackhole
from av import AudioFrame
import httpx
from pyee.base import EventEmitter
from abc import ABCMeta, abstractmethod
import numpy as np
from pyee import AsyncIOEventEmitter

logger = logging.getLogger(__name__)

def random_tid(length=8) -> str:
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

class EventTarget:
    def __init__(self):
        self._listeners: Dict[str, List[Callable[[Any], None]]] = {}

    def add_event_listener(self, event_type: str, listener: Callable[[Any], None]):
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(listener)

    def remove_event_listener(self, event_type: str, listener: Callable[[Any], None]):
        if event_type in self._listeners:
            self._listeners[event_type].remove(listener)
            if not self._listeners[event_type]:
                del self._listeners[event_type]

    def dispatch_event(self, event_type: str, event_data: Any):
        if event_type in self._listeners:
            for listener in self._listeners[event_type]:
                listener(event_data)

class EventManager:
    def __init__(self):
        self.event_waiters: List[dict[str, Callable[[Any], Any]]] = []

    def add_waiter(self, predicate: Callable[[Any], bool], resolve: Callable[[Any], None], reject: Callable[[Exception], None]):
        self.event_waiters.append({
            "predicate": predicate,
            "resolve": resolve,
            "reject": reject,
        })

    def process_event(self, evt: Any):
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
    def __init__(
        self,
        webrtc_url: str,
        room_id: str,
        credential: str,
        user_id: str,
        stream_name: str,
        turn_servers: Dict[str, Any],
    ):
        self.webrtc_url = webrtc_url
        self.room_id = room_id
        self.credential = credential
        self.user_id = user_id
        self.stream_name = stream_name
        self.turn_servers = turn_servers

class JanusClient(EventEmitter):
    """
    A Python version of the TypeScript JanusClient, using aiortc + janus-client libraries.
    """

    def __init__(self, config: JanusConfig):
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
        1) Create a Janus session
        2) Attach to the VideoRoom plugin
        3) Create and join the room
        4) Create the aiortc RTCPeerConnection
        5) Enable local audio track
        6) Configure publisher
        """
        # 1) Create Session ID
        logger.info("Creating Janus client...")
        
        self.session_id = await self.create_session()

        # 2) Attach plugin
        logger.info("Attaching VideoRoom plugin...")
        self.handle_id = await self.attach_plugin()
        logger.info("VideoRoom plugin attached")
        
        # 3) Start polling
        self.poll_active = True
        self.start_polling()

        # 4) Create room
        await self.create_room()

        # 5) Join room as publisher
        self.publisher_id = await self.join_room()

        # 6) Create local aiortc RTCPeerConnection
        self.pc = RTCPeerConnection({
            "iceServers": [
                "urls": self.config.turn_servers["uris"],
                "username": self.config.turn_servers["username"],
                "credential": self.config.turn_servers["password"]
            ]
        })
        self.setup_peer_events()

        # 7) Enable local audio
        await self.enable_local_audio()

        # 7) Negotiate with Janus (send offer, get answer)
        await self.configure_publisher()

        logger.info("[JanusClient] Initialization complete")
        
    def enable_local_audio(self):
        if self.pc is None:
            logger.warn("[JanusClient] enableLocalAudio => No RTCPeerConnection")
            return
        if self.local_audio_source is not None:
            logger.info("[JanusClient] localAudioSource already active")
            return
        self.local_audio_source = JanusAudioSource()
#        track = self.local_audio_source.get_track()
#        local_stream =
        
    async def create_session(self) -> int:
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
            "Referer": "https://x.com"
        }
        body = {
            "janus": "create",
            "transaction": transaction
        }
        
        response = await client.post(self.config.webrtc_url, headers=headers, json=body)
        response.raise_for_status()

        data = response.json()
        
        return data["data"]["id"]

    async def create_room(self):
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}/{self.handle_id}"
        
        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json",
            "Referer": "https://x.com"
        }
        inner_body = {
            "request": "create"
            "room": self.config.room_id,
            "periscope_user_id": self.config.user_id,
            "audiocodec": "opus",
            "videocodec": "h264",
            "transport_wide_cc_ext": True,
            "app_component": "audio-room",
            "h264_profile": "42e01f",
            "dummy_publisher": False
        }
        outer_body = {
            "janus": "message",
            "transaction": transaction,
            "body": inner_body
        }
        
        response = await client.get(url, headers=headers, json=outer_body)
        response.raise_for_status()

        data = response.json()
        
        logger.info(f"[JanusClient] createRoom => {data}")
        logger.info(f"[JanusClient] Room {self.config.room_id} created successfully")

    async def join_room(self) -> int:
        logger.info("[JanusClient] joinRoom => start")
        def has_joined_event(e: Dict[str, Any]) -> bool:
            return e.get("janus") is not None and e["janus"] in "event" and
            e.get("plugindata") is not None and e["plugindata"]["plugin"] in "janus.plugin.videoroom" and
            e["plugindata"].get("data") is not None and e["plugindata"]["data"]["videoroom"] in "joined"
        evt_promise = self.wait_for_janus_event(has_joined_event, 12000, "Host Joined Event")
        
        body = {
            "request": "join",
            "room": self.config.room_id,
            "ptype": "publisher",
            "display": self.config.user_id,
            "periscope_user_id": self.config.user_id
        }
        await self.send_janus_message(self.handle_id, body)
        event = await evt_promise
        publisher_id = int(event["plugindata"]["data"]["id"])
        logger.info(f"[JanusClient] joined room => publisher_id={publisher_id}")
        return publisher_id

    async def send_janus_message(self, handle_id: int, body: Dict[str, Any], jsep: Optional[Dict[str, Any]] = None):
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}/{handle_id}"
        
        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json"
        }
        outer_body = {
            "janus": "message",
            "transaction": transaction,
            "body": body
        }
        
        if jsep is not None:
            outer_body["jsep"] = jsep
        
        response = await client.post(url, headers=headers, json=outer_body)
        response.raise_for_status()
        
    @self.on('error')
    def on_error(e: Exception):
        except e
        
    def setup_peer_events(self):
        if self.pc is None:
            return

        def connect_statge_change():
            logger.info(f"[JanusClient] ICE state => {self.pc.connectionState()}")
            if self.pc.connectionState() in "failed":
                self.emit('error', Exception("ICE connection failed"))
        self.pc.add_listener("iceconnectionstatechange", connect_statge_change)
        
        def track(event: Dict[str, Any]):
            kind = event["track"]["kind"]
            logger.info(f"[JanusClient] track => {kind}")
        self.pc.add_listener("track", track)

    async def wait_for_janus_event(self, predicate: Callable[[Any], bool], timeout_ms: int = 5000, description: str = "untitled event") -> Dict[str, Any]:
        future = asyncio.get_event_loop().create_future()
        waiter = {"predicate": predicate, "resolve": future.set_result, "reject": future.set_exception}
        self.event_waiters.append(waiter)

        try:
            # Wait for the future to complete, with a timeout
            return await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            # Remove the waiter from the list if it times out
            if waiter in self.event_waiters:
                self.event_waiters.remove(waiter)
            logger.warn(f"[JanusClient] wait_for_janus_event => timed out waiting for: {description}")
            raise Exception(f"[JanusClient] wait_for_janus_event (expecting '{description}') timed out after {timeout_ms}ms")

    async def attach_plugin(self) -> int:
        client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
        transaction = random_tid()
        url = f"{self.config.webrtc_url}/{self.session_id}"
        
        headers = {
            "Authorization": self.config.credential,
            "Content-Type": "application/json"
        }
        body = {
            "janus": "attach",
            "plugin": "janus.plugin.videoroom",
            "transaction": transaction
        }
        
        response = await client.post(self.config.webrtc_url, headers=headers, json=body)
        response.raise_for_status()

        data = response.json()
        
        return data["data"]["id"]
        
    def start_polling(self):
        logger.info("[JanusClient] Starting polling...")
        async def do_poll():
            client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
            url = f"{self.config.webrtc_url}/{self.session_id}?maxev=1&_={int(time.time())}"
            
            headers = {
                "Authorization": self.config.credential
            }
            
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()
            self.handle_janus_event(data)
            
            asyncio.sleep(0.5)
            do_poll()
        do_poll()
    
    # TODO: Handle errors
    def handle_janus_event(self, event: Dict[str, Any]):
        if event["janus"] in "keepalive":
            logger.info("[JanusClient] keepalive received")
            return
        if event["janus"] in "webrtcup":
            sender = event["sender"]
            logger.info(f"[JanusClient] webrtcup => {sender}")
        if event.get("jsep") is not None and event["jsep"]["type"] in "answer":
            self.on_received_answer(event["jsep"])
        if event.get("plugindata") is not None and event["plugindata"].get("data") is not None:
            self.publisher_id = int(event["plugindata"]["data"]["id"])
        self.event_waiters.process_event(event)
            
    async def on_received_answer(self, answer: Dict[str, Any]):
        if self.pc is None:
            return
        logger.info("[JanusClient] got answer => setRemoteDescription")
        desc = RTCSessionDescription()
        desc.type = answer["type"]
        desc.sdp = answer["sdp"]
        await self.pc.setRemoteDescription(desc)

#    async def subscribe_speaker(self, user_id: str):
#        """
#        Roughly similar to the TypeScript version:
#        1) Attach to plugin as subscriber
#        2) Wait for event with 'publishers'
#        3) Identify feedId from the matching user
#        4) Join as subscriber
#        5) Wait for 'attached' + JSEP offer
#        6) Create subPC, setRemoteDescription(offer), createAnswer, setLocalDescription
#        7) Send 'start'
#        """
#        if not self.video_room:
#            raise RuntimeError("VideoRoom plugin not attached")
#
#        logger.info(f"subscribeSpeaker => userId={user_id}")
#        # The janus-client Python library can create multiple plugin handles for you.
#        # So we get a new plugin handle for the subscriber:
#        sub_plugin = await self.session.attach(VideoRoomPlugin)
#        logger.info(f"Subscriber handle => {sub_plugin.handle_id}")
#
#        # 1) In the Python janus-client, you can fetch the list of publishers with .list_participants()
#        #    or you can watch for events. We'll do something simpler:
#        publishers_list = await self.video_room.list_participants(self.config.room_id)
#        # publishers_list is typically a dict with "participants": [...]
#        # each participant might have "publisher" and "id" and "display"
#        participants = publishers_list.get("participants", [])
#
#        # 2) Find the user
#        pub = None
#        for p in participants:
#            # Some Janus backends store user info in 'display' or in custom fields
#            # We'll do a naive approach:
#            if p.get("display") == user_id:
#                pub = p
#                break
#
#        if not pub:
#            raise RuntimeError(
#                f"No publisher found for userId={user_id} in participants list"
#            )
#        feed_id = pub["id"]
#        logger.info(f"Found feedId => {feed_id}")
#
#        # 3) "join" as subscriber
#        await sub_plugin.join(
#            room_id=self.config.room_id,
#            ptype="subscriber",
#            streams=[{"feed": feed_id, "mid": "0", "send": True}],
#        )
#
#        # The next step is that Janus should give us an offer (JSEP)
#        # We'll wait for the "attached" event.
#        # In the python janus-client, we typically do sub_plugin.on("event", callback)
#        # or we can call wait_for. Here's a rough approach:
#
#        jsep_offer = await sub_plugin.wait_for_jsep_offer(timeout=8.0)
#        logger.info("Subscriber => attached with offer")
#
#        # 4) Create subPc
#        sub_pc = self._create_peer_connection()
#        # Handle ontrack
#        @sub_pc.on("track")
#        def on_subscriber_track(track):
#            logger.info(
#                f"[JanusClient] subscriber track => kind={track.kind}, id={track.id}"
#            )
#            # In aiortc, we can attach a media sink or parse the audio frames ourselves
#            # For example, direct them to a blackhole or a custom processor
#            if track.kind == "audio":
#                # Just sink them (no-op). Or implement your own processing
#                blackhole = MediaBlackhole()
#                asyncio.ensure_future(self._run_sink(track, blackhole, user_id))
#
#        # 5) Set remote description with the offer
#        await sub_pc.setRemoteDescription(
#            RTCSessionDescription(sdp=jsep_offer["sdp"], type=jsep_offer["type"])
#        )
#        # 6) Create answer
#        answer = await sub_pc.createAnswer()
#        await sub_pc.setLocalDescription(answer)
#
#        # 7) Send 'start' with jsep=answer
#        await sub_plugin.start(jsep=answer)
#
#        # Store sub_pc for future reference/cleanup
#        self.subscribers[user_id] = sub_pc
#        logger.info(f"subscriber => done (user={user_id})")
#
#    def push_local_audio(self, samples: bytes, sample_rate: int = 48000, channels: int = 1):
#        if not self._local_audio_track:
#            logger.warning("[JanusClient] No localAudioSource; enabling now...")
#            # You can call `enable_local_audio()` or re-initialize the track
#            # but be mindful that re-initializing may require re-negotiation
#            return
#
#        # Convert your Int16Array to raw bytes if needed
#        # Assuming `samples` is already raw PCM bytes in 16-bit format
#        timestamp_sec = time.time()
#        self._local_audio_track.push_pcm_data(samples, timestamp_sec)
#
#    async def enable_local_audio(self):
#        """
#        Create the local audio track from PCM data if not already created,
#        and add it to the RTCPeerConnection.
#        """
#        if not self.pc_publisher:
#            logger.warning("[JanusClient] No RTCPeerConnection to add track to.")
#            return
#
#        if self._local_audio_track:
#            logger.info("[JanusClient] localAudioTrack already active")
#            return
#
#        self._local_audio_track = PCMDataAudioStreamTrack(
#            sample_rate=48000,
#            channels=1,
#        )
#        self.pc_publisher.addTrack(self._local_audio_track)
#
#    async def stop(self):
#        """Stop everything."""
#        logger.info("[JanusClient] Stopping...")
#        # If you have a plugin join, also leave the room if desired
#        await self.leave_room()
#
#        # Close the main PC
#        if self.pc_publisher:
#            await self.pc_publisher.close()
#            self.pc_publisher = None
#
##        # Close subscriber PCs
##        for user_id, pc in self.subscribers.items():
##            logger.info(f"Closing subscriber PC for user={user_id}")
##            await pc.close()
##        self.subscribers.clear()
##
##        # Detach plugin and destroy session
##        if self.video_room:
##            try:
##                await self.video_room.detach()
##            except JanusError as e:
##                logger.error(f"Error detaching video_room plugin: {e}")
##            self.video_room = None
##
##        if self.session:
##            await self.session.destroy()
##            self.session = None
#
#    async def create_room(self):
#        if self._room_created:
#            logger.info(f"Room '{self.config.room_id}' already created")
#            return
#        if not self.video_room:
#            raise RuntimeError("VideoRoom plugin not attached")
#
#        logger.info(f"Creating room '{self.config.room_id}'...")
#        # janus-client’s VideoRoomPlugin has a create_room() convenience method
#        try:
#            await self.video_room.create_room(
#                room_id=self.config.room_id,
#                audio_codec="opus",
#                video_codec="h264",
#                description="audio-room",
#            )
#            logger.info(f"Room '{self.config.room_id}' created successfully")
#            self._room_created = True
#        except Exception as err:
#            # Possibly the room already exists, so handle that gracefully
#            if "already exists" in str(err):
#                logger.warning(f"Room {self.config.room_id} already exists.")
#                self._room_created = True
#            else:
#                raise
#
#    async def join_room_as_publisher(self):
#        """
#        Join the video room as a publisher.
#        Returns the Janus response (which should contain 'id' field for publisherId).
#        """
#        if not self.video_room:
#            raise RuntimeError("VideoRoom plugin not attached")
#
#        logger.info(f"Joining room '{self.config.room_id}' as publisher...")
#        response = await self.video_room.join(
#            room_id=self.config.room_id,
#            ptype="publisher",
#            display=self.config.user_id,
#        )
#        logger.info(f"Join room response => {response}")
#        return response
#
#    async def configure_publisher(self):
#        """
#        Create an offer, setLocalDescription, send to Janus as 'configure' or 'publish',
#        and handle the answer from Janus.
#        """
#        if not self.pc_publisher or not self.video_room:
#            return
#
#        logger.info("[JanusClient] Creating offer for publishing...")
#        offer = await self.pc_publisher.createOffer(
#            offerToReceiveAudio=True,
#            offerToReceiveVideo=False,
#        )
#        await self.pc_publisher.setLocalDescription(offer)
#
#        # In janus-client Python, you can do something like:
#        response = await self.video_room.configure(
#            jsep=offer,
#            audio=True,
#            video=False,
#            room_id=self.config.room_id,
#            # Additional fields as needed:
#            # e.g. {"session_uuid": "", "stream_name": self.config.stream_name}
#        )
#        logger.info(f"[JanusClient] configure publisher => {response}")
#
#        # The response should contain a JSEP answer
#        jsep_answer = response.get("jsep", None)
#        if jsep_answer and jsep_answer.get("type") == "answer":
#            logger.info("[JanusClient] Got JSEP answer, setting remote description.")
#            await self.pc_publisher.setRemoteDescription(
#                RTCSessionDescription(
#                    sdp=jsep_answer["sdp"], type=jsep_answer["type"]
#                )
#            )
#
#    def _create_peer_connection(self) -> RTCPeerConnection:
#        """Helper to build an aiortc PeerConnection with TURN servers."""
#        ice_servers = [
#            RTCIceServer(
#                urls=self.config.turn_servers["uris"],
#                username=self.config.turn_servers["username"],
#                credential=self.config.turn_servers["password"],
#            )
#        ]
#        pc = RTCPeerConnection(configuration={"iceServers": ice_servers})
#        return pc
#
#    def _setup_publisher_pc_events(self, pc: RTCPeerConnection):
#        """Attach event handlers on the publisher PC."""
#        @pc.on("iceconnectionstatechange")
#        def on_ice_state_change():
#            logger.info(f"[JanusClient] ICE state => {pc.iceConnectionState}")
#            if pc.iceConnectionState == "failed":
#                logger.error("ICE connection failed")
#
#        @pc.on("track")
#        def on_track(track: MediaStreamTrack):
#            logger.info(f"[JanusClient] track => kind={track.kind}, id={track.id}")
#            # For local publisher PC, typically we do not expect inbound tracks.
#            # But if you do for some reason, handle them here.
#
#    async def _run_sink(self, track: MediaStreamTrack, sink, user_id: str):
#        """A helper to continuously pull frames from a track and write them to a sink."""
#        while True:
#            try:
#                frame = await track.recv()
#                await sink.write(frame)
#                # If you want to parse PCM data, you’d do that here
#                # e.g. transform frame to raw PCM, then do whatever you need
#            except Exception as e:
#                logger.info(
#                    f"Done reading track={track.kind} for user={user_id}, reason={e}"
#                )
#                break
#
#    async def destroy_room(self):
#        """Destroy the room if desired."""
#        if not self.video_room or not self._room_created:
#            logger.warning("[JanusClient] destroyRoom => no plugin or not created")
#            return
#        logger.info(f"Destroying room => {self.config.room_id}")
#        try:
#            await self.video_room.destroy_room(room_id=self.config.room_id)
#            logger.info("Room destroyed")
#        except Exception as e:
#            logger.error(f"destroyRoom failed => {e}")
#
#    async def leave_room(self):
#        """Leave the room (as a publisher)."""
#        if not self.video_room:
#            logger.warning("[JanusClient] leaveRoom => no plugin")
#            return
#        logger.info(f"[JanusClient] leaving room => {self.config.room_id}")
#        try:
#            await self.video_room.leave()
#            logger.info("Left room successfully")
#        except Exception as e:
#            logger.error(f"leaveRoom => error: {e}")
#
#    @staticmethod
#    def random_tid() -> str:
#        """Generate a random transaction ID if needed."""
#        return "".join(random.choice(string.ascii_letters) for _ in range(8))
