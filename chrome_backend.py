import base64
import io
import os
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from PIL import Image
import requests
import google.generativeai as genai

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
HF_API_KEY = os.getenv("HF_API_KEY")
HF_MODEL = os.getenv("HF_MODEL", "Qwen/Qwen2.5-7B-Instruct")
HF_VISION_MODEL = os.getenv("HF_VISION_MODEL", "Qwen/Qwen3.5-9B")

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

if not GOOGLE_API_KEY and not HF_API_KEY:
    raise RuntimeError("Set GOOGLE_API_KEY or HF_API_KEY in .env")

SYSTEM_PROMPT = (
    "You are Nova, an accessibility-focused AI assistant for blind and visually impaired users. "
    "Respond clearly and concisely in plain text. No markdown."
)

VISION_SYSTEM_PROMPT = (
    "You are Nova, an accessibility assistant for blind users. When describing screen content:\n"
    "1. FOCUS ONLY on the main content area - ignore sidebars, toolbars, tabs, and navigation menus\n"
    "2. Describe in logical reading order: headings, body text, important controls, buttons, forms\n"
    "3. Be extremely concise - 2-3 sentences maximum for simple pages, 4-5 for complex ones\n"
    "4. Skip decorative elements, logos, background images\n"
    "5. Highlight interactive elements (links, buttons, input fields) by name and purpose\n"
    "6. Use plain language, no technical jargon\n"
    "7. If the page is mostly navigation/chrome, say 'This page contains primarily navigation controls'"
)

app = Flask(__name__)
CORS(app)

GEMINI_MODEL_INSTANCE = (
    genai.GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)
    if GOOGLE_API_KEY else None
)
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"


@app.get("/health")
def health() -> tuple[dict, int]:
    return {
        "ok": True,
        "providers": {
            "gemini": bool(GOOGLE_API_KEY),
            "huggingface": bool(HF_API_KEY),
        },
        "models": {
            "gemini": GEMINI_MODEL,
            "huggingface_text": HF_MODEL,
            "huggingface_vision": HF_VISION_MODEL,
        },
    }, 200


def _choose_provider(payload: dict, task: str) -> str:
    requested = (payload.get("provider") or "").strip().lower()
    if requested in {"gemini", "hf", "huggingface"}:
        if requested == "gemini":
            return "gemini"
        return "huggingface"

    if task == "vision":
        if GOOGLE_API_KEY:
            return "gemini"
        return "huggingface"

    if GOOGLE_API_KEY:
        return "gemini"
    return "huggingface"


def _call_hf_model(prompt: str, max_tokens: int = 512) -> Optional[str]:
    """Call Hugging Face inference API for text generation."""
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
        response = requests.post(HF_ROUTER_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return (message.get("content") or message.get("reasoning") or "").strip()
        return None
    except Exception:
        return None


def _call_gemini_text(query: str) -> Optional[str]:
    if not GEMINI_MODEL_INSTANCE:
        return None
    try:
        response = GEMINI_MODEL_INSTANCE.generate_content(query)
        return (response.text or "").strip()
    except Exception:
        return None


def _call_hf_model_with_key(prompt: str, hf_api_key: str, max_tokens: int = 512) -> Optional[str]:
    headers = {"Authorization": f"Bearer {hf_api_key}"}
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
        response = requests.post(HF_ROUTER_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return (message.get("content") or message.get("reasoning") or "").strip()
        return None
    except Exception:
        return None


def _call_gemini_text_with_key(query: str, google_api_key: str) -> Optional[str]:
    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)
        response = model.generate_content(query)
        return (response.text or "").strip()
    except Exception:
        return None


def _call_gemini_vision(query: str, image: Image.Image) -> Optional[str]:
    if not GEMINI_MODEL_INSTANCE:
        return None
    prompt = (
        f"User request: {query}\n\n"
        "Analyze this screen and describe ONLY the main content area. "
        "Ignore navigation menus, sidebars, toolbars, and tabs. "
        "Be extremely concise (2-5 sentences max). "
        "List important headings and interactive elements."
    )
    try:
        response = GEMINI_MODEL_INSTANCE.generate_content([prompt, image])
        return (response.text or "").strip()
    except Exception:
        return None


def _call_gemini_vision_with_key(query: str, image: Image.Image, google_api_key: str) -> Optional[str]:
    prompt = (
        f"User request: {query}\n\n"
        "Analyze this screen and describe ONLY the main content area. "
        "Ignore navigation menus, sidebars, toolbars, and tabs. "
        "Be extremely concise (2-5 sentences max). "
        "List important headings and interactive elements."
    )
    try:
        genai.configure(api_key=google_api_key)
        model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=VISION_SYSTEM_PROMPT)
        response = model.generate_content([prompt, image])
        return (response.text or "").strip()
    except Exception:
        return None


