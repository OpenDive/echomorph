# ----- Standard Library Imports -----
import os
import math
import array
import asyncio
import logging
import time

# ----- Third-Party Imports -----
from dotenv import load_dotenv
from tweeterpy import TweeterPy

# ----- Local Imports -----
from spaces import Space, SpaceConfig

load_dotenv()
logging.basicConfig(level=logging.INFO)


def generate_beep(
    sample_rate: int = 48000,
    duration: float = 0.2,
    frequency: float = 440.0,
    amplitude: int = 10000,
) -> bytes:
    """
    Generate a 16-bit mono PCM beep at the specified frequency and amplitude.

    :param sample_rate: The audio sample rate in Hz.
    :param duration:    Duration of the beep in seconds.
    :param frequency:   Frequency of the sine wave in Hz.
    :param amplitude:   Amplitude of the wave (max ~32767 for int16).
    :return:            Raw bytes suitable for push_audio() calls.
    """
    num_samples = int(sample_rate * duration)
    beep_array = array.array("h", [0] * num_samples)  # 'h' = int16

    for i in range(num_samples):
        sample_val = amplitude * math.sin(
            2.0 * math.pi * frequency * (i / sample_rate)
        )
        beep_array[i] = int(sample_val)

    return beep_array.tobytes()


async def main():
    """
    Main coroutine for testing space creation and optionally playing a beep in the background.
    """
    # Replace these with real tokens or credentials from your environment
    email = os.getenv("TWITTER_EMAIL")
    username = os.getenv("TWITTER_USERNAME")
    password = os.getenv("TWITTER_PASSWORD")

    twitter = TweeterPy()
    twitter.login(username=username, password=password, email=email)

    # Create the Space instance
    space = Space(twitter)

    # Create the space configuration
    config = SpaceConfig(
        mode="INTERACTIVE",
        title="Test Space with Python beep",
        description="Playing a beep every 5 seconds",
        languages=["en"],
    )

    # Initialize the space
    broadcast_info = await space.initialize(config)

    # TODO: Implement beeping task
    """
    async def beep_task():
        beep_data = generate_beep()  # 440Hz, 0.2s duration, 48kHz
        while True:
            space.push_audio(beep_data, 48000)
            print("Pushed beep!")
            await asyncio.sleep(5)

    beep_job = asyncio.create_task(beep_task())

    # Let it run for 30 seconds, then stop
    await asyncio.sleep(30)
    beep_job.cancel()

    print("Stopping space...")
    await space.stop()
    print("Done.")
    """


if __name__ == "__main__":
    asyncio.run(main())

