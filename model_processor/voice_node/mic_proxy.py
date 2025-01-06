import pyaudio
from pydub import AudioSegment
from pydub.effects import normalize
import time

def play_mp3_to_virtual_mic(mp3_file_path, device_index, loop=True):
    # Load and convert the MP3 file to a standard format
    audio = AudioSegment.from_file(mp3_file_path, format="mp3")
    audio = audio.set_frame_rate(44100).set_channels(2).set_sample_width(2)  # Ensure 16-bit stereo, 44100 Hz
    # audio = audio.set_frame_rate(48000).set_channels(2).set_sample_width(2)  # Ensure 16-bit stereo, 44100 Hz

    audio = audio - 10  # Reduce volume by 5 dB

    # Get audio properties
    audio_data = audio.raw_data  # Access the raw audio data
    sample_width = audio.sample_width
    channels = audio.channels
    frame_rate = audio.frame_rate

    p = pyaudio.PyAudio()

    stream = p.open(
        format=p.get_format_from_width(sample_width),
        channels=channels,
        rate=frame_rate,
        output=True,
        output_device_index=device_index
    )

    print(f"Playing {mp3_file_path} to virtual microphone (Device Index: {device_index}). Press Ctrl+C to stop.")

    chunk_size = 512
    # chunk_size = 1024
    # chunk_size = 4096
    try:
        while True:  # Infinite loop for looping playback
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i+chunk_size]
                stream.write(chunk)  # Write audio data to the virtual microphone
            if not loop:
                break  # Exit loop if looping is disabled
    except KeyboardInterrupt:
        print("\nStopping playback...")
    finally:
        # Cleanup
        stream.stop_stream()
        stream.close()
        p.terminate()

# Example usage
# mp3_file = "./sbf_before_im_liquidated.mp3"  # Provide the path to your MP3 file
# virtual_device_index = 1  # Replace with the actual index of your virtual microphone
# play_mp3_to_virtual_mic(mp3_file, device_index=virtual_device_index, loop=True)
