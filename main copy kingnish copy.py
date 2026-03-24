# --- Suppress noisy library logs BEFORE other imports ---
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2' # Suppress TensorFlow logs
os.environ['WDM_LOG_LEVEL'] = '0' # Suppress WebDriver-Manager logs
import logging
logging.getLogger('WDM').setLevel(logging.NOTSET)
# --- End of log suppression ---

import time
import sys
from dotenv import load_dotenv
import asyncio
import base64
import os.path

# --- Imports for Terminal Aesthetics ---
import typer
from yaspin import yaspin
import pyfiglet
from tabulate import tabulate
import inquirer
from rich.console import Console

# --- Core Logic Imports (Unchanged) ---
import cv2
import mss
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import google.generativeai as genai
from google.generativeai import types
import edge_tts
import pygame
import pyautogui
import pywhatkit
from googlesearch import search
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Rich Console for better printing ---
console = Console()

# --- Constants (Unchanged) ---
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send']
LOG_FILE = "conversation_log.txt"
MAX_LOG_SIZE_CHARS = 10000
MAX_HISTORY_LENGTH = 20
VOICE = "en-US-JennyNeural"

# --- Configuration (Unchanged) ---
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    typer.secho("Error: GOOGLE_API_KEY not found in .env file.", fg=typer.colors.RED, bold=True)
    sys.exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

# --- Initialize Gemini Model (Unchanged) ---
SYSTEM_PROMPT = """
<purpose>
    Your purpose is to act as 'Drishti', a visionary AI assistant. 'Drishti' means 'vision' in Hindi, reflecting your core mission. You are a highly capable, empathetic, and patient personal assistant designed specifically to empower blind and disabled individuals. Your primary goal is to enhance their independence, improve their interaction with digital devices, and facilitate communication by being their eyes and hands in the digital and physical world.
</purpose>
<instructions>
    <instruction>**Persona and Tone:** Embody 'Drishti'. Be empathetic, patient, and empowering. Your tone must always be clear, concise, and direct.</instruction>
    <instruction>**Core Communication Rule:** Get straight to the point. Absolutely no conversational filler, preambles, or extra sentences (e.g., "Of course, I can help with that," or "Here is the information you requested.").</instruction>
    <instruction>**Formatting Constraint:** You MUST NOT use any markdown formatting. No bolding, italics, lists, or code blocks. All responses must be plain english text suitable for a screen reader.</instruction>
    <instruction>**Safety and Confirmation Protocol:** For non-sensitive requests (describing screen/surroundings, sending a message), you MUST directly use the appropriate tool. For highly sensitive actions (making a call, modifying system settings), you MUST ask for explicit verbal confirmation.</instruction>
    <instruction>**Task Execution:** Understand and respond to spoken commands in English. Adapt to speech variations. When composing messages/emails, infer subject and body. Perform Google searches and provide concise summaries. Control applications and browse the web.</instruction>
    <instruction>**Error and Ambiguity Handling:** If a request is ambiguous, ask a short, direct clarifying question. If you cannot perform a task, explain the limitation clearly and offer a viable alternative.</instruction>
</instructions>
"""

model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=SYSTEM_PROMPT)

# --- Helper Functions (UI Enhanced) ---
def log_message(content: str, sender: str = None):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if sender: f.write(f"{sender}: {content}\n")
        else: f.write(f"{content}\n")

async def summarize_conversation_log(current_log_content: str) -> str:
    summary_prompt = (f"Summarize this conversation concisely for context. Focus on topics, decisions, and key info. No filler. Under 500 chars.\n\nConversation:\n{current_log_content}")
    try:
        with yaspin(text="Summarizing conversation history...", color="magenta") as sp:
            summary_response = model.generate_content(summary_prompt, generation_config=genai.GenerationConfig(temperature=0.0, max_output_tokens=2000))
            summary = summary_response.text.strip()
            sp.ok("📄")
            typer.secho("Conversation summarized. New context established.", fg=typer.colors.MAGENTA)
        return summary
    except Exception as e:
        typer.secho(f"Error summarizing conversation: {e}", fg=typer.colors.RED); log_message("error", f"Error summarizing conversation: {e}")
        return "Failed to summarize previous conversation."

