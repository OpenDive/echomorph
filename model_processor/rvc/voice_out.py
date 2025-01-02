import torch
import soundfile as sf
from TTS.api import TTS
from rvc_python.infer import RVCInference

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

text = "Hello, world!"
language = "en"
audio = tts.tts(text, speaker="Andrew Chipper", language=language)
sf.write("tts_audio.wav", audio, 22050)

rvc = RVCInference(device="cuda:0")
rvc_model = rvc.load_model("weights/sbf.pth")
rvc_model.infer_file("tts_audio.wav", "voice.wav")
#sf.write("converted_audio.wav", converted_audio, 22050)
