import os
import math
import array
import asyncio
import logging
import time

from tweeterpy import TweeterPy

from spaces import Space, SpaceConfig

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)


def generate_beep(sample_rate=48000, duration=0.2, frequency=440.0, amplitude=10000):
    """
    Generates a 16-bit mono PCM beep at the specified frequency and amplitude.
    Returns raw bytes suitable for push_audio().
    """
    num_samples = int(sample_rate * duration)
    beep_array = array.array('h', [0]*num_samples)  # 'h' = int16

    for i in range(num_samples):
        sample_val = amplitude * math.sin(2.0 * math.pi * frequency * (i / sample_rate))
        beep_array[i] = int(sample_val)

    return beep_array.tobytes()


async def main():
    # Replace these with real tokens or credentials
    email, username, password = os.getenv("TWITTER_EMAIL"), os.getenv("TWITTER_USERNAME"), os.getenv("TWITTER_PASSWORD")
    twitter = TweeterPy()
    twitter.login(username=username, password=password, email=email)

    # Create the Space instance
    space = Space(twitter)

    # Create a config
    config = SpaceConfig(
        mode="INTERACTIVE",
        title="Test Space with Python beep",
        description="Playing a beep every 5 seconds",
        languages=["en"]
    )

    # Initialize
    broadcast_info = await space.initialize(config)
    print("Broadcast info:", broadcast_info)

    # Example: beep every 5 seconds. We'll do it in a background task.
    async def beep_task():
        beep_data = generate_beep()  # 440Hz, 0.2s, 48kHz
        while True:
            space.push_audio(beep_data, 48000)
            print("Pushed beep!")
            await asyncio.sleep(5)

    # Start beep background task
    beep_job = asyncio.create_task(beep_task())

    # Let it run for 30 seconds, then stop
    await asyncio.sleep(30)
    beep_job.cancel()

    print("Stopping space...")
    await space.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

