# Echomorph

![Echomorph Demo](resources/demo.gif)

**Echomorph** is a Python library that enables developers to build AI-powered agents that integrate seamlessly with **Twitter Spaces** and the broader **Twitter** platform. By interacting with Twitter’s API and additional back-end services (like WebRTC for Spaces), Echomorph streamlines audio capture, publishing, and interactive automation.

## Features

- **AI Agent Integration**  
  Leverage AI-driven logic in real time within Twitter Spaces or traditional Twitter workflows.

- **Audio Management**  
  Send and receive audio data through WebRTC, including generating sound samples on the fly, capturing user audio, and forwarding it to AI models for analysis.

- **Plugin Architecture**  
  Extend functionality by attaching custom plugins (e.g., analytics, moderation, event logging). Each plugin can react to events like audio data, speaker requests, occupancy updates, and more.

- **Chat & Broadcast Orchestration**  
  Manage broadcast creation, real-time chat, speaker approvals, and user interaction using dedicated classes that handle the lower-level protocols (e.g., Janus WebRTC gateway, chat servers).

## Getting Started

### Prerequisites

- A recent version of Python (3.8+ recommended).
- Access to Twitter’s developer API (if you plan on using advanced Twitter functionalities).
- Familiarity with WebRTC concepts and Janus for audio/video publishing (optional but recommended).

### Installation

Clone or download the Echomorph repository. Then navigate into the project folder and install dependencies:

```bash
pip install -r requirements.txt
```

If you intend to use additional functionality (like advanced AI frameworks), install their respective libraries as well (e.g., `torch`, `tensorflow`, etc.).

### Repository Structure

```
echomorph/
│
├── README.md            # This file
├── requirements.txt     # Dependencies required for the scripts
├── model_processor/     # Contains the scripts used to process Twitter's API and RVC calls
├── agent-processor/     # Contains the TypeScript example used for model inferencing via ElizaOS
|   ├── client/          # Contains scripts used for interfacing with Twitter
│   ├── chat_client.py
│   │   ├── custom_janus_client.py
│   │   ├── authenticator.py
│   │   ├── space.py
│   │   └── test.py
|   ├── rvc/             # Contains scripts used for inferencing the RVC model
|   └── voice_node/      # Combines the above to act as the front facing voice node
└──

- **`janus_client.py`, `custom_janus_client.py`**  
  Handles Janus WebRTC session creation, media handling, and plugin attachment.

- **`chat_client.py`**  
  Connects to the Twitter Spaces chat service using WebSockets and emits events such as speaker requests and occupancy updates.

- **`authenticator.py`**  
  Manages authentication flows with Twitter/Periscope, returning cookies and tokens needed for broadcasting.

- **`tweeterpy.py`**  
  A helper class to log in and interface with Twitter. (Placeholder name/example.)

- **`space.py`**  
  Represents a running audio space session, handling broadcast creation, user plugins, and real-time events.

- **`test_space.py`**  
  Demonstrates how to use the library to create a Space, play a test beep, and stop gracefully after some time.
```

### Usage

Below is a simple example of creating a Space, playing a short audio beep, and then stopping:

```python
import asyncio
from tweeterpy import TweeterPy
from spaces import Space, SpaceConfig
from test_space import generate_beep

async def main():
    # Log in via TweeterPy (requires valid credentials)
    twitter_auth = TweeterPy()
    twitter_auth.login(username="YOUR_USERNAME", password="YOUR_PASSWORD", email="YOUR_EMAIL")

    # Create the space
    space = Space(twitter_auth)
    config = SpaceConfig(
        mode="INTERACTIVE",
        title="My AI-Driven Space",
        description="Testing Echomorph beep...",
        languages=["en"]
    )

    # Initialize and configure the broadcast
    await space.initialize(config)

    # (Optional) Send a beep every 5 seconds
    beep_data = generate_beep()
    for _ in range(6):
        space.push_audio(beep_data, 48000)
        print("Pushed beep!")
        await asyncio.sleep(5)

    # Clean up the space
    await space.stop()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(main())
```

### Contributing

1. Fork this repository.
2. Create your feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a pull request to merge into the main branch.

### License

This project is not officially affiliated with Twitter, Inc. or Twitter’s APIs and is offered “as is” without warranty. Check with Twitter’s Developer Agreement & Policy for usage rules.  
(You can specify an actual license such as [MIT License](https://opensource.org/licenses/MIT) here, if desired.)

### Disclaimer

- **Not an official Twitter product**  
  Echomorph is an independent tool and is neither endorsed nor supported by Twitter.

### Contact

For support, questions, or feedback, please open an issue on this repository or contact the maintainers directly.
