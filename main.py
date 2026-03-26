import os
import time
import sys
import warnings
import re
from datetime import datetime, timezone
from uuid import uuid4
from dotenv import load_dotenv

import cv2  # For webcam access
import mss  # For screen capture
from PIL import Image  # For image manipulation

# Selenium imports for your new STT
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Gemini API
import google.generativeai as genai
from google.genai import types

# Hugging Face API (optional for future dual-provider mode)
import requests
import json

# Edge TTS
import edge_tts
import asyncio

# Pygame for audio playback
import pygame

# NEW IMPORTS for new tools
import pyautogui
import pywhatkit
from googlesearch import search
import base64
import io
import os.path
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pymongo import MongoClient

# --- Constants ---
# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send']

# Conversation Logging Constants
LOG_FILE = "conversation_log.txt"
MAX_LOG_SIZE_CHARS = 10000  # Max characters before summarization
MAX_HISTORY_LENGTH = 20  # Max turns to keep in memory for immediate context
SESSION_ID = str(uuid4())

# TTS Constants
VOICE = "en-US-JennyNeural"
# Not directly used in current play_audio, but good to keep if streaming was implemented.
BUFFER_SIZE = 1024

# --- Configuration ---
load_dotenv()
# IMPORTANT: Load API key from environment variable, do NOT hardcode it here.
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
HF_API_KEY = os.getenv("HF_API_KEY")
HF_MODEL = os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "Qwen/Qwen3.5-9B")
ACTIVE_PROVIDER = os.getenv("ACTIVE_PROVIDER", "auto").strip().lower()
MONGO_LOG_ENABLED = os.getenv("MONGO_LOG_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "y", "on"
}
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "nova_voice_ai")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "conversation_events")
USE_VOICE_INPUT = os.getenv("USE_VOICE_INPUT", "true").strip().lower() in {
    "1", "true", "yes", "y", "on"
}

if ACTIVE_PROVIDER not in {"auto", "gemini", "huggingface"}:
    ACTIVE_PROVIDER = "auto"

if ACTIVE_PROVIDER == "auto":
    if GOOGLE_API_KEY:
        ACTIVE_PROVIDER = "gemini"
    elif HF_API_KEY:
        ACTIVE_PROVIDER = "huggingface"

if ACTIVE_PROVIDER == "gemini" and not GOOGLE_API_KEY:
    if HF_API_KEY:
        print("\033[93mGOOGLE_API_KEY missing. Falling back to Hugging Face mode.\033[0m")
        ACTIVE_PROVIDER = "huggingface"
    else:
        print("Error: GOOGLE_API_KEY not found in .env file.")
        sys.exit(1)

if ACTIVE_PROVIDER == "huggingface" and not HF_API_KEY:
    if GOOGLE_API_KEY:
        print("\033[93mHF_API_KEY missing. Falling back to Gemini mode.\033[0m")
        ACTIVE_PROVIDER = "gemini"
    else:
        print("Error: Set GOOGLE_API_KEY or HF_API_KEY in .env file.")
        sys.exit(1)

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"

# --- MongoDB Logging ---
mongo_client = None
mongo_collection = None


def _init_mongo_logging():
    global mongo_client, mongo_collection
    if not MONGO_LOG_ENABLED:
        print("\033[93mMongoDB logging disabled by configuration.\033[0m")
        return

    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        mongo_client.admin.command("ping")
        mongo_collection = mongo_client[MONGO_DB_NAME][MONGO_COLLECTION_NAME]
        print(
            f"\033[94mMongoDB logging enabled: {MONGO_DB_NAME}.{MONGO_COLLECTION_NAME}\033[0m"
        )
    except Exception as e:
        mongo_client = None
        mongo_collection = None
        print(f"\033[93mMongoDB unavailable, continuing with file logs only: {e}\033[0m")


def _log_to_mongo(content: str, sender: str | None = None):
    if mongo_collection is None:
        return
    try:
        mongo_collection.insert_one({
            "session_id": SESSION_ID,
            "timestamp": datetime.now(timezone.utc),
            "sender": sender or "system",
            "content": content,
            "provider": ACTIVE_PROVIDER,
            "model": ACTIVE_MODEL,
        })
    except Exception as e:
        print(f"\033[93mMongoDB log write failed: {e}\033[0m")

