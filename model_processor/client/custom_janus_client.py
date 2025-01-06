import asyncio
import logging
import time
import random
import string
from typing import Optional, Dict, Any

from janus_client import JanusSession, JanusVideoRoomPlugin
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, MediaStreamTrack
from aiortc.contrib.media import MediaBlackhole
from av import AudioFrame

logger = logging.getLogger(__name__)


class PCMDataAudioStreamTrack(MediaStreamTrack):
    """
    A custom aiortc AudioStreamTrack that you can push PCM data into.
    We store the incoming PCM frames and serve them on demand in `recv()`.
    
    TODO: 
    - Need to handle buffering, sample rates, converting to 16-bit, etc.
    - aiortc expects frames at ~20ms intervals in standard stereo/mono.
    """

    kind = "audio"

    def __init__(self, sample_rate: int = 48000, channels: int = 1):
        super().__init__()
        self._sample_rate = sample_rate
        self._channels = channels
        # A simple buffer of AudioFrame objects
        self._frames = asyncio.Queue()

    async def recv(self) -> AudioFrame:
        """
        Called by aiortc to get the next chunk of audio data to send.
        We block until we have a frame in our queue.
        """
        frame = await self._frames.get()
        return frame

    def push_pcm_data(self, samples: int):
        """
        Accept PCM data from external code.
        - `samples` should be raw PCM bytes (16-bit signed).
        - `timestamp` in seconds or some monotonic reference.
        """
        # Create an AudioFrame
        frame = AudioFrame(
            samples,
            layout="mono" if self._channels == 1 else "stereo"
        )

        frame.sample_rate = self._sample_rate

        # Put it in the queue to be served by recv()
        self._frames.put_nowait(frame)


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
        
class JanusAudioSource:
    """
    Python version of the TypeScript JanusAudioSource.
    Holds a custom PCMDataAudioStreamTrack and exposes push_pcm_data().
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 1):
        self._track = PCMDataAudioStreamTrack(
            sample_rate=sample_rate, channels=channels
        )

    def get_track(self) -> MediaStreamTrack:
        """
        Return the aiortc MediaStreamTrack that can be added to a PeerConnection.
        """
        return self._track

#    def pushPcmData(
#        self, samples: Int16Array, sampleRate: int, channels: int = 1
#    ):
#        """
#        In TypeScript, you call this.source.onData(...).
#        Here, we call `push_pcm_data(...)` on our custom track.
#        
#        We'll assume `samples` is an Int16Array-like object. In Python,
#        you might actually have a list or a numpy array. We'll convert it to bytes.
#        """
#        if channels != self._track.channels or sampleRate != self._track.sample_rate:
#            logger.warning(
#                "Pushing PCM data with different rate or channel count than the original track!"
#            )
#
#        # Convert Int16 array -> raw bytes in little-endian
#        # e.g. if you already have a Python 'bytes' object, skip this step
#        raw_bytes = samples.tobytes()  # works if samples is a numpy array or array module
#
#        timestamp = time.time()  # or any monotonic clock
#        self._track.push_pcm_data(
#            samples=raw_bytes,
#            timestamp=timestamp,
#        )


class JanusAudioSink:
    """
    Python version of the TypeScript JanusAudioSink.
    Listens for audio data from a track, delivering raw PCM frames to a callback.
    
    We don't have an `ondata` property as in Node's RTCAudioSink,
    so we run a task that continuously calls track.recv().
    """

    def __init__(
        self,
        track: MediaStreamTrack
    ):
        if track.kind != "audio":
            raise ValueError("JanusAudioSink must be constructed with an audio track")

        self.track = track
        self.active = True
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        """
        Read from the track until stop() is called or the track ends,
        then call self._on_audio_data(frame_dict).
        """
        while self.active:
            try:
                frame = await self.track.recv()
            except (asyncio.CancelledError, Exception):
                logger.info("JanusAudioSink: track ended or error.")
                break

            # Convert the aiortc AudioFrame to raw PCM
            # By default, frames from aiortc are 16-bit 48kHz.
            # You can call frame.to_ndarray(format='s16') to get a numpy array of shape (samples, channels).
            # For demonstration, let's do that:
            pcm_samples = frame.to_ndarray(format="s16")
            # pcm_samples is now a numpy int16 array with shape (samples, channels).
            # The sample_rate might be in frame.sample_rate (aiortc >= 1.4.0).
            # Let's read it, or assume 48000 if not present.
            sample_rate = getattr(frame, "sample_rate", 48000)
            channel_count = pcm_samples.shape[1] if len(pcm_samples.shape) > 1 else 1

            # Turn that into the same dictionary shape the TypeScript code emitted:
            frame_dict = {
                "samples": pcm_samples,     # or pcm_samples.flatten() if you want interleaved
                "sampleRate": sample_rate,
                "bitsPerSample": 16,
                "channelCount": channel_count,
            }

