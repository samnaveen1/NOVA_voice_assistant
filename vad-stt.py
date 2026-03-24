import torch
import torchaudio
import sounddevice as sd
from silero_vad import load_silero_vad, collect_chunks, VADIterator
import numpy as np
from collections import deque
import time
from scipy.io.wavfile import write
import speech_recognition as sr

# Configuration
SAMPLING_RATE = 16000  # Hertz
WINDOW_SIZE = 512      # Number of samples per window (32ms for 16kHz)
OVERLAP = 256          # Overlap between windows
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

start_time = 0
collected_chunks = None
is_recording = False
is_speaking = False

# Initialize Speech Recognition
r = sr.Recognizer()

# Initialize the Silero VAD model
# model = load_silero_vad(onnx=False).to(DEVICE)
model = load_silero_vad(onnx=True)

vad_iterator = VADIterator(model, sampling_rate=SAMPLING_RATE)

# Queue to hold audio chunks
audio_buffer = deque()

# Callback function for the audio stream
def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"Stream status: {status}", flush=True)
    # Convert bytes to float32 tensor
    audio_chunk = torch.from_numpy(indata[:, 0]).float()
    audio_buffer.append(audio_chunk)

def collect_recording(chunk):
    """Collect recording and save it to a file."""
    global collected_chunks
    
    if collected_chunks is None:
        collected_chunks = chunk
    else:
        collected_chunks = torch.cat([collected_chunks, chunk], dim=0)


# Function to process audio chunks
def process_audio():
    global start_time
    global is_speaking
    global collected_chunks
    previous_chunk = None
    is_saved = False
    just_started_speaking = False

    while True:
        if len(audio_buffer) == 0:
            sd.sleep(10)
            continue

        # Get the next audio chunk
        audio_chunk = audio_buffer.popleft().to(DEVICE)

        if just_started_speaking:
            just_started_speaking = False
            collect_recording(previous_chunk)
        
        if is_speaking:
            is_saved = False
            collect_recording(audio_chunk)

        # Save the audio chunk to a file
        sample_rate = 16000  # Replace with your actual sample rate
        
        if not is_speaking and collected_chunks is not None and not is_saved:
            final_audio_data = collected_chunks.numpy()
            final_audio_data = (final_audio_data * 32000).astype(np.int16)
            write("output.wav", sample_rate, final_audio_data)
            # recognize the audio using speech recognition library
            try:
                with sr.AudioFile("output.wav") as source:
                    r.adjust_for_ambient_noise(source)
                    audio_data = r.record(source)
                    text = r.recognize_google(audio_data)
                    collected_chunks = None
                    print(f"Recognized text: {text}")
            except Exception as e:
                print(f"Error recognizing audio: {e}")
                pass
            is_saved = True

        if previous_chunk is None:
            previous_chunk = audio_chunk
        else:
            previous_chunk = torch.cat([previous_chunk[-8000:], audio_chunk], dim=0)

        # VAD processing
        speech_dict = vad_iterator(audio_chunk, return_seconds=True)

        if speech_dict:
            if speech_dict.get('start'):
                print("Speech Started")
                is_speaking = True
                just_started_speaking = True
            # Use .get() method to safely retrieve values with a default
            start_time = speech_dict.get('start', start_time)
            end_time = speech_dict.get('end', None)
            
            # Check if both start and end times are available
            if start_time is not None and end_time is not None:
                is_speaking = False
                print(f"Speech detected from {start_time:.2f}s to {end_time:.2f}s")


# Start the audio stream
def main():
    print("Starting real-time VAD. Press Ctrl+C to stop.")
    try:
        with sd.InputStream(channels=1,
                            samplerate=SAMPLING_RATE,
                            blocksize=WINDOW_SIZE,
                            callback=audio_callback):
            process_audio()
    except KeyboardInterrupt:
        print("\nReal-time VAD stopped.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()