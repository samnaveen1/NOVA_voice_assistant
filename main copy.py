import os
import time
import sys
import subprocess
from dotenv import load_dotenv

import cv2  # For webcam access
import mss  # For screen capture
from PIL import Image  # For image manipulation
import inspect  # For checking function signatures

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

# Edge TTS
import edge_tts
import asyncio

# Pygame for audio playback
import pygame

# NEW IMPORTS for new tools
import pyautogui
import pywhatkit

# --- Configuration ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    sys.exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

# --- SYSTEM PROMPT ---
SYSTEM_PROMPT = """
You are 'Visionary AI Assistant', a highly capable, empathetic, and patient personal assistant designed to empower blind and disabled individuals. Your primary goal is to enhance their independence, improve their interaction with digital devices, and facilitate communication.

**Core Principles:**
1.  **Empowerment:** Always aim to empower the user, providing them with control and information.
2.  **Clarity & Conciseness:** Speak clearly, use simple language, and be direct. **Absolutely no markdown formatting (like bolding, lists, code blocks). Do not add any conversational filler, preambles, or extra sentences.** Get straight to the point.
3.  **Low Latency & Efficiency:** Provide quick, relevant, and actionable responses. Prioritize efficiency in tasks.
4.  **Empathy & Patience:** Be understanding and patient.
5.  **Safety & Privacy:** For requests to describe the screen or surroundings (webcam), sending a messsage **you MUST directly and immediately use the appropriate tool without asking for confirmation or adding any preamble.** For highly sensitive actions (e.g., making a call, modifying system settings), you MUST ask for explicit verbal user confirmation. Protect user privacy at all times.

**Your Capabilities (for future phases):**
*   Understand and respond to spoken commands.
*   Provide descriptions of physical surroundings (via webcam).
*   Describe and interact with on-screen content (via screen access).
*   Open applications.
*   Send WhatsApp messages (requires confirmation).
*   Perform Google searches and provide concise summaries.
*   Control applications and browse the web.
*   Automate communication tasks (WhatsApp, Telegram, Gmail).
*   Remember past interactions and preferences.

**Communication Guidelines:**
*   Always respond in spoken English.
*   If a request is ambiguous, ask clarifying questions.
*   Confirm understanding of complex requests before acting *only for sensitive operations*.
*   If you encounter an error or cannot perform a task, explain why clearly and offer alternatives.
*   End your responses clearly, indicating you are ready for the next command.
"""

# Initialize Gemini Model with the system instruction
model = genai.GenerativeModel(
    'gemini-2.0-flash',  # Or 'gemini-2.0-flash'
    system_instruction=SYSTEM_PROMPT
)

# --- NEW SPEECH TO TEXT LISTENER CLASS ---


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
        print("Made By ❤️ @DevsDoCode")

    def stream(self, content: str):
        """Prints the given content to the console with a yellow color, overwriting previous output, with "speaking..." added."""
        print("\033[96m\rUser Speaking: \033[93m" +
              f" {content}", end='', flush=True)

    def get_text(self) -> str:
        """Retrieves the transcribed text from the website."""
        try:
            return self.wait.until(EC.presence_of_element_located((By.ID, "convert_text"))).text
        except:
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
            print(
                f"Error: Failed to select the correct language. Selected: {self.verify_language_selection():}, Expected: {self.language}")
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
        self.last_stt_text = ""
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
VOICE = "en-US-JennyNeural"
BUFFER_SIZE = 1024