# --- Gmail API Functions (UI Enhanced) ---
def get_gmail_service():
    creds = None
    if os.path.exists('token.json'): creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        with yaspin(text="Gmail authentication required. Please follow browser prompts...", color="cyan") as sp:
            if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            with open('token.json', 'w') as token: token.write(creds.to_json())
            sp.ok("✅")
    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except HttpError as error:
        typer.secho(f'An error occurred with Gmail authentication: {error}', fg=typer.colors.RED); return None

def send_gmail_message(recipient_name: str, subject: str, message_text: str) -> str:
    email_contact_map = {"papa": "papa@gmail.com", "mom": "mom@example.com"}
    recipient_email = email_contact_map.get(recipient_name.lower())
    if not recipient_email: return f"Error: Contact '{recipient_name}' not found."
    try:
        service = get_gmail_service()
        if not service: return "Failed to authenticate with Gmail."
        message = MIMEText(message_text)
        message['to'] = recipient_email; message['subject'] = subject
        create_message = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}
        with yaspin(text=f"Sending email to {recipient_name}...", color="yellow") as sp:
            send_message = service.users().messages().send(userId="me", body=create_message).execute()
            sp.ok("📧")
        return f'Email sent successfully to {recipient_name}.'
    except Exception as e: return f'An unexpected error occurred while sending email: {str(e)}'

def read_gmail_messages(max_results: int = 5) -> str:
    try:
        service = get_gmail_service()
        if not service: return "Failed to authenticate with Gmail."
        with yaspin(text="Fetching latest emails...", color="yellow") as sp:
            results = service.users().messages().list(userId='me', labelIds=['INBOX'], maxResults=max_results).execute()
            messages = results.get('messages', [])
            if not messages: sp.text = "No new messages found."; sp.ok("🤷"); return 'No new messages found.'
            email_data = []
            for message in messages:
                msg = service.users().messages().get(userId='me', id=message['id'], format='metadata', metadataHeaders=['From', 'Subject']).execute()
                headers = msg.get('payload', {}).get('headers', []); sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender'); subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                email_data.append([sender, subject])
            sp.ok("📧")
        table = tabulate(email_data, headers=["From", "Subject"], tablefmt="heavy_grid", maxcolwidths=[35, 60])
        return f"Here are your latest emails:\n{table}"
    except Exception as e: return f'An unexpected error occurred while reading emails: {str(e)}'

# --- Speech-to-Text Listener Class (UI Enhanced) ---
class SpeechToTextListener:
    def __init__(self, website_path: str = "https://realtime-stt-devs-do-code.netlify.app/", language: str = "en-US", wait_time: int = 10):
        self.website_path = website_path; self.language = language; self.last_stt_text = ""
        self.chrome_options = Options()
        self.chrome_options.add_argument("--use-fake-ui-for-media-stream"); self.chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"); self.chrome_options.add_argument("--headless=new")
        self.chrome_options.add_experimental_option('excludeSwitches', ['enable-logging']) # Quieter logs
        self.driver = webdriver.Chrome(service=webdriver.ChromeService(ChromeDriverManager().install()), options=self.chrome_options)
        self.wait = WebDriverWait(self.driver, wait_time)

    def get_text(self) -> str:
        try: return self.wait.until(EC.presence_of_element_located((By.ID, "convert_text"))).text
        except Exception: return ""

    def main_stt_process(self):
        self.driver.get(self.website_path); self.wait.until(EC.presence_of_element_located((By.ID, "language_select")))
        self.driver.execute_script(f"document.getElementById('language_select').value = '{self.language}'; document.getElementById('language_select').dispatchEvent(new Event('change'));")
        self.driver.find_element(By.ID, "click_to_record").click(); is_recording = self.wait.until(EC.presence_of_element_located((By.ID, "is_recording")))
        spinner = yaspin(text="Listening...", color="cyan")
        with spinner:
            start_time = time.time(); last_text_time = time.time(); silence_timeout = 5; max_listen_time = 30
            while is_recording.text.startswith("Recording: True") and (time.time() - start_time < max_listen_time):
                text = self.get_text()
                if text and text != self.last_stt_text:
                    spinner.write(f"\r\033[K[bold yellow]Speaking:[/] [bright_cyan]{text}[/]"); self.last_stt_text = text; last_text_time = time.time()
                if time.time() - last_text_time > silence_timeout and len(text.strip()) > 0:
                    spinner.text = "Silence detected. Processing..."; break
                time.sleep(0.1)
        return self.get_text()

    def listen(self) -> str:
        try:
            while True:
                result = self.main_stt_process()
                if result and len(result.strip()) > 0: return result
                else: typer.secho("No speech detected. Please try again.", fg=typer.colors.YELLOW); time.sleep(0.5)
        except Exception as e: typer.secho(f"Error in STT listener: {e}", fg=typer.colors.RED, bold=True); return None

    def close(self):
        if self.driver: self.driver.quit(); typer.secho("Selenium WebDriver for STT closed.", fg=typer.colors.BLUE)