# --- Initialize Gemini Model (SINGLE INSTANCE) ---
SYSTEM_PROMPT = """
<purpose>
    Your purpose is to act as 'Nova', a visionary AI assistant designed to empower blind and disabled individuals by being their eyes and hands in the digital and physical world. You are a highly capable, empathetic, and patient personal assistant. Your primary goal is to enhance independence, improve interaction with digital devices, and facilitate communication.
</purpose>

<instructions>
    <instruction>
        **Persona and Tone:** Embody 'Nova'. Be empathetic, patient, and empowering. Your tone must always be clear, concise, and direct.
    </instruction>
    <instruction>
        **Core Communication Rule:** Get straight to the point. Absolutely no conversational filler, preambles, or extra sentences (e.g., "Of course, I can help with that," or "Here is the information you requested.").
    </instruction>
    <instruction>
        **Formatting Constraint:** You MUST NOT use any markdown formatting. No bolding, italics, lists, or code blocks. All responses must be plain english text suitable for a screen reader.
    </instruction>
    <instruction>
        **Safety and Confirmation Protocol:** This is a critical instruction.
        - For non-sensitive requests to describe the screen or surroundings (via webcam), sending a message, you MUST directly and immediately use the appropriate tool without asking for confirmation.
        - For highly sensitive actions (e.g., making a call, modifying system settings), you MUST ask for explicit, simple verbal confirmation from the user before proceeding.
    </instruction>
    <instruction>
        **Task Execution:**
        - Understand and respond to spoken commands in English.
        - Adapt to variations in user speech (e.g., recognize "mum" as "mom").
        - When asked to compose messages or emails, you can infer and create the subject and body based on the user's request.
        - Perform Google searches and provide concise summaries of the results.
        - Control applications and browse the web as directed.
    </instruction>
    <instruction>
        **Error and Ambiguity Handling:**
        - If a request is ambiguous, ask a short, direct clarifying question.
        - If you encounter an error or cannot perform a task, explain the limitation clearly and offer a viable alternative if one exists.
    </instruction>
</instructions>
"""

def _list_generate_content_models() -> list[str]:
    """Return available model names that support generateContent."""
    available_models: list[str] = []
    try:
        for model_info in genai.list_models():
            methods = getattr(model_info, "supported_generation_methods", []) or []
            if "generateContent" not in methods:
                continue
            model_name = getattr(model_info, "name", "")
            if model_name.startswith("models/"):
                model_name = model_name.split("/", 1)[1]
            if model_name:
                available_models.append(model_name)
    except Exception as e:
        print(f"\033[93mWarning: Could not list Gemini models: {e}\033[0m")
    return available_models


VISION_SYSTEM_PROMPT = """
You are Nova, an accessibility assistant for blind users. When describing screen content:
1. FOCUS ONLY on the main content area - ignore sidebars, toolbars, tabs, and navigation menus
2. Describe in logical reading order: headings, body text, important controls, buttons, forms
3. Be extremely concise - 2-3 sentences maximum for simple pages, 4-5 for complex ones
4. Skip decorative elements, logos, background images
5. Highlight interactive elements (links, buttons, input fields) by name and purpose
6. Use plain language, no technical jargon
7. If the page is mostly navigation/chrome, say 'This page contains primarily navigation controls'
"""


def _build_model_with_fallback(system_prompt: str):
    """Create model instance using configured model or best available fallback."""
    preferred_models = [
        GEMINI_MODEL,
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
    ]

    available_models = _list_generate_content_models()
    selected_model = GEMINI_MODEL

    if available_models:
        selected_model = next(
            (m for m in preferred_models if m in available_models),
            available_models[0]
        )
        if selected_model != GEMINI_MODEL:
            print(
                f"\033[93mConfigured model '{GEMINI_MODEL}' not available. "
                f"Using '{selected_model}' instead.\033[0m"
            )

    model_instance = genai.GenerativeModel(
        selected_model,
        system_instruction=system_prompt
    )
    return model_instance, selected_model


if ACTIVE_PROVIDER == "gemini":
    model, ACTIVE_MODEL = _build_model_with_fallback(SYSTEM_PROMPT)
else:
    model = None
    ACTIVE_MODEL = HF_MODEL

_init_mongo_logging()


def _extract_retry_seconds(error_text: str) -> int | None:
    """Parse retry delay from error text if present."""
    match = re.search(r"Please retry in\s+([0-9]+(?:\.[0-9]+)?)s", error_text)
    if not match:
        return None
    try:
        return int(float(match.group(1)))
    except ValueError:
        return None


def _is_quota_error(error_text: str) -> bool:
    """Detect quota/rate-limit style errors from Gemini responses."""
    normalized = error_text.lower()
    return (
        "429" in normalized
        or "quota exceeded" in normalized
        or "rate limit" in normalized
        or "generate_content_free_tier" in normalized
    )


def _is_model_not_found_error(error_text: str) -> bool:
    """Detect invalid/unsupported model errors from Gemini responses."""
    normalized = error_text.lower()
    return (
        "404" in normalized
        and (
            "is not found" in normalized
            or "not supported for generatecontent" in normalized
        )
    )


def _is_screen_description_command(user_text: str) -> bool:
    """Return True when user is clearly asking to describe on-screen content."""
    normalized = user_text.lower().strip()
    screen_keywords = [
        "screen",
        "display",
        "monitor",
        "on my screen",
        "in my screen",
        "what is on my screen",
    ]
    describe_keywords = ["describe", "what", "read", "tell", "see"]
    return any(k in normalized for k in screen_keywords) and any(k in normalized for k in describe_keywords)