def remove_file(file_path):
    max_attempts = 3
    attempts = 0
    while attempts < max_attempts:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            break
        except Exception as e:
            print(f"Error removing file '{file_path}': {e}")
            attempts += 1
            time.sleep(0.1)


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
        pygame.mixer.quit()
    except Exception as e:
        print(f"\033[91mError playing audio with Pygame: {e}\033[0m")
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
        print(f"Current working directory for screenshot debug: {os.getcwd()}")
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            sct_img = sct.grab(monitor)
            pil_image = Image.frombytes(
                "RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            # --- DEBUGGING: Temporarily save the image to disk to verify capture ---
            # debug_output_path = "debug_screen_capture.png"
            # pil_image.save(debug_output_path)
            # print(f"\033[94mDEBUG: Screenshot temporarily saved to {debug_output_path}\033[0m")
            # --- END DEBUGGING ---

            print("\033[94mScreenshot captured as PIL Image object.\033[0m")
            return pil_image
    except Exception as e:
        print(f"\033[91mError capturing screenshot: {e}\033[0m")
        return None

# --- Gemini Tools ---


async def describe_webcam_view(user_query: str) -> str:
    """Captures an image from the webcam, sends it to Gemini for description, and returns the text description.
    This tool is used when the user asks about their physical surroundings, what is in front of them, or what they see.
    """
    print("\033[93mAI is preparing to capture webcam view and describe it...\033[0m")
    pil_image = capture_webcam_image()

    if pil_image:
        try:
            contents_with_image = [
                f"Provide a concise, factual, plain-text {user_query} of this image. Do not add any conversational filler, preambles, or extra sentences. No markdown.",
                pil_image
            ]

            response = model.generate_content(
                contents=contents_with_image,
                generation_config=genai.GenerationConfig(temperature=0.0)
            )

            if response.text:
                print("\033[94mWebcam description received from Gemini.\033[0m")
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
    """Captures a screenshot of the current screen, sends it to Gemini for description, and returns the text description.
    This tool is used when the user asks about what's on their screen, what is displayed, or what their device shows.
    """
    print(
        "\033[93mAI is preparing to capture screen content and describe it...\003[0m")
    pil_image = capture_screen_image()

    if pil_image:
        try:
            contents_with_image = [
                f"Provide a concise, factual, plain-text {user_query} of this screen image. Focus on main elements, text, and overall layout. Do not add any conversational filler, preambles, or extra sentences. No markdown.",
                pil_image
            ]

            response = model.generate_content(
                contents=contents_with_image,
                generation_config=genai.GenerationConfig(temperature=0.0)
            )

            if response.text:
                print("\033[94mScreen description received from Gemini.\033[0m")
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
        "papa": "+91999999999",  # Replace with actual number
        "mom": "+915465454654",
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

# Define the Google Search grounding tool


AVAILABLE_TOOLS = [
    describe_webcam_view,
    describe_screen_content,
    send_whatsapp_message,
]
# --- Main Conversation Loop ---


async def main_conversation_loop():
    print("Dhrishti: Hello! How can I assist you today? (Say 'exit' to quit)")
    await speak("Hello! How can I assist you today!")

    conversation_history = []

    while True:
        user_input = stt_listener.listen(prints=True)

        if user_input is None or user_input.lower() == "exit":
            print("Dhrishti: Goodbye!")
            await speak("Goodbye!")
            break

        if not user_input.strip():
            continue

        # Add user input to conversation history
        conversation_history.append(
            {"role": "user", "parts": [{"text": user_input}]})

        try:
            # Clear conversation history if it gets too long to prevent context issues
            if len(conversation_history) > 20:
                # Keep last 10 exchanges
                conversation_history = conversation_history[-10:]

            response = model.generate_content(
                contents=conversation_history,
                tools=AVAILABLE_TOOLS,
                generation_config=genai.GenerationConfig(temperature=0.0)
            )

            if response.candidates and response.candidates[0].content.parts:
                tool_calls_to_execute = []
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        tool_calls_to_execute.append(part.function_call)

                if tool_calls_to_execute:
                    print(
                        "\033[93mGemini requested tool calls. Executing...\033[0m")

                    final_tool_response_text = ""

                    for tool_call in tool_calls_to_execute:
                        tool_name = tool_call.name

                        # Ensure tool_args is always a mutable dictionary
                        current_tool_args = {}
                        if tool_call.args:
                            for key, value in tool_call.args.items():
                                current_tool_args[key] = value

                        called_function = next(
                            (f for f in AVAILABLE_TOOLS if f.__name__ == tool_name), None)

                        if called_function:
                            # Only add 'user_query' if the tool function expects it
                            import inspect
                            signature = inspect.signature(called_function)
                            if 'user_query' in signature.parameters:
                                current_tool_args['user_query'] = user_input

                            if asyncio.iscoroutinefunction(called_function):
                                tool_result_text = await called_function(**current_tool_args)
                            else:
                                tool_result_text = called_function(
                                    **current_tool_args)

                            print(
                                f"\033[93mTool '{tool_name}' executed. Result: {tool_result_text}\033[0m")
                            final_tool_response_text = tool_result_text

                            # Add tool call and response to conversation history in correct format
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
                            final_tool_response_text = error_message
                            conversation_history.append(
                                {"role": "model", "parts": [{"text": error_message}]})

                    if final_tool_response_text:
                        print(final_tool_response_text)
                        await speak(final_tool_response_text)
                    else:
                        await speak("I performed an action, but there was no direct response.")

                else:
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
                        conversation_history.append(
                            {"role": "model", "parts": [{"text": gemini_text_response}]})
                    else:
                        print(
                            "\033[91mGemini returned an empty text response.\033[0m")
                        await speak("I'm sorry, I couldn't generate a response.")
                        conversation_history.append({"role": "model", "parts": [
                                                    {"text": "I'm sorry, I couldn't generate a response."}]})
            else:
                print(
                    "\033[91mGemini did not return a response or candidate.\033[0m")
                await speak("I'm sorry, I couldn't generate a response.")
                conversation_history.append({"role": "model", "parts": [
                                            {"text": "I'm sorry, I couldn't generate a response."}]})

        except Exception as e:
            print(f"\033[91mError communicating with Gemini: {e}\033[0m")
            # Clear conversation history on error to prevent cascading issues
            conversation_history = []
            await speak("I'm sorry, I encountered an error. Please try again.")
            conversation_history.append({"role": "model", "parts": [
                                        {"text": "I'm sorry, I encountered an error. Please try again."}]})

    if stt_listener:
        stt_listener.close()


if __name__ == "__main__":
    try:
        stt_listener = SpeechToTextListener()
        asyncio.run(main_conversation_loop())
    except KeyboardInterrupt:
        print("\nDhrishti: Conversation interrupted. Exiting.")
        if stt_listener:
            stt_listener.close()
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        if stt_listener:
            stt_listener.close()