stt_listener = None

# --- Pygame-based TTS Function (UI Enhanced) ---
def remove_file(file_path):
    if os.path.exists(file_path):
        try: os.remove(file_path)
        except Exception: pass

async def generate_tts(TEXT, output_file):
    with yaspin(text="Drishti is generating speech...", color="green") as sp:
        try: await edge_tts.Communicate(TEXT, VOICE).save(output_file); sp.ok("🎤")
        except Exception as e: sp.fail("💥"); typer.secho(f"Error during TTS generation: {e}", fg=typer.colors.RED)

def play_audio(file_path):
    with yaspin(text="Playing audio...", color="blue") as sp:
        try:
            pygame.mixer.init(); pygame.mixer.music.load(file_path); pygame.mixer.music.play()
            while pygame.mixer.music.get_busy(): pygame.time.Clock().tick(10)
            pygame.mixer.quit(); sp.ok("▶️ ")
        except Exception as e:
            sp.fail("💥"); typer.secho(f"Error playing audio with Pygame: {e}", fg=typer.colors.RED)
            if pygame.mixer.get_init(): pygame.mixer.quit()

async def speak(TEXT):
    output_file = "output.mp3"; remove_file(output_file)
    await generate_tts(TEXT, output_file)
    if os.path.exists(output_file): play_audio(output_file)
    remove_file(output_file)

# --- Vision Capture Functions (UI Enhanced) ---
def capture_webcam_image():
    with yaspin(text="Accessing webcam...", color="blue") as sp:
        cap = cv2.VideoCapture(0);
        if not cap.isOpened(): sp.fail("💥"); typer.secho("Error: Could not open webcam.", fg=typer.colors.RED); return None
        ret, frame = cap.read(); cap.release()
        if ret: img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB); pil_image = Image.fromarray(img_rgb); sp.ok("📸"); return pil_image
        else: sp.fail("💥"); typer.secho("Error: Could not read frame from webcam.", fg=typer.colors.RED); return None

def capture_screen_image():
    with yaspin(text="Capturing screen...", color="blue") as sp:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0]; sct_img = sct.grab(monitor); pil_image = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                sp.ok("🖥️ "); return pil_image
        except Exception as e: sp.fail("💥"); typer.secho(f"Error capturing screenshot: {e}", fg=typer.colors.RED); return None

