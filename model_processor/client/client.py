import sys
import json
import urllib.parse
import threading
import queue
import requests
import subprocess
import tempfile
import os
import torch
import asyncio
import websockets  # pip install websockets

from lightning_whisper_mlx import LightningWhisperMLX

# ---------------------------
# Existing Twitter + Whisper logic
# ---------------------------

def getGuest(bearer):
    guestActivate = "https://api.twitter.com/1.1/guest/activate.json"
    res = requests.post(
        guestActivate,
        headers={"Authorization": f"Bearer {bearer}"}
    )
    return res.json()["guest_token"]

def getAudioSpaceGraphQl(bearer, g_token, space_id):
    url = "https://twitter.com/i/api/graphql/FJoTSHMVF7fMhGLc2t9cog/AudioSpaceById"
    variables = {
        "id": space_id,
        "isMetatagsQuery": False,
        "withSuperFollowsUserFields": False,
        "withUserResults": True,
        "withBirdwatchPivots": False,
        "withReactionsMetadata": False,
        "withReactionsPerspective": False,
        "withSuperFollowsTweetFields": False,
        "withScheduledSpaces": True
    }
    res = requests.get(
        url + "?variables=" + urllib.parse.quote(json.dumps(variables)),
        headers={"Authorization": f"Bearer {bearer}", "X-Guest-Token": g_token}
    )
    return res.json()

def getStreamInfo(bearer, g_token, media_id):
    url = (
        "https://twitter.com/i/api/1.1/live_video_stream/status/"
        f"{media_id}?client=web&use_syndication_guest_id=false&cookie_set_host=twitter.com"
    )
    res = requests.get(
        url,
        headers={"Authorization": f"Bearer {bearer}", "X-Guest-Token": g_token}
    )
    return res.json()

def getStreamingUrl(space_id):
    """
    Retrieves the Twitter Space HLS stream URL for the given space_id.
    """
    bearer = (
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs="
        "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
    )
    g_token = getGuest(bearer)
    asGraphQl = getAudioSpaceGraphQl(bearer, g_token, space_id)

    metadata = asGraphQl["data"]["audioSpace"]["metadata"]
    space_media_key = metadata["media_key"]

    streamInfo = getStreamInfo(bearer, g_token, space_media_key)
    streaming_url = streamInfo["source"]["location"]
    return metadata, streaming_url

def capture_aac_chunks(stream_url, aac_file, audio_queue):
    """
    1) Runs FFmpeg to capture raw AAC from the Twitter Space stream.
    2) Writes the entire audio to 'aac_file' on disk (just raw ADTS frames).
    3) Also pushes raw AAC data in small chunks to the audio_queue.
    """

    # FFmpeg #1: save raw AAC to disk
    disk_ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-loglevel", "quiet",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-c", "copy",
        "-f", "adts",
        aac_file
    ]
    disk_process = subprocess.Popen(
        disk_ffmpeg_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # FFmpeg #2: pipe raw AAC to stdout (same stream)
    stdout_ffmpeg_cmd = [
        "ffmpeg",
        "-loglevel", "quiet",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-c", "copy",
        "-f", "adts",
        "pipe:1"
    ]
    capture_process = subprocess.Popen(
        stdout_ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    chunk_size = 4096
    while True:
        chunk = capture_process.stdout.read(chunk_size)
        if not chunk:
            break
        audio_queue.put(chunk)

    audio_queue.put(None)
    capture_process.terminate()
    disk_process.terminate()

def transcribe_aac_chunk(aac_data, model, text_queue):
    """
    Writes 'aac_data' to a .aac file, then calls `model.transcribe(...)` on it.
    Instead of printing, we push the transcribed text onto `text_queue`.
    """
    if not aac_data:
        return
    with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tmp_aac:
        tmp_aac.write(aac_data)
        tmp_aac_path = tmp_aac.name

    try:
        result = model.transcribe(tmp_aac_path)
        text = result["text"].strip()
        if text:
            # Instead of print, publish text to the text_queue for our WebSocket server
            text_queue.put(text)
    finally:
        os.remove(tmp_aac_path)

def transcribe_audio(audio_queue, model, text_queue):
    """
    Accumulates raw AAC in memory. Once we have enough (8 seconds?), we transcribe.
    Instead of printing text, we add it to a text_queue.
    """
    CHUNK_ACCUM_SIZE = 8192 * 8
    accumulator = b""
    
    while True:
        chunk = audio_queue.get()
        if chunk is None:
            if accumulator:
                transcribe_aac_chunk(accumulator, model, text_queue)
            break

        accumulator += chunk
        if len(accumulator) >= CHUNK_ACCUM_SIZE:
            transcribe_aac_chunk(accumulator, model, text_queue)
            accumulator = b""

# ---------------------------
# New: WebSocket Publisher
# ---------------------------

# Shared queue that holds lines of transcribed text to send out to clients.
text_queue = queue.Queue()

# This is the main "server task" that each WebSocket client runs.
# For each connected client, we keep sending them anything that appears in `text_queue`.
async def websocket_handler(websocket, path):
    print("[WebSocket] Client connected.")
    while True:
        # Block until there's a new line of text
        text_line = text_queue.get()
        # Send it over to the connected client
        await websocket.send(text_line)

# Launch the WebSocket server in an asyncio event loop.
def start_websocket_server():
    async def main():
        async with websockets.serve(websocket_handler, "127.0.0.1", 6789):
            print("[WebSocket] Server started on ws://127.0.0.1:6789")
            # Keep the server alive forever:
            await asyncio.Future()

    asyncio.run(main())

# ---------------------------
# main() function
# ---------------------------

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 spaces.py <space_id>")
        sys.exit(1)

    space_id = sys.argv[1]
    metadata, streaming_url = getStreamingUrl(space_id)

    if metadata["state"] != "Running":
        print(f"Space not active (state: {metadata['state']}). Exiting.")
        sys.exit(0)

    aac_file = "output.aac"
    audio_queue = queue.Queue()

    # Decide on MPS vs CPU
    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        device = "mps"
    else:
        device = "cpu"

    model = LightningWhisperMLX(model="distil-large-v3", batch_size=24, quant=None)

    # Start the capturing thread
    capture_thread = threading.Thread(
        target=capture_aac_chunks,
        args=(streaming_url, aac_file, audio_queue),
        daemon=True
    )
    capture_thread.start()

    # Start the transcription thread
    transcribe_thread = threading.Thread(
        target=transcribe_audio,
        args=(audio_queue, model, text_queue),
        daemon=True
    )
    transcribe_thread.start()

    # Start the WebSocket server in a separate thread so it doesn't block.
    ws_server_thread = threading.Thread(target=start_websocket_server, daemon=True)
    ws_server_thread.start()

    print("[Main] All threads started. Press Ctrl+C to stop.")

    # Keep main thread alive until Ctrl+C
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("\nStopping...")

if __name__ == "__main__":
    main()