#            if self._on_audio_data:
#                self._on_audio_data(frame_dict)

    def stop(self):
        """
        Stop reading from the track and cancel the task.
        """
        self.active = False
        if not self._task.done():
            self._task.cancel()
        if self.track:
            self.track.stop()


class JanusClient:
    """
    A Python version of the TypeScript JanusClient, using aiortc + janus-client libraries.
    """

    def __init__(self, config: JanusConfig):
        self.config = config

        self.session: Optional[JanusSession] = None
        self.video_room: Optional[VideoRoomPlugin] = None

        # Used to store the main publisher PC:
        self.pc_publisher: Optional[RTCPeerConnection] = None

        # Store subscriber PCs keyed by userId
        self.subscribers: Dict[str, RTCPeerConnection] = {}

        # Our local audio track (for publishing)
        self._local_audio_track: Optional[PCMDataAudioStreamTrack] = None

        self.publisher_id: Optional[int] = None
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
        # 1) Create Janus Session
        logger.info("Creating Janus session...")
        self.session = JanusSession(
            url=self.config.webrtc_url,
            token=self.config.credential,  # if needed as Bearer
        )
        await self.session.create()  # Establish the session

        # 2) Attach plugin
        logger.info("Attaching VideoRoom plugin...")
        self.video_room = await self.session.attach(VideoRoomPlugin)
        logger.info("VideoRoom plugin attached")

        # 3) Create room
        await self.create_room()

        # 4) Join room as publisher
        joined_response = await self.join_room_as_publisher()
        self.publisher_id = joined_response.get("id")
        logger.info(f"Joined room as publisher => publisherId={self.publisher_id}")

        # 5) Create local aiortc RTCPeerConnection
        self.pc_publisher = self._create_peer_connection()
        self._setup_publisher_pc_events(self.pc_publisher)

        # 6) Enable local audio
        await self.enable_local_audio()

        # 7) Negotiate with Janus (send offer, get answer)
        await self.configure_publisher()

        logger.info("JanusClient initialization complete")

    async def subscribe_speaker(self, user_id: str):
        """
        Roughly similar to the TypeScript version:
        1) Attach to plugin as subscriber
        2) Wait for event with 'publishers'
        3) Identify feedId from the matching user
        4) Join as subscriber
        5) Wait for 'attached' + JSEP offer
        6) Create subPC, setRemoteDescription(offer), createAnswer, setLocalDescription
        7) Send 'start'
        """
        if not self.video_room:
            raise RuntimeError("VideoRoom plugin not attached")

        logger.info(f"subscribeSpeaker => userId={user_id}")
        # The janus-client Python library can create multiple plugin handles for you.
        # So we get a new plugin handle for the subscriber:
        sub_plugin = await self.session.attach(VideoRoomPlugin)
        logger.info(f"Subscriber handle => {sub_plugin.handle_id}")

        # 1) In the Python janus-client, you can fetch the list of publishers with .list_participants()
        #    or you can watch for events. We'll do something simpler:
        publishers_list = await self.video_room.list_participants(self.config.room_id)
        # publishers_list is typically a dict with "participants": [...]
        # each participant might have "publisher" and "id" and "display"
        participants = publishers_list.get("participants", [])

        # 2) Find the user
        pub = None
        for p in participants:
            # Some Janus backends store user info in 'display' or in custom fields
            # We'll do a naive approach:
            if p.get("display") == user_id:
                pub = p
                break

        if not pub:
            raise RuntimeError(
                f"No publisher found for userId={user_id} in participants list"
            )
        feed_id = pub["id"]
        logger.info(f"Found feedId => {feed_id}")

        # 3) "join" as subscriber
        await sub_plugin.join(
            room_id=self.config.room_id,
            ptype="subscriber",
            streams=[{"feed": feed_id, "mid": "0", "send": True}],
        )

        # The next step is that Janus should give us an offer (JSEP)
        # We'll wait for the "attached" event.
        # In the python janus-client, we typically do sub_plugin.on("event", callback)
        # or we can call wait_for. Here's a rough approach:

        jsep_offer = await sub_plugin.wait_for_jsep_offer(timeout=8.0)
        logger.info("Subscriber => attached with offer")

        # 4) Create subPc
        sub_pc = self._create_peer_connection()
        # Handle ontrack
        @sub_pc.on("track")
        def on_subscriber_track(track):
            logger.info(
                f"[JanusClient] subscriber track => kind={track.kind}, id={track.id}"
            )
            # In aiortc, we can attach a media sink or parse the audio frames ourselves
            # For example, direct them to a blackhole or a custom processor
            if track.kind == "audio":
                # Just sink them (no-op). Or implement your own processing
                blackhole = MediaBlackhole()
                asyncio.ensure_future(self._run_sink(track, blackhole, user_id))

        # 5) Set remote description with the offer
        await sub_pc.setRemoteDescription(
            RTCSessionDescription(sdp=jsep_offer["sdp"], type=jsep_offer["type"])
        )
        # 6) Create answer
        answer = await sub_pc.createAnswer()
        await sub_pc.setLocalDescription(answer)

        # 7) Send 'start' with jsep=answer
        await sub_plugin.start(jsep=answer)

        # Store sub_pc for future reference/cleanup
        self.subscribers[user_id] = sub_pc
        logger.info(f"subscriber => done (user={user_id})")

    def push_local_audio(self, samples: bytes, sample_rate: int = 48000, channels: int = 1):
        if not self._local_audio_track:
            logger.warning("[JanusClient] No localAudioSource; enabling now...")
            # You can call `enable_local_audio()` or re-initialize the track
            # but be mindful that re-initializing may require re-negotiation
            return

        # Convert your Int16Array to raw bytes if needed
        # Assuming `samples` is already raw PCM bytes in 16-bit format
        timestamp_sec = time.time()
        self._local_audio_track.push_pcm_data(samples, timestamp_sec)

    async def enable_local_audio(self):
        """
        Create the local audio track from PCM data if not already created,
        and add it to the RTCPeerConnection.
        """
        if not self.pc_publisher:
            logger.warning("[JanusClient] No RTCPeerConnection to add track to.")
            return

        if self._local_audio_track:
            logger.info("[JanusClient] localAudioTrack already active")
            return

        self._local_audio_track = PCMDataAudioStreamTrack(
            sample_rate=48000,
            channels=1,
        )
        self.pc_publisher.addTrack(self._local_audio_track)

    async def stop(self):
        """Stop everything."""
        logger.info("[JanusClient] Stopping...")
        # If you have a plugin join, also leave the room if desired
        await self.leave_room()

        # Close the main PC
        if self.pc_publisher:
            await self.pc_publisher.close()
            self.pc_publisher = None