# --- Gemini Tools (UI Enhanced, Logic from Reference) ---
async def describe_webcam_view(user_query: str) -> str:
    typer.secho("AI is preparing to capture webcam view...", fg=typer.colors.YELLOW)
    pil_image = capture_webcam_image()
    if pil_image:
        try:
            with yaspin(text="Analyzing webcam view...", color="magenta") as sp:
                contents_with_image = [f"Analyze this webcam image and provide a short and concise description focusing on {user_query}.", pil_image]
                response = model.generate_content(contents=contents_with_image, generation_config=genai.GenerationConfig(temperature=0.0)) # NOT awaited
                sp.ok("✅")
            if response.text: typer.secho("Webcam analysis completed.", fg=typer.colors.CYAN); return response.text.strip()
            else: typer.secho("Gemini did not return a text description for the webcam image.", fg=typer.colors.RED); return "I captured an image, but couldn't get a description."
        except Exception as e: typer.secho(f"Error sending webcam image to Gemini: {e}", fg=typer.colors.RED); return "I captured an image, but encountered an error while analyzing it."
    else: return "I was unable to capture an image from the webcam."

async def describe_screen_content(user_query: str) -> str:
    typer.secho("AI is preparing to capture screen content...", fg=typer.colors.YELLOW)
    pil_image = capture_screen_image()
    if pil_image:
        try:
            with yaspin(text="Analyzing screen content...", color="magenta") as sp:
                contents_with_image = [f"Analyze this screen image and provide a short and concise description focusing on {user_query}.", pil_image]
                response = model.generate_content(contents=contents_with_image, generation_config=genai.GenerationConfig(temperature=0.0)) # NOT awaited
                sp.ok("✅")
            if response.text: typer.secho("Screen analysis completed.", fg=typer.colors.CYAN); return response.text.strip()
            else: typer.secho("Gemini did not return a text description for the screen image.", fg=typer.colors.RED); return "I captured your screen, but couldn't get a description."
        except Exception as e: typer.secho(f"Error sending screen image to Gemini: {e}", fg=typer.colors.RED); return "I captured your screen, but encountered an error while analyzing it."
    else: return "I was unable to capture your screen."

async def send_whatsapp_message(recipient_name: str, message_content: str) -> str:
    phone_number_map = {"papa": "+9112321321", "mom": "+911231565"}
    phone_no = phone_number_map.get(recipient_name.lower())
    if not phone_no: return f"Error: Contact '{recipient_name}' not found."
    with yaspin(text=f"Sending WhatsApp message to {recipient_name}...", color="green") as sp:
        try:
            pywhatkit.sendwhatmsg_instantly(phone_no=phone_no, message=message_content, wait_time=9, tab_close=True, close_time=2)
            sp.ok("✅"); return f"WhatsApp message sent to {recipient_name}."
        except Exception as e: sp.fail("💥"); return f"Failed to send WhatsApp message. Error: {e}"

async def search_web(query: str) -> str:
    with yaspin(text=f"Searching Google for '{query}'...", color="yellow") as sp:
        try:
            search_results = list(search(query, advanced=True, num_results=5))
            if not search_results: sp.text = "No results found."; sp.ok("🤷"); return "No search results found for your query."
            table_data = [[res.title, res.description, res.url] for res in search_results]
            table = tabulate(table_data, headers=["Title", "Description", "URL"], tablefmt="heavy_grid", maxcolwidths=[30, 60, 40])
            sp.ok("🔎"); return f"Here are the top search results for '{query}':\n{table}"
        except Exception as e: sp.fail("💥"); return f"Error performing web search: {e}"

async def call_whatsapp_contact(person_name: str, call_type: str = 'voice'):
    with yaspin(text=f"Initiating WhatsApp {call_type} call to {person_name}...", color="green") as sp:
        try:
            pyautogui.press('win'); await asyncio.sleep(0.5); pyautogui.write('whatsapp'); await asyncio.sleep(0.5); pyautogui.press('enter'); await asyncio.sleep(4)
            pyautogui.hotkey('ctrl', 'f'); await asyncio.sleep(0.5); pyautogui.hotkey('ctrl', 'a'); await asyncio.sleep(0.2); pyautogui.press('backspace'); await asyncio.sleep(0.2)
            pyautogui.write(person_name, interval=0.1); await asyncio.sleep(1.5); pyautogui.click(347, 260); await asyncio.sleep(1)
            if call_type == 'voice': pyautogui.click(1814, 101)
            elif call_type == 'video': pyautogui.click(1755, 103)
            else: raise ValueError('call_type must be "voice" or "video"')
            await asyncio.sleep(1); sp.ok("📞"); return f'WhatsApp {call_type} call initiated to {person_name}.'
        except Exception as e: sp.fail("💥"); return f"Failed to initiate WhatsApp call. Error: {e}"