def _hf_generate_text(prompt: str, max_tokens: int = 512) -> str:
    if not HF_API_KEY:
        return "Hugging Face API key is missing."

    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {
        "model": HF_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    try:
        response = requests.post(HF_ROUTER_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = (message.get("content") or message.get("reasoning") or "").strip()
            return content or "No response from model."
        return "No response from model."
    except Exception as e:
        return f"Hugging Face request failed: {e}"


def _hf_describe_image(pil_image: Image.Image, user_query: str) -> str:
    if not HF_API_KEY:
        return "Hugging Face API key is missing."

    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode()

    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {
        "model": HF_VISION_MODEL,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_query},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }

    try:
        response = requests.post(HF_ROUTER_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            text = (message.get("content") or message.get("reasoning") or "").strip()
            return text or "I could not describe the image."
        return "I could not describe the image."
    except Exception as e:
        return f"Image description failed: {e}"

# --- Helper Functions ---


def log_message(content: str, sender: str = None):
    """Appends a timestamped message to the conversation log file."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if sender:
            f.write(f"{sender}: {content}\n")
        else:
            f.write(f"{content}\n")
    _log_to_mongo(content, sender)


async def summarize_conversation_log(current_log_content: str) -> str:
    """Uses Gemini to summarize the current conversation log content."""
    summary_prompt = (
        "Summarize the following conversation concisely, focusing on key topics, "
        "decisions, and important information discussed. This summary will be used "
        "as context for future interactions. Do not include any conversational filler "
        "or preambles. Just the summary. Aim for under 500 characters."
        f"\n\nConversation:\n{current_log_content}"
    )
    try:
        if ACTIVE_PROVIDER == "huggingface":
            summary = _hf_generate_text(summary_prompt, max_tokens=256).strip()
            print(f"\033[95mConversation summarized. New context established.\033[0m")
            return summary

        summary_response = await model.generate_content(
            summary_prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.0, max_output_tokens=2000)  # Max tokens for summary
        )
        summary = summary_response.text.strip()
        print(f"\033[95mConversation summarized. New context established.\033[0m")
        return summary
    except Exception as e:
        print(f"\033[91mError summarizing conversation: {e}\033[0m")
        log_message("error", f"Error summarizing conversation: {e}")
        return "Failed to summarize previous conversation."


# --- Gmail API Functions ---
def get_gmail_service():
    """Shows basic usage of the Gmail API.
    Lists the user's Gmail labels.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except HttpError as error:
        print(f'An error occurred with Gmail authentication: {error}')
        return None


def send_gmail_message(recipient_name: str, subject: str, message_text: str) -> str:
    """
    Sends an email message using the Gmail API.

    Args:
        recipient_name: Name of the contact (e.g., "Papa", "Alice"). This will be mapped to an email address.
        subject: The subject of the email.
        message_text: The text of the email.

    Returns:
        A status message indicating success or failure.
    """
    # Contact mapping for Gmail
    email_contact_map = {
        "papa": "papa@gmail.com",  # Replace with actual email
        "mom": "mom@example.com",    # Replace with actual email
        # Add more contacts here
    }

    recipient_email = email_contact_map.get(recipient_name.lower())

    if not recipient_email:
        return f"Error: Contact '{recipient_name}' not found in my known contacts. Please provide a valid contact name."

    try:
        service = get_gmail_service()
        if not service:
            return "Failed to authenticate with Gmail. Please check your credentials."

        message = MIMEText(message_text)
        message['to'] = recipient_email
        message['subject'] = subject

        create_message = {'raw': base64.urlsafe_b64encode(
            message.as_bytes()).decode()}

        send_message = service.users().messages().send(
            userId="me", body=create_message).execute()

        return f'Email sent successfully to {recipient_name} ({recipient_email}). Message Id: {send_message["id"]}'
    except HttpError as error:
        return f'An error occurred while sending email: {error}'
    except Exception as e:
        return f'An unexpected error occurred: {str(e)}'


def read_gmail_messages(max_results: int = 5) -> str:
    """
    Retrieves the latest emails from the user's Gmail inbox.

    Args:
        max_results: Maximum number of emails to retrieve (default: 5).

    Returns:
        A formatted string containing the email subjects and senders.
    """
    try:
        service = get_gmail_service()
        if not service:
            return "Failed to authenticate with Gmail. Please check your credentials."

        results = service.users().messages().list(
            userId='me',
            labelIds=['INBOX'],
            maxResults=max_results
        ).execute()

        messages = results.get('messages', [])

        if not messages:
            return 'No messages found.'

        result = 'Latest emails:\n'
        for message in messages:
            msg = service.users().messages().get(
                userId='me',
                id=message['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject']
            ).execute()

            headers = msg.get('payload', {}).get('headers', [])
            sender = next(
                (h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
            subject = next(
                (h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')

            result += f'From: {sender}\n'
            result += f'Subject: {subject}\n'
            result += '---\n'
        return result
    except HttpError as error:
        return f'An error occurred while reading emails: {error}'
    except Exception as e:
        return f'An unexpected error occurred: {str(e)}'


# --- Speech-to-Text Listener Class ---
class SpeechToTextListener:
    """A class for performing speech-to-text using a web-based service."""

    def __init__(
            self,
            website_path: str = "https://realtime-stt-devs-do-code.netlify.app/",
            language: str = "en-US",
            wait_time: int = 10):
        """Initializes the STT class with the given website path and language."""
        self.website_path = website_path
        self.language = language
        self.chrome_options = Options()
        self.chrome_options.add_argument("--use-fake-ui-for-media-stream")
        self.chrome_options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3")
        self.chrome_options.add_argument("--headless=new")
        self.driver = webdriver.Chrome(service=webdriver.ChromeService(
            ChromeDriverManager().install()), options=self.chrome_options)
        self.wait = WebDriverWait(self.driver, wait_time)
        self.last_stt_text = ""  # Corrected: Initialized once here
        print("Made By ❤️ @DevsDoCode")

    def stream(self, content: str):
        """Prints the given content to the console with a yellow color, overwriting previous output, with "speaking..." added."""
        print("\033[96m\rUser Speaking: \033[93m" +
              f" {content}", end='', flush=True)

    def get_text(self) -> str:
        """Retrieves the transcribed text from the website."""
        try:
            return self.wait.until(EC.presence_of_element_located((By.ID, "convert_text"))).text
        except Exception:  # More specific catch for TimeoutException could be added
            return ""

    def select_language(self):
        """Selects the language from the dropdown using JavaScript."""
        self.driver.execute_script(
            f"""
            var select = document.getElementById('language_select');
            select.value = '{self.language}';
            var event = new Event('change');
            select.dispatchEvent(event);
            """
        )

    def verify_language_selection(self):
        """Verifies if the language is correctly selected."""
        language_select = self.driver.find_element(By.ID, "language_select")
        selected_language = language_select.find_element(
            By.CSS_SELECTOR, "option:checked").get_attribute("value")
        return selected_language == self.language

    def main_stt_process(self):
        """Performs speech-to-text conversion and returns the transcribed text."""
        self.driver.get(self.website_path)

        self.wait.until(EC.presence_of_element_located(
            (By.ID, "language_select")))

        self.select_language()

        if not self.verify_language_selection():
            actual_selected = self.driver.find_element(By.ID, "language_select").find_element(
                By.CSS_SELECTOR, "option:checked").get_attribute("value")
            print(
                f"Error: Failed to select the correct language. Selected: {actual_selected}, Expected: {self.language}")
            return None

        self.driver.find_element(By.ID, "click_to_record").click()

        is_recording = self.wait.until(
            EC.presence_of_element_located((By.ID, "is_recording"))
        )

        print("\033[94m\rListening...", end='', flush=True)
        start_time = time.time()
        max_listen_time = 30  # seconds
        last_text_time = time.time()
        silence_timeout = 5  # seconds of silence to consider input finished

        while is_recording.text.startswith("Recording: True") and (time.time() - start_time < max_listen_time):
            text = self.get_text()
            if text:
                if text != self.last_stt_text:
                    self.stream(text)
                    self.last_stt_text = text
                    last_text_time = time.time()

            if time.time() - last_text_time > silence_timeout and len(text.strip()) > 0:
                print(
                    f"\n\033[94mDetected silence for {silence_timeout} seconds. Stopping listening.\033[0m", flush=True)
                break

            time.sleep(0.1)

        final_text = self.get_text()
        print("\r" + " " * (len(final_text) + 25) + "\r", end="", flush=True)
        return final_text

    def listen(self, prints: bool = False):
        # self.last_stt_text = "" # Removed: Initialized in __init__
        try:
            while True:
                result = self.main_stt_process()
                if result and len(result.strip()) > 0:
                    if prints:
                        print("\033[92m\rYOU SAID: " + f"{result}\033[0m\n")
                    return result
                else:
                    print(
                        "\033[91mNo speech detected or recognized. Please try again.\033[0m")
                    time.sleep(0.5)
        except Exception as e:
            print(f"\n\033[91mError in STT listener: {e}\033[0m")
            return None

    def close(self):
        if self.driver:
            self.driver.quit()
            print("Selenium WebDriver for STT closed.")


# Global instance of STT Listener
stt_listener = None


# --- Pygame-based TTS Function ---
def remove_file(file_path):
    max_attempts = 8
    attempts = 0
    while attempts < max_attempts:
        try:
            if pygame.mixer.get_init():
                try:
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                except Exception:
                    pass
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
            if os.path.exists(file_path):
                os.remove(file_path)
            break
        except Exception as e:
            print(f"Error removing file '{file_path}': {e}")
            attempts += 1
            time.sleep(0.25)


async def generate_tts(TEXT, output_file):
    try:
        print("\033[92mGenerating TTS...\033[0m")
        cm_txt = edge_tts.Communicate(TEXT, VOICE)
        await cm_txt.save(output_file)
        print("\033[94mTTS Generation Complete.\033[0m")
    except Exception as e:
        print(f"\033[91mError during TTS generation: {e}\033[0m")


def play_audio(file_path):
    print("\033[92mPlaying audio...\033[0m")
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        pygame.mixer.quit()
    except Exception as e:
        print(f"\033[91mError playing audio with Pygame: {e}\033[0m")
        if pygame.mixer.get_init():
            pygame.mixer.quit()


async def speak(TEXT):
    output_file = "output.mp3"

    remove_file(output_file)

    await generate_tts(TEXT, output_file)

    if os.path.exists(output_file):
        play_audio(output_file)
    else:
        print(
            "\033[91mOutput MP3 file not found after TTS generation. Cannot play.\033[0m")

    remove_file(output_file)

# --- Vision Capture Functions ---


def capture_webcam_image():
    """Captures an image from the webcam and returns it as a PIL Image object.
    Returns None if capture fails.
    """
    cap = cv2.VideoCapture(0)  # 0 is typically the default webcam
    if not cap.isOpened():
        print(
            "\033[91mError: Could not open webcam. Make sure it's not in use by another application.\033[0m")
        return None

    ret, frame = cap.read()
    cap.release()  # Release the webcam immediately

    if ret:
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(img_rgb)
        print("\033[94mWebcam image captured as PIL Image object.\033[0m")
        return pil_image
    else:
        print("\033[91mError: Could not read frame from webcam.\033[0m")
        return None


def capture_screen_image():
    """Captures a screenshot of the primary monitor and returns it as a PIL Image object.
    Returns None if capture fails.
    """
    try:
        # print(f"Current working directory for screenshot debug: {os.getcwd()}") # Removed debug print
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            sct_img = sct.grab(monitor)
            pil_image = Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            print("\033[94mScreenshot captured as PIL Image object.\033[0m")
            return pil_image
    except Exception as e:
        print(f"\033[91mError capturing screenshot: {e}\033[0m")
        return None

# --- Gemini Tools ---


async def describe_webcam_view(user_query: str) -> str:
    """Captures an image from the webcam, sends it to Gemini for description, and returns the raw analysis result.
    This tool is used when the user asks about their physical surroundings, what is in front of them, or what they see.
    """
    print("\033[93mAI is preparing to capture webcam view and describe it...\033[0m")
    pil_image = capture_webcam_image()

    if pil_image:
        try:
            if ACTIVE_PROVIDER == "huggingface":
                return _hf_describe_image(pil_image, user_query)

            # Rephrased prompt to avoid awkwardness with user_query
            contents_with_image = [
                f"Analyze this webcam image and provide a short and concise description focusing on {user_query}.",
                pil_image
            ]

            response = model.generate_content(
                contents=contents_with_image,
                generation_config=genai.GenerationConfig(temperature=0.0)
            )

            if response.text:
                print("\033[94mWebcam analysis completed.\033[0m")
                return response.text.strip()
            else:
                print(
                    "\033[91mGemini did not return a text description for the webcam image.\033[0m")
                return "I captured an image, but couldn't get a description from Gemini."
        except Exception as e:
            print(f"\033[91mError sending webcam image to Gemini: {e}\033[0m")
            return "I captured an image, but encountered an error while analyzing it."
    else:
        return "I was unable to capture an image from the webcam."


async def describe_screen_content(user_query: str) -> str:
    """Captures a screenshot of the current screen, sends it to Gemini for description, and returns the raw analysis result.
    This tool is used when the user asks about what's on their screen, what is displayed, or what their device shows.
    """
    print(
        "\033[93mAI is preparing to capture screen content and describe it...\033[0m")
    pil_image = capture_screen_image()

    if pil_image:
        try:
            if ACTIVE_PROVIDER == "huggingface":
                return _hf_describe_image(pil_image, user_query)

            # Use vision system prompt with user query
            prompt = (
                f"User request: {user_query}\n\n"
                "Analyze this screen and describe ONLY the main content area. "
                "Ignore navigation menus, sidebars, toolbars, and tabs. "
                "Be extremely concise (2-5 sentences max). "
                "List important headings and interactive elements."
            )
            contents_with_image = [prompt, pil_image]

            response = model.generate_content(
                contents=contents_with_image,
                generation_config=genai.GenerationConfig(temperature=0.0)
            )

            if response.text:
                print("\033[94mScreen analysis completed.\033[0m")
                return response.text.strip()
            else:
                print(
                    "\033[91mGemini did not return a text description for the screen image.\033[0m")
                return "I captured your screen, but couldn't get a description from Gemini."
        except Exception as e:
            print(f"\033[91mError sending screen image to Gemini: {e}\033[0m")
            return "I captured your screen, but encountered an error while analyzing it."
    else:
        return "I was unable to capture your screen."


async def send_whatsapp_message(recipient_name: str, message_content: str) -> str:
    """Sends a WhatsApp message to a specified recipient.
    Args:
        recipient_name: The name of the contact (e.g., "Papa", "Alice"). This will be mapped to a phone number.
        message_content: The message to send.
    Returns:
        A status message indicating success or failure.
    """
    phone_number_map = {
        "papa": "+9112321321",  # Replace with actual number
        "mom": "+911231565",    # Replace with actual number
        # Add more contacts here
    }

    phone_no = phone_number_map.get(recipient_name.lower())

    if not phone_no:
        return f"Error: Contact '{recipient_name}' not found in my known contacts. Please provide a valid contact name."

    try:
        print(
            f"\033[93mAttempting to send WhatsApp message to {recipient_name} ({phone_no}): '{message_content}'\033[0m")
        pywhatkit.sendwhatmsg_instantly(
            phone_no=phone_no, message=message_content, wait_time=9, tab_close=True, close_time=2)
        return f"WhatsApp message sent to {recipient_name}."
    except Exception as e:
        return f"Failed to send WhatsApp message to {recipient_name}. Error: {e}"


async def search_web(query: str) -> str:
    """Performs a Google search and returns the search results."""
    try:
        print(f"\033[93mSearching for: {query}\033[0m")
        search_results = search(query, advanced=True)
        final_result = ""
        result_count = 0
        for result in search_results:
            if result_count >= 5:  # Limit to first 5 results for efficiency
                break
            final_result += f"Title: {result.title}\nDescription: {result.description}\nURL: {result.url}\n\n"
            result_count += 1
        return final_result if final_result.strip() else "No search results found for your query."
    except Exception as e:
        return f"Error performing web search: {e}"


async def call_whatsapp_contact(person_name: str, call_type: str = 'voice'):
    """
    Initiates a WhatsApp voice or video call to the specified contact using UI automation.
    Args:
        person_name: The name of the contact (e.g., "Papa", "Mom").
        call_type: 'voice' or 'video'.
    Returns:
        A status message indicating the action was triggered.
    """
    try:
        print(
            f"\033[93mAttempting to initiate a WhatsApp {call_type} call to {person_name}...\033[0m")

        # Press Windows key
        pyautogui.press('win')
        await asyncio.sleep(0.5)

        # Type 'whatsapp'
        pyautogui.write('whatsapp')
        await asyncio.sleep(0.5)

        # Press Enter to open WhatsApp
        pyautogui.press('enter')
        await asyncio.sleep(4)  # Increased wait for WhatsApp to fully open

        # Press Ctrl+F to search within WhatsApp
        pyautogui.hotkey('ctrl', 'f')
        await asyncio.sleep(0.5)

        # Clear existing search text (select all and backspace)
        pyautogui.hotkey('ctrl', 'a')
        await asyncio.sleep(0.2)
        pyautogui.press('backspace')
        await asyncio.sleep(0.2)

        # Type the person's name
        pyautogui.write(person_name, interval=0.1)
        await asyncio.sleep(1.5)  # Wait for search results to appear

        # Click on the first search result (assuming it's the correct contact)
        # These coordinates are highly sensitive and depend on screen resolution and WhatsApp UI.
        # You might need to calibrate these (e.g., using `pyautogui.displayMousePosition()`)
        pyautogui.click(347, 260)  # Example coordinate, adjust for your setup!
        await asyncio.sleep(1)

        # Click on call button based on type
        # These coordinates are also highly sensitive and depend on UI.
        if call_type == 'voice':
            pyautogui.click(1814, 101)  # Voice call button (example coord)
        elif call_type == 'video':
            pyautogui.click(1755, 103)  # Video call button (example coord)
        else:
            raise ValueError('call_type must be "voice" or "video"')
        await asyncio.sleep(1)

        return f'WhatsApp {call_type} call initiated to {person_name}. Please be ready to interact with the call window on your screen.'

    except Exception as e:
        return f"Failed to initiate WhatsApp call to {person_name}. Error: {e}"


AVAILABLE_TOOLS = [
    describe_webcam_view,
    describe_screen_content,
    send_whatsapp_message,
    search_web,
    send_gmail_message,
    read_gmail_messages,
    call_whatsapp_contact,
]
# --- Main Conversation Loop ---


async def main_conversation_loop():
    print("Nova: Hello! How can I assist you today? (Say 'exit' to quit)")
    print(f"\033[94mActive provider: {ACTIVE_PROVIDER} ({ACTIVE_MODEL})\033[0m")
    await speak("Hello! How can I assist you today!")

    if USE_VOICE_INPUT and stt_listener:
        print("\033[94mVoice mode enabled. Speak your command.\033[0m")
    elif USE_VOICE_INPUT and not stt_listener:
        print("\033[93mVoice mode requested, but STT listener is unavailable. Falling back to keyboard input.\033[0m")
    else:
        print("\033[94mKeyboard mode enabled. Type your command.\033[0m")

    # Ensure log file exists
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")  # Create empty file

    conversation_history = []

    # Load initial context from log file if it exists and is large
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            initial_log_content = f.read()
        if len(initial_log_content) > MAX_LOG_SIZE_CHARS:
            print(
                "\033[95mInitial conversation log exceeding limit. Summarizing...\033[0m")
            summary_text = await summarize_conversation_log(initial_log_content)
            with open(LOG_FILE, "w", encoding="utf-8") as f:  # Overwrite with summary
                log_message(summary_text, sender="Nova")
            conversation_history.append({"role": "user", "parts": [
                                        {"text": f"Previous conversation summary: {summary_text}"}]})
        else:  # If not too large, just load it into history as a single block for initial context
            conversation_history.append({"role": "user", "parts": [
                                        {"text": f"Previous conversation log: {initial_log_content}"}]})

    while True:
        if USE_VOICE_INPUT and stt_listener:
            user_input = stt_listener.listen(prints=True)
            if user_input is None:
                print("\033[93mSTT failed for this turn. Please type your command.\033[0m")
                user_input = input(">>> ")
        else:
            user_input = input(">>> ")

        if user_input is None or user_input.lower() == "exit":
            log_message("system", "User exited conversation.")
            print("Nova: Goodbye!")
            await speak("Goodbye!")
            break

        if not user_input.strip():
            continue

        # Log user input to the file
        log_message(user_input, "User")

        # Deterministic shortcut: directly handle common accessibility command.
        if _is_screen_description_command(user_input):
            screen_result = await describe_screen_content(user_input)
            print(screen_result)
            await speak(screen_result)
            log_message(screen_result, "Nova")
            conversation_history.append(
                {"role": "user", "parts": [{"text": user_input}]}
            )
            conversation_history.append(
                {"role": "model", "parts": [{"text": screen_result}]}
            )
            continue

        # Add user input to conversation history for the current turn
        conversation_history.append(
            {"role": "user", "parts": [{"text": user_input}]})

        # Trim conversation history to manage in-memory context window
        if len(conversation_history) > MAX_HISTORY_LENGTH:
            conversation_history = conversation_history[-(MAX_HISTORY_LENGTH):]

        if ACTIVE_PROVIDER == "huggingface":
            hf_prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"Recent context: {conversation_history[-4:]}\n\n"
                f"User: {user_input}\nAssistant:"
            )
            hf_response_text = _hf_generate_text(hf_prompt, max_tokens=512).strip()
            if not hf_response_text:
                hf_response_text = "I'm sorry, I couldn't generate a response."

            print(hf_response_text)
            await speak(hf_response_text)
            log_message(hf_response_text, "Nova")
            conversation_history.append(
                {"role": "model", "parts": [{"text": hf_response_text}]}
            )
            continue

        try:
            response = model.generate_content(
                contents=conversation_history,
                tools=AVAILABLE_TOOLS,
                generation_config=genai.GenerationConfig(temperature=0.6)
            )

            if response.candidates and response.candidates[0].content.parts:
                tool_calls_to_execute = []
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        tool_calls_to_execute.append(part.function_call)
                        log_message(
                            "model_tool_call", f"Requested tool: {part.function_call.name} with args: {part.function_call.args}")

                if tool_calls_to_execute:
                    print(
                        "\033[93mGemini requested tool calls. Executing...\033[0m")

                    tool_results_list = []  # Store results to send back to model

                    for tool_call in tool_calls_to_execute:
                        tool_name = tool_call.name
                        current_tool_args = {}
                        if tool_call.args:
                            for key, value in tool_call.args.items():
                                current_tool_args[key] = value

                        called_function = next(
                            (f for f in AVAILABLE_TOOLS if f.__name__ == tool_name), None)

                        if called_function:
                            # Pass user_input if the tool expects it for context (e.g., vision tools)
                            if 'user_query' in called_function.__code__.co_varnames:
                                current_tool_args['user_query'] = user_input

                            if asyncio.iscoroutinefunction(called_function):
                                tool_result_text = await called_function(**current_tool_args)
                            else:
                                tool_result_text = called_function(
                                    **current_tool_args)

                            print(
                                f"\033[93mTool '{tool_name}' executed. Result: {tool_result_text}\033[0m")
                            log_message(tool_result_text, tool_name)
                            tool_results_list.append(tool_result_text)

                            # Add tool call and response to conversation history
                            conversation_history.append({
                                "role": "model",
                                "parts": [{
                                    "function_call": {
                                        "name": tool_name,
                                        "args": current_tool_args
                                    }
                                }]
                            })
                            conversation_history.append({
                                "role": "function",
                                "parts": [{
                                    "function_response": {
                                        "name": tool_name,
                                        "response": {"type": "text", "text": tool_result_text}
                                    }
                                }]
                            })
                        else:
                            error_message = f"I'm sorry, I don't know how to perform the action '{tool_name}'."
                            print(
                                f"\033[91mError: Unknown tool '{tool_name}' requested by Gemini.\033[0m")
                            log_message(
                                "error", f"Unknown tool requested: {tool_name}")
                            tool_results_list.append(error_message)
                            conversation_history.append(
                                {"role": "model", "parts": [{"text": error_message}]})

                    # If tool results were generated, send them back to model for final response
                    if tool_results_list:
                        print(
                            "\033[93mSending tool results back to model for processing...\033[0m")

                        final_response_from_model = model.generate_content(
                            contents=conversation_history,  # Send entire updated history
                            generation_config=genai.GenerationConfig(
                                temperature=0.0)  # Low temperature for factual summarization of tool results
                        )

                        final_text_response = ""
                        if final_response_from_model.candidates and final_response_from_model.candidates[0].content.parts:
                            for part in final_response_from_model.candidates[0].content.parts:
                                if part.text:
                                    final_text_response += part.text

                        if final_text_response.strip():
                            print(final_text_response)
                            await speak(final_text_response)
                            log_message(final_text_response, "Nova")
                            conversation_history.append(
                                {"role": "model", "parts": [{"text": final_text_response}]})
                        else:
                            await speak("I performed the requested action successfully, but I have no further details to add.")
                            log_message(
                                "Action performed, no further details.", "Nova")
                            conversation_history.append(
                                {"role": "model", "parts": [{"text": "Action performed, no further details."}]})
                    else:  # This path should ideally not be hit if tool_calls_to_execute was not empty
                        await speak("I performed an action, but there was no direct response.")
                        log_message(
                            "Action performed, no direct response.", "Nova")
                        conversation_history.append(
                            {"role": "model", "parts": [{"text": "Action performed, no direct response."}]})

                else:  # Gemini provided a direct text response (no tool calls)
                    gemini_text_response = ""
                    for part in response.candidates[0].content.parts:
                        if part.text:
                            gemini_text_response += part.text
                        else:
                            print(
                                "\033[91mGemini returned an unexpected part type (not text or function call).\033[0m")
                            gemini_text_response += " I received an unusual response."

                    if gemini_text_response.strip():
                        print(gemini_text_response)
                        await speak(gemini_text_response)
                        log_message(gemini_text_response, "Nova")
                        conversation_history.append(
                            {"role": "model", "parts": [{"text": gemini_text_response}]})
                    else:
                        print(
                            "\033[91mGemini returned an empty text response.\033[0m")
                        await speak("I'm sorry, I couldn't generate a response.")
                        log_message("Empty response from Gemini.", "Nova")
                        conversation_history.append({"role": "model", "parts": [
                                                    {"text": "I'm sorry, I couldn't generate a response."}]})
            else:
                print(
                    "\033[91mGemini did not return a response or candidate.\033[0m")
                await speak("I'm sorry, I couldn't generate a response.")
                log_message(
                    "No candidate or response from Gemini.", "Nova")
                conversation_history.append({"role": "model", "parts": [
                                            {"text": "I'm sorry, I couldn't generate a response."}]})

        except Exception as e:
            error_text = str(e)
            if _is_quota_error(error_text):
                retry_seconds = _extract_retry_seconds(error_text)
                retry_hint = (
                    f" Please retry after about {retry_seconds} seconds."
                    if retry_seconds is not None else ""
                )
                concise_error = (
                    "Gemini API quota exceeded. Check Gemini API billing/quota settings for this key "
                    f"and model '{ACTIVE_MODEL}'.{retry_hint}"
                )
                print(f"\033[91m{concise_error}\033[0m")
                log_message("error", concise_error)
                await speak(
                    "I cannot reach Gemini right now because your API quota is exhausted. "
                    "Please check your plan and quota, then try again."
                )
                conversation_history.append({"role": "model", "parts": [{"text": concise_error}]})
                continue

            if _is_model_not_found_error(error_text):
                available_models = _list_generate_content_models()
                preview = ", ".join(available_models[:5]) if available_models else "none returned"
                concise_error = (
                    f"Configured Gemini model '{ACTIVE_MODEL}' is unavailable for this API key/version. "
                    f"Available generateContent models: {preview}."
                )
                print(f"\033[91m{concise_error}\033[0m")
                log_message("error", concise_error)
                await speak(
                    "This is not a voice error. The configured Gemini model is not available for your key. "
                    "Please switch to a supported model and try again."
                )
                conversation_history.append({"role": "model", "parts": [{"text": concise_error}]})
                continue

            print(f"\033[91mError communicating with Gemini: {error_text}\033[0m")
            log_message("error", f"Critical error in main loop: {error_text}")
            # Clear conversation history on critical error to prevent cascading issues
            conversation_history = []
            await speak("I'm sorry, I encountered an error. Please try again.")
            conversation_history.append({"role": "model", "parts": [
                                        {"text": "I'm sorry, I encountered an error. Please try again."}]})

    if stt_listener:
        stt_listener.close()


if __name__ == "__main__":
    stt_listener = None
    try:
        if USE_VOICE_INPUT:
            try:
                stt_listener = SpeechToTextListener()
            except Exception as e:
                print(
                    f"\033[93mWarning: Could not initialize STT listener ({e}). "
                    "Continuing in keyboard mode.\033[0m"
                )
        asyncio.run(main_conversation_loop())
    except KeyboardInterrupt:
        print("\nNova: Conversation interrupted. Exiting.")
        if stt_listener:
            stt_listener.close()
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        if stt_listener:
            stt_listener.close()