def _decode_data_url(data_url: str) -> Optional[Image.Image]:
    if not data_url or "," not in data_url:
        return None
    try:
        _, encoded = data_url.split(",", 1)
        raw = base64.b64decode(encoded)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None


@app.post("/ask")
def ask() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    api_key_override = (payload.get("api_key") or "").strip()
    if not query:
        return {"error": "query is required"}, 400

    provider = _choose_provider(payload, task="text")

    try:
        reply = None
        model_name = ""

        if provider == "gemini":
            google_key = api_key_override or GOOGLE_API_KEY
            if not google_key:
                return {"error": "Gemini requested but GOOGLE_API_KEY is missing"}, 400
            if api_key_override:
                reply = _call_gemini_text_with_key(query, google_key)
            else:
                reply = _call_gemini_text(query)
            model_name = GEMINI_MODEL
        else:
            hf_key = api_key_override or HF_API_KEY
            if not hf_key:
                return {"error": "Hugging Face requested but HF_API_KEY is missing"}, 400
            formatted_prompt = f"{SYSTEM_PROMPT}\n\nUser: {query}\n\nAssistant:"
            if api_key_override:
                reply = _call_hf_model_with_key(formatted_prompt, hf_key, max_tokens=512)
            else:
                reply = _call_hf_model(formatted_prompt, max_tokens=512)
            model_name = HF_MODEL

        if not reply:
            return {"error": "Model returned empty response"}, 502
        return {"reply": reply, "provider": provider, "model": model_name}, 200
    except Exception as exc:
        return {"error": f"Request failed: {exc}"}, 500


@app.post("/describe-image")
def describe_image() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "describe what is visible").strip()
    image_data_url = payload.get("image_data_url") or ""
    api_key_override = (payload.get("api_key") or "").strip()
    provider = _choose_provider(payload, task="vision")

    image = _decode_data_url(image_data_url)
    if image is None:
        return {"error": "Valid image_data_url is required"}, 400

    if provider == "gemini":
        google_key = api_key_override or GOOGLE_API_KEY
        if not google_key:
            return {"error": "Gemini requested but GOOGLE_API_KEY is missing"}, 400
        try:
            if api_key_override:
                reply = _call_gemini_vision_with_key(query, image, google_key)
            else:
                reply = _call_gemini_vision(query, image)
            if not reply:
                return {"error": "Gemini vision returned empty response"}, 502
            return {"reply": reply, "provider": "gemini", "model": GEMINI_MODEL}, 200
        except Exception as exc:
            return {"error": f"Gemini vision request failed: {exc}"}, 500

    hf_key = api_key_override or HF_API_KEY
    if not hf_key:
        return {"error": "Hugging Face requested but HF_API_KEY is missing"}, 400

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    image_data = base64.b64encode(buf.getvalue()).decode()

    headers = {"Authorization": f"Bearer {hf_key}"}

    try:
        response = requests.post(
            HF_ROUTER_URL,
            headers=headers,
            json={
                "model": HF_VISION_MODEL,
                "messages": [
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": query},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                        ],
                    },
                ],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()

        reply = None
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            reply = (message.get("content") or message.get("reasoning") or "").strip()

        if not reply:
            return {"error": "Vision model returned empty response"}, 502

        return {
            "reply": reply,
            "provider": "huggingface",
            "model": HF_VISION_MODEL,
        }, 200
    except Exception as exc:
        return {"error": f"HF vision request failed: {exc}"}, 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