AVAILABLE_TOOLS = [describe_webcam_view, describe_screen_content, send_whatsapp_message, search_web, send_gmail_message, read_gmail_messages, call_whatsapp_contact]

# --- Main Conversation Loop (Logic from Reference, UI Enhanced) ---
async def main_conversation_loop(stt_listener: SpeechToTextListener):
    typer.secho("\nDrishti is ready.", fg=typer.colors.CYAN, bold=True)
    await speak("Hello! How can I assist you today!")

    if not os.path.exists(LOG_FILE): open(LOG_FILE, "w", encoding="utf-8").close()
    conversation_history = []
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
        with open(LOG_FILE, "r", encoding="utf-8") as f: initial_log_content = f.read()
        if len(initial_log_content) > MAX_LOG_SIZE_CHARS:
            summary_text = await summarize_conversation_log(initial_log_content)
            with open(LOG_FILE, "w", encoding="utf-8") as f: log_message(summary_text, sender="Dhrishti")
            conversation_history.append({"role": "user", "parts": [{"text": f"Previous conversation summary: {summary_text}"}]})
        else: conversation_history.append({"role": "user", "parts": [{"text": f"Previous conversation log: {initial_log_content}"}]})

    questions = [inquirer.List('input_method', message="Select Input Method (type 'exit' anytime to quit)", choices=['🎤 Voice Command', '⌨️ Text Input'], carousel=True)]
    try:
        answer = inquirer.prompt(questions, raise_keyboard_interrupt=True)
        if not answer: typer.secho("\nNo input method selected. Exiting.", fg=typer.colors.YELLOW); return
        input_mode = answer['input_method']
    except KeyboardInterrupt: typer.secho("\nConversation interrupted. Exiting.", fg=typer.colors.YELLOW); await speak("Goodbye!"); return

    typer.secho(f"\nSelected input method: {input_mode}", fg=typer.colors.GREEN)

    while True:
        user_input = None
        if input_mode == '🎤 Voice Command':
            user_input = stt_listener.listen()
            if user_input: typer.secho(f"You: {user_input}", fg=typer.colors.GREEN, bold=True)
        elif input_mode == '⌨️ Text Input':
            user_input = typer.prompt(typer.style("You", fg=typer.colors.GREEN, bold=True), default="", prompt_suffix=" > ")
        
        if user_input is None or user_input.lower() == "exit":
            log_message("system", "User exited conversation."); typer.secho("\nDrishti: Goodbye!", fg=typer.colors.BRIGHT_BLUE); await speak("Goodbye!"); break
        if not user_input.strip(): continue

        log_message(user_input, "User"); conversation_history.append({"role": "user", "parts": [{"text": user_input}]})
        if len(conversation_history) > MAX_HISTORY_LENGTH: conversation_history = conversation_history[-MAX_HISTORY_LENGTH:]

        try:
            with yaspin(text="Drishti is thinking...", color="cyan") as sp:
                response = model.generate_content(contents=conversation_history, tools=AVAILABLE_TOOLS, generation_config=genai.GenerationConfig(temperature=0.6))
            
            if response.candidates and response.candidates[0].content.parts:
                tool_calls_to_execute = []
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        tool_calls_to_execute.append(part.function_call)
                        log_message("model_tool_call", f"Requested tool: {part.function_call.name} with args: {part.function_call.args}")

                if tool_calls_to_execute:
                    typer.secho("Gemini requested tool calls. Executing...", fg=typer.colors.YELLOW)
                    # Add model's tool request to history BEFORE executing
                    conversation_history.append(response.candidates[0].content)
                    
                    function_responses = []
                    for tool_call in tool_calls_to_execute:
                        tool_name = tool_call.name
                        current_tool_args = {key: value for key, value in tool_call.args.items()}
                        called_function = next((f for f in AVAILABLE_TOOLS if f.__name__ == tool_name), None)
                        if called_function:
                            if 'user_query' in called_function.__code__.co_varnames: current_tool_args['user_query'] = user_input
                            if asyncio.iscoroutinefunction(called_function): tool_result_text = await called_function(**current_tool_args)
                            else: tool_result_text = called_function(**current_tool_args)
                            typer.secho(f"Tool '{tool_name}' executed. Result:\n{tool_result_text}", fg=typer.colors.YELLOW)
                            log_message(tool_result_text, tool_name)
                            function_responses.append({"function_response": {"name": tool_name, "response": {"type": "text", "text": tool_result_text}}})
                        else:
                            error_message = f"I'm sorry, I don't know how to perform the action '{tool_name}'."
                            typer.secho(f"Error: Unknown tool '{tool_name}' requested by Gemini.", fg=typer.colors.RED)
                            log_message("error", f"Unknown tool requested: {tool_name}")
                            function_responses.append({"function_response": {"name": tool_name, "response": {"type": "text", "text": error_message}}})

                    if function_responses:
                        typer.secho("Sending tool results back to model...", fg=typer.colors.YELLOW)
                        # Add tool results to history
                        conversation_history.append({"role": "function", "parts": function_responses})
                        with yaspin(text="Drishti is synthesizing tool results...", color="cyan") as sp:
                            final_response_from_model = model.generate_content(contents=conversation_history, generation_config=genai.GenerationConfig(temperature=0.0))
                        final_text_response = "".join([p.text for p in final_response_from_model.candidates[0].content.parts if p.text])
                else: # Direct text response
                    final_text_response = "".join([p.text for p in response.candidates[0].content.parts if p.text])
                
                if final_text_response.strip():
                    typer.secho(f"Drishti: {final_text_response}", fg=typer.colors.CYAN)
                    await speak(final_text_response)
                    log_message(final_text_response, "Dhrishti")
                    conversation_history.append({"role": "model", "parts": [{"text": final_text_response}]})
                else:
                    typer.secho("Drishti had nothing to say.", fg=typer.colors.YELLOW)
                    log_message("Empty final response from Gemini.", "Dhrishti")
            else:
                typer.secho("Drishti did not return a response.", fg=typer.colors.RED)
                await speak("I'm sorry, I couldn't generate a response.")
                log_message("No candidate or response from Gemini.", "Dhrishti")

        except Exception as e:
            typer.secho(f"An error occurred in the main loop: {e}", fg=typer.colors.RED, bold=True)
            log_message("error", f"Critical error in main loop: {e}"); conversation_history = []
            await speak("I'm sorry, I encountered an error. Please try again.")

# --- Main Entry Point ---
def main():
    fig = pyfiglet.Figlet(font='standard', width=100)
    typer.secho(fig.renderText('Drishti'), fg=typer.colors.CYAN); typer.secho("A Visionary AI Assistant to Empower and Assist", bold=True); typer.secho("Made By ❤️ Lokesh for Blind\n", fg=typer.colors.MAGENTA)
    global stt_listener
    try:
        with yaspin(text="Initializing services...", color="yellow") as sp:
            stt_listener = SpeechToTextListener(); pygame.init(); sp.ok("✅")
        asyncio.run(main_conversation_loop(stt_listener))
    except KeyboardInterrupt: typer.secho("\nConversation interrupted by user. Exiting.", fg=typer.colors.YELLOW)
    except Exception as e: typer.secho(f"\nAn unexpected critical error occurred: {e}", fg=typer.colors.RED, bold=True)
    finally:
        if stt_listener: stt_listener.close()
        if pygame.mixer.get_init(): pygame.quit()

if __name__ == "__main__":
    typer.run(main)