#        # Close subscriber PCs
#        for user_id, pc in self.subscribers.items():
#            logger.info(f"Closing subscriber PC for user={user_id}")
#            await pc.close()
#        self.subscribers.clear()
#
#        # Detach plugin and destroy session
#        if self.video_room:
#            try:
#                await self.video_room.detach()
#            except JanusError as e:
#                logger.error(f"Error detaching video_room plugin: {e}")
#            self.video_room = None
#
#        if self.session:
#            await self.session.destroy()
#            self.session = None

    async def create_room(self):
        if self._room_created:
            logger.info(f"Room '{self.config.room_id}' already created")
            return
        if not self.video_room:
            raise RuntimeError("VideoRoom plugin not attached")

        logger.info(f"Creating room '{self.config.room_id}'...")
        # janus-client’s VideoRoomPlugin has a create_room() convenience method
        try:
            await self.video_room.create_room(
                room_id=self.config.room_id,
                audio_codec="opus",
                video_codec="h264",
                description="audio-room",
            )
            logger.info(f"Room '{self.config.room_id}' created successfully")
            self._room_created = True
        except Exception as err:
            # Possibly the room already exists, so handle that gracefully
            if "already exists" in str(err):
                logger.warning(f"Room {self.config.room_id} already exists.")
                self._room_created = True
            else:
                raise

    async def join_room_as_publisher(self):
        """
        Join the video room as a publisher.
        Returns the Janus response (which should contain 'id' field for publisherId).
        """
        if not self.video_room:
            raise RuntimeError("VideoRoom plugin not attached")

        logger.info(f"Joining room '{self.config.room_id}' as publisher...")
        response = await self.video_room.join(
            room_id=self.config.room_id,
            ptype="publisher",
            display=self.config.user_id,
        )
        logger.info(f"Join room response => {response}")
        return response

    async def configure_publisher(self):
        """
        Create an offer, setLocalDescription, send to Janus as 'configure' or 'publish',
        and handle the answer from Janus.
        """
        if not self.pc_publisher or not self.video_room:
            return

        logger.info("[JanusClient] Creating offer for publishing...")
        offer = await self.pc_publisher.createOffer(
            offerToReceiveAudio=True,
            offerToReceiveVideo=False,
        )
        await self.pc_publisher.setLocalDescription(offer)

        # In janus-client Python, you can do something like:
        response = await self.video_room.configure(
            jsep=offer,
            audio=True,
            video=False,
            room_id=self.config.room_id,
            # Additional fields as needed:
            # e.g. {"session_uuid": "", "stream_name": self.config.stream_name}
        )
        logger.info(f"[JanusClient] configure publisher => {response}")

        # The response should contain a JSEP answer
        jsep_answer = response.get("jsep", None)
        if jsep_answer and jsep_answer.get("type") == "answer":
            logger.info("[JanusClient] Got JSEP answer, setting remote description.")
            await self.pc_publisher.setRemoteDescription(
                RTCSessionDescription(
                    sdp=jsep_answer["sdp"], type=jsep_answer["type"]
                )
            )

    def _create_peer_connection(self) -> RTCPeerConnection:
        """Helper to build an aiortc PeerConnection with TURN servers."""
        ice_servers = [
            RTCIceServer(
                urls=self.config.turn_servers["uris"],
                username=self.config.turn_servers["username"],
                credential=self.config.turn_servers["password"],
            )
        ]
        pc = RTCPeerConnection(configuration={"iceServers": ice_servers})
        return pc

    def _setup_publisher_pc_events(self, pc: RTCPeerConnection):
        """Attach event handlers on the publisher PC."""
        @pc.on("iceconnectionstatechange")
        def on_ice_state_change():
            logger.info(f"[JanusClient] ICE state => {pc.iceConnectionState}")
            if pc.iceConnectionState == "failed":
                logger.error("ICE connection failed")

        @pc.on("track")
        def on_track(track: MediaStreamTrack):
            logger.info(f"[JanusClient] track => kind={track.kind}, id={track.id}")
            # For local publisher PC, typically we do not expect inbound tracks.
            # But if you do for some reason, handle them here.

    async def _run_sink(self, track: MediaStreamTrack, sink, user_id: str):
        """A helper to continuously pull frames from a track and write them to a sink."""
        while True:
            try:
                frame = await track.recv()
                await sink.write(frame)
                # If you want to parse PCM data, you’d do that here
                # e.g. transform frame to raw PCM, then do whatever you need
            except Exception as e:
                logger.info(
                    f"Done reading track={track.kind} for user={user_id}, reason={e}"
                )
                break

    async def destroy_room(self):
        """Destroy the room if desired."""
        if not self.video_room or not self._room_created:
            logger.warning("[JanusClient] destroyRoom => no plugin or not created")
            return
        logger.info(f"Destroying room => {self.config.room_id}")
        try:
            await self.video_room.destroy_room(room_id=self.config.room_id)
            logger.info("Room destroyed")
        except Exception as e:
            logger.error(f"destroyRoom failed => {e}")

    async def leave_room(self):
        """Leave the room (as a publisher)."""
        if not self.video_room:
            logger.warning("[JanusClient] leaveRoom => no plugin")
            return
        logger.info(f"[JanusClient] leaving room => {self.config.room_id}")
        try:
            await self.video_room.leave()
            logger.info("Left room successfully")
        except Exception as e:
            logger.error(f"leaveRoom => error: {e}")

    @staticmethod
    def random_tid() -> str:
        """Generate a random transaction ID if needed."""
        return "".join(random.choice(string.ascii_letters) for _ in range(8))
