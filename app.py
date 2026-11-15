import io
import os
import re
import json
import uuid
import base64
import itertools
import sqlite3
import urllib.parse
from contextlib import contextmanager
from datetime import date

import requests
import streamlit as st
from dotenv import load_dotenv
from groq import Groq
from pypdf import PdfReader

load_dotenv()

st.set_page_config(
    page_title="আমার পার্সোনাল AI",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- ChatGPT স্টাইল কাস্টম CSS (মোবাইল ও ডেস্কটপ ফিক্সড) ---
st.markdown(
    """
    <style>
    .stApp {
        background-color: #212121 !important;
        color: #ececec !important;
    }
    .stApp p, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp label, .stMarkdown {
        color: #ececec !important;
    }
    [data-testid="stSidebar"] {
        background-color: #171717 !important;
        border-right: 1px solid #2f2f2f;
    }
    [data-testid="stSidebar"] * {
        color: #ececec !important;
    }
    .block-container {
        max-width: 820px;
        padding-top: 2rem;
        padding-bottom: 6rem;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background-color: #2f2f2f !important;
        border-radius: 18px;
        padding: 12px 18px;
        margin-left: auto;
        width: fit-content;
        max-width: 85%;
        border: 1px solid #3c3c3c;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
        background-color: transparent !important;
        padding: 12px 0px;
    }
    .stButton > button, .stDownloadButton > button {
        background-color: #2f2f2f !important;
        color: #ececec !important;
        border: 1px solid #444444 !important;
        border-radius: 10px !important;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        background-color: #3d3d3d !important;
        border-color: #666666 !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        width: 100%;
        text-align: left;
        padding: 8px 12px;
    }
    [data-testid="stBottom"], [data-testid="stBottom"] > div {
        background-color: #212121 !important;
    }
    [data-testid="stChatInput"], [data-testid="stChatInput"] > div {
        background-color: #2f2f2f !important;
        border-radius: 24px !important;
        border: 1px solid #4a4a4a !important;
    }
    [data-testid="stChatInput"] textarea {
        background-color: transparent !important;
        color: #ffffff !important;
        caret-color: #ffffff !important;
    }
    [data-testid="stChatInput"] textarea::placeholder {
        color: #9e9e9e !important;
        opacity: 1 !important;
    }
    [data-testid="stChatInput"] button, [data-testid="stChatInput"] svg {
        color: #ececec !important;
        fill: #ececec !important;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header[data-testid="stHeader"] {
        background-color: rgba(33, 33, 33, 0.85) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_secret(name, default=""):
    val = os.environ.get(name)
    if val:
        return val
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


APP_PASSWORD = get_secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("auth"):
    st.markdown("<h2 style='text-align:center;'>🔒 আমার পার্সোনাল AI লগইন</h2>", unsafe_allow_html=True)
    pw = st.text_input("পাসওয়ার্ড দিন:", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("ভুল পাসওয়ার্ড")
    st.stop()

api_key = get_secret("GROQ_API_KEY")
if not api_key:
    st.error("GROQ_API_KEY পাওয়া যায়নি। Secrets বা .env ফাইলে key সেট করো।")
    st.stop()

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GEMINI_MODEL = get_secret("GEMINI_MODEL") or "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = get_secret("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
HF_TOKEN = get_secret("HF_TOKEN")
TAVILY_API_KEY = get_secret("TAVILY_API_KEY")
OPENAI_IMAGE_MODEL = get_secret("OPENAI_IMAGE_MODEL") or "gpt-image-1"
HAS_EDIT = bool(OPENAI_API_KEY or GEMINI_API_KEY or HF_TOKEN)
client = Groq(api_key=api_key)

WP_URL = get_secret("WP_URL").rstrip("/")
WP_AUTH = (get_secret("WP_USER"), get_secret("WP_APP_PASSWORD"))

MAX_HISTORY = 12
GROQ_FILE_LIMIT = 20000  # Groq মডেলের জন্য নিরাপদ সীমা
MAX_FILE_CHARS = 100000 if GEMINI_API_KEY else GROQ_FILE_LIMIT
MAX_IMAGE_BYTES = 3_000_000
MAX_CHATS = 30
VISION_MODEL = "qwen/qwen3.8-27b"  # Groq-এর ছবি দেখার (Vision) মডেল
HF_IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"
HF_EDIT_MODEL = "black-forest-labs/FLUX.1-Kontext-dev"
try:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BASE_DIR = os.getcwd()
DB_FILE = get_secret("CHAT_DB_PATH") or os.path.join(_BASE_DIR, "chat_history.db")

IMAGE_SIZES = {
    "⬜ বর্গাকার (1024×1024)": (1024, 1024, "1024x1024"),
    "📱 পোর্ট্রেট (1024×1536)": (1024, 1536, "1024x1536"),
    "🖥️ ল্যান্ডস্কেপ (1536×1024)": (1536, 1024, "1536x1024"),
}
IMAGE_QUALITIES = ["auto", "low", "medium", "high"]

ELEMENTOR_PROMPT = """You are an expert WordPress and Elementor developer.
When the user asks for a template, page, section, or block, output ONLY one valid JSON object
inside a ```json code block, in Elementor template import format:
{
  "version": "0.4",
  "title": "<template title>",
  "type": "page",
  "content": [
    {"id": "<7-char hex>", "elType": "section", "settings": {}, "elements": [
      {"id": "<7-char hex>", "elType": "column", "settings": {"_column_size": 100}, "elements": [
        {"id": "<7-char hex>", "elType": "widget", "widgetType": "heading", "settings": {"title": "..."}}
      ]}
    ]}
  ]
}
Every id must be unique. Use only real Elementor widgetType values (heading, text-editor, button, image, icon-box, etc.).
If the user attaches a screenshot, rebuild that design as closely as possible in this JSON format.
No text outside the code block."""

CHAT_PROMPT = "You are a helpful AI assistant. Answer clearly in the language the user uses."

AGENT_PROMPT = """You are a WordPress + Elementor assistant connected to the user's real site via tools.
- To build a page, call create_elementor_page with valid Elementor structure (unique 7-char hex ids, section > column > widget, real widgetType values).
- Always create as 'draft' unless the user explicitly says publish.
- Before updating an existing page, call list_pages to find its id, then pass post_id.
- Only call trash_page when the user explicitly asks to delete a page.
- After a tool succeeds, reply briefly with the edit_url and view_url.
Reply in the language the user uses."""

WP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_elementor_page",
            "description": "Create or update a WordPress page built with Elementor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "elementor_data": {"type": "array", "items": {"type": "object"}},
                    "status": {"type": "string", "enum": ["draft", "publish"]},
                    "post_id": {"type": "integer"},
                },
                "required": ["title", "elementor_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pages",
            "description": "List existing WordPress pages.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trash_page",
            "description": "Move a WordPress page to trash.",
            "parameters": {
                "type": "object",
                "properties": {"post_id": {"type": "integer"}},
                "required": ["post_id"],
            },
        },
    },
]


# ---------- Intent Detection (ছবি তৈরি / এডিট / চ্যাট) ----------
CREATE_VERBS = r"(\b(create|make|generate|draw|design)\b|বানাও|বানিয়ে|তৈরি|আঁক)"
CREATE_NOUNS = r"(\b(logo|poster|banner|thumbnail|wallpaper|avatar|illustration|picture|image|photo)\b|লোগো|ছবি|পোস্টার|ব্যানার)"
EDIT_PATTERN = (
    r"(\b(remove|replace|change|edit|make|turn|wear|add)\b.*\b(background|clothes|shirt|suit|hair|colou?r|object|style)\b"
    r"|\bturn\b.*\binto\b|\bmake it\b"
    r"|ব্যাকগ্রাউন্ড|এডিট|বদলে|সরিয়ে|পরিবর্তন)"
)
FOLLOWUP_PATTERN = (
    r"(\b(darker|brighter|lighter|sharper|blur|crop|zoom|vintage|cartoon|black and white|recolou?r)\b"
    r"|রং বদল|রঙ বদল|উজ্জ্বল|সাদা-কালো|কার্টুন)"
)
NOT_VISUAL = r"(\b(code|python|html|css|javascript|js|wordpress|elementor|function|script|plugin|api|json)\b|কোড|ওয়ার্ডপ্রেস|এলিমেন্টর)"


def detect_visual_intent(text, has_image):
    t = text.lower()
    if has_image:
        return "edit" if re.search(EDIT_PATTERN, t) else "vision"
    if re.search(NOT_VISUAL, t):
        return "chat"
    if re.search(CREATE_VERBS, t) and re.search(CREATE_NOUNS, t):
        return "create"
    return "chat"


def wants_followup_edit(text):
    """আগের তৈরি করা ছবিটাই আবার বদলাতে চাইছে কি না"""
    t = text.lower()
    if re.search(CREATE_VERBS, t) and re.search(CREATE_NOUNS, t):
        return False
    return bool(re.search(EDIT_PATTERN, t) or re.search(FOLLOWUP_PATTERN, t))


def parse_command(text):
    """/create, /image, /edit, /chat দিয়ে জোর করে মোড ঠিক করা"""
    low = text.lower()
    for cmd, mode in (("/create", "create"), ("/image", "create"), ("/edit", "edit"), ("/chat", "chat")):
        if low == cmd or low.startswith(cmd + " "):
            return mode, text[len(cmd):].strip()
    return None, text


# ---------- Elementor / WordPress ----------
def fix_elementor(data):
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and "content" in data:
        data = data["content"]
    seen = set()

    def walk(nodes):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", ""))
            while not re.fullmatch(r"[0-9a-f]{7}", nid) or nid in seen:
                nid = uuid.uuid4().hex[:7]
            seen.add(nid)
            n["id"] = nid
            if not isinstance(n.get("settings"), dict):
                n["settings"] = {}
            if not isinstance(n.get("elements"), list):
                n["elements"] = []
            walk(n["elements"])

    walk(data)
    return data


def run_wp_tool(name, args):
    try:
        if name == "create_elementor_page":
            args["elementor_data"] = fix_elementor(args.get("elementor_data", []))
            args.setdefault("status", "draft")
            r = requests.post(
                f"{WP_URL}/wp-json/ai-builder/v1/page",
                json=args,
                auth=WP_AUTH,
                timeout=60,
            )
        elif name == "list_pages":
            r = requests.get(
                f"{WP_URL}/wp-json/wp/v2/pages?per_page=50&status=any&_fields=id,title,status,link",
                auth=WP_AUTH,
                timeout=30,
            )
        elif name == "trash_page":
            r = requests.delete(
                f"{WP_URL}/wp-json/wp/v2/pages/{int(args['post_id'])}",
                auth=WP_AUTH,
                timeout=30,
            )
        else:
            return json.dumps({"error": f"unknown tool {name}"})
        return r.text[:4000]
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_agent(model, system_text, history, temp, max_steps=5):
    msgs = [{"role": "system", "content": system_text}] + history
    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model,
            messages=msgs,
            tools=WP_TOOLS,
            tool_choice="auto",
            temperature=temp,
            max_tokens=8000,
        )
        m = resp.choices[0].message
        if not m.tool_calls:
            return m.content or ""
        msgs.append({
            "role": "assistant",
            "content": m.content or "",
            "tool_calls": [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {"name": t.function.name, "arguments": t.function.arguments},
                }
                for t in m.tool_calls
            ],
        })
        for t in m.tool_calls:
            with st.status(f"🔧 {t.function.name} চলছে...", expanded=False):
                try:
                    a = json.loads(t.function.arguments or "{}")
                except Exception:
                    a = {}
                result = run_wp_tool(t.function.name, a)
            msgs.append({"role": "tool", "tool_call_id": t.id, "content": result})
    return "⚠️ Maximum step অতিক্রম করেছে। আবার চেষ্টা করো।"


# ---------- Gemini (বড় ফাইল, ছবি-বোঝা, ছবি তৈরি/এডিট) ----------
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_headers():
    return {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}


def _gemini_error(r):
    try:
        return f"Gemini {r.status_code}: {r.json().get('error', {}).get('message', '')[:200]}"
    except Exception:
        return f"Gemini {r.status_code}: {r.text[:200]}"


def gemini_contents(history, use_image):
    contents = []
    last = len(history) - 1
    for i, m in enumerate(history):
        parts = [{"text": m["content"] or " "}]
        if use_image and i == last and m["role"] == "user" and m.get("image"):
            parts.append({"inlineData": {"mimeType": m.get("mime") or "image/png", "data": m["image"]}})
        contents.append({"role": "user" if m["role"] == "user" else "model", "parts": parts})
    return contents


def gemini_stream(system_text, history, temp, use_image):
    """Gemini থেকে লেখা একটু একটু করে আসে (SSE)"""
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": gemini_contents(history, use_image),
        "generationConfig": {"temperature": temp, "maxOutputTokens": 8000},
    }
    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:streamGenerateContent?alt=sse"
    with requests.post(url, headers=_gemini_headers(), json=body, stream=True, timeout=120) as r:
        if r.status_code != 200:
            raise RuntimeError(_gemini_error(r))
        got_text = False
        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            block = (obj.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise RuntimeError(f"Gemini অনুরোধ আটকে দিয়েছে ({block})")
            for cand in obj.get("candidates", []):
                for part in (cand.get("content") or {}).get("parts", []):
                    if part.get("text") and not part.get("thought"):
                        got_text = True
                        yield part["text"]
        if not got_text:
            raise RuntimeError("Gemini কোনো উত্তর দেয়নি")


def _prime(gen):
    """প্রথম অংশ এনে দেখে নেয় ঠিকঠাক চলছে কি না (না হলে ফলব্যাক করা যায়)"""
    try:
        first = next(gen)
    except StopIteration:
        raise RuntimeError("Gemini কোনো উত্তর দেয়নি")
    return itertools.chain([first], gen)


def _gemini_image_call(parts):
    body = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    r = requests.post(f"{GEMINI_BASE}/{GEMINI_IMAGE_MODEL}:generateContent",
                      headers=_gemini_headers(), json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(_gemini_error(r))
    for cand in r.json().get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"]), (blob.get("mimeType") or blob.get("mime_type") or "image/png")
    raise RuntimeError("Gemini ছবি দেয়নি (সেফটি ফিল্টার বা কোটা হতে পারে)")


def gemini_text_to_image(prompt, size_key):
    hint = {"পোর্ট্রেট": "portrait 2:3", "ল্যান্ডস্কেপ": "landscape 3:2"}
    ratio = next((v for k, v in hint.items() if k in size_key), "square 1:1")
    return _gemini_image_call([{"text": f"{prompt}\n\nAspect ratio: {ratio}."}])


def gemini_edit_image(image_bytes, prompt, mime):
    parts = [
        {"text": prompt},
        {"inlineData": {"mimeType": mime or "image/png", "data": base64.b64encode(image_bytes).decode()}},
    ]
    return _gemini_image_call(parts)


# ---------- Image generation / editing ----------
def _llm_rewrite(system_text, user_text):
    try:
        res = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            temperature=0.6,
            max_tokens=800,
        )
        text = (res.choices[0].message.content or "").strip()
        return text or user_text
    except Exception:
        return user_text


def enhance_image_prompt(user_prompt):
    """বাংলা বা সাধারণ প্রম্পটকে ছবির জন্য নিখুঁত ইংরেজি প্রম্পটে রূপান্তর করে"""
    return _llm_rewrite(
        "Convert the user's image request into a detailed, high-quality English image generation prompt. "
        "Output ONLY the English prompt, nothing else.",
        user_prompt,
    )


def enhance_edit_prompt(user_prompt):
    """এডিটের নির্দেশনাকে ছোট, স্পষ্ট ইংরেজি নির্দেশনায় রূপান্তর করে"""
    return _llm_rewrite(
        "Convert the user's image editing request into one short, clear English editing instruction "
        "(for example: 'Replace the background with a beach at sunset, keep the person unchanged'). "
        "Output ONLY the instruction, nothing else.",
        user_prompt,
    )


# --- OpenAI (gpt-image) ---
def _openai_check(r):
    if r.status_code == 200:
        return
    try:
        msg = r.json().get("error", {}).get("message", r.text[:200])
    except Exception:
        msg = r.text[:200]
    raise RuntimeError(f"OpenAI {r.status_code}: {msg}")


def openai_text_to_image(prompt, size, quality):
    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": OPENAI_IMAGE_MODEL, "prompt": prompt, "size": size, "quality": quality, "n": 1},
        timeout=180,
    )
    _openai_check(r)
    return base64.b64decode(r.json()["data"][0]["b64_json"])


def openai_edit_image(image_bytes, prompt, mime, quality):
    ext = "jpg" if "jpeg" in mime or "jpg" in mime else ("webp" if "webp" in mime else "png")
    r = requests.post(
        "https://api.openai.com/v1/images/edits",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"image": (f"image.{ext}", image_bytes, mime or "image/png")},
        data={"model": OPENAI_IMAGE_MODEL, "prompt": prompt, "quality": quality, "n": "1"},
        timeout=180,
    )
    _openai_check(r)
    return base64.b64decode(r.json()["data"][0]["b64_json"])


# --- Hugging Face (FLUX) ---
def _hf_call(fn):
    """HF InferenceClient: আগে provider=auto, না হলে fal-ai দিয়ে চেষ্টা"""
    from huggingface_hub import InferenceClient

    last_error = None
    for provider in ("auto", "fal-ai"):
        try:
            return fn(InferenceClient(provider=provider, api_key=HF_TOKEN))
        except Exception as e:
            last_error = e
    raise last_error


def _pil_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def hf_text_to_image(prompt, width, height):
    img = _hf_call(lambda c: c.text_to_image(prompt, model=HF_IMAGE_MODEL, width=width, height=height))
    return _pil_to_bytes(img)


def hf_edit_image(image_bytes, prompt):
    img = _hf_call(lambda c: c.image_to_image(image_bytes, prompt=prompt, model=HF_EDIT_MODEL))
    return _pil_to_bytes(img)


def _short(e):
    return str(e).replace("\n", " ")[:160]


def generate_ai_image(prompt, size_key, quality):
    """ছবি তৈরি: OpenAI → FLUX (HF) → ফ্রি Pollinations, যেটা আগে কাজ করে"""
    width, height, osize = IMAGE_SIZES[size_key]
    eng = enhance_image_prompt(prompt)
    notes = []

    if OPENAI_API_KEY:
        try:
            return {"bytes": openai_text_to_image(eng, osize, quality), "mime": "image/png",
                    "prompt": eng, "source": f"OpenAI ({OPENAI_IMAGE_MODEL})", "notes": notes}
        except Exception as e:
            notes.append(f"OpenAI ব্যর্থ: {_short(e)}")

    if GEMINI_API_KEY:
        try:
            data, gmime = gemini_text_to_image(eng, size_key)
            return {"bytes": data, "mime": gmime, "prompt": eng,
                    "source": f"Gemini ({GEMINI_IMAGE_MODEL})", "notes": notes}
        except Exception as e:
            notes.append(f"Gemini ব্যর্থ: {_short(e)}")

    if HF_TOKEN:
        try:
            return {"bytes": hf_text_to_image(eng, width, height), "mime": "image/png",
                    "prompt": eng, "source": "FLUX (Hugging Face)", "notes": notes}
        except Exception as e:
            notes.append(f"HF ব্যর্থ: {_short(e)}")

    try:
        seed = uuid.uuid4().int % 100000
        url = (
            "https://image.pollinations.ai/prompt/"
            f"{urllib.parse.quote(eng)}?width={width}&height={height}&nologo=true&seed={seed}"
        )
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image"):
            raise RuntimeError("ছবি পাওয়া যায়নি")
        return {"bytes": r.content, "mime": ctype, "prompt": eng, "source": "Pollinations", "notes": notes}
    except Exception as e:
        notes.append(f"Pollinations ব্যর্থ: {_short(e)}")
        raise RuntimeError("ছবি তৈরি করা যায়নি। " + " | ".join(notes))


def edit_ai_image(image_bytes, instruction, mime, quality):
    """OpenAI -> FLUX Kontext (HF) -> ফ্রি Groq Vision + Pollinations Flux ফলব্যাক"""
    eng = enhance_edit_prompt(instruction)
    notes = []
    mime = mime or "image/png"

    if OPENAI_API_KEY:
        try:
            return {
                "bytes": openai_edit_image(image_bytes, eng, mime, quality),
                "mime": "image/png",
                "prompt": eng,
                "source": f"OpenAI ({OPENAI_IMAGE_MODEL})",
                "notes": notes,
            }
        except Exception as e:
            notes.append(f"OpenAI ব্যর্থ: {_short(e)}")

    if GEMINI_API_KEY:
        try:
            data, gmime = gemini_edit_image(image_bytes, eng, mime)
            return {
                "bytes": data,
                "mime": gmime,
                "prompt": eng,
                "source": f"Gemini ({GEMINI_IMAGE_MODEL})",
                "notes": notes,
            }
        except Exception as e:
            notes.append(f"Gemini ব্যর্থ: {_short(e)}")

    if HF_TOKEN:
        try:
            return {
                "bytes": hf_edit_image(image_bytes, eng),
                "mime": "image/png",
                "prompt": eng,
                "source": "FLUX Kontext (Hugging Face)",
                "notes": notes,
            }
        except Exception as e:
            notes.append(f"HF ব্যর্থ: {_short(e)}")

    # ফ্রি ফলব্যাক: Groq Vision দিয়ে ছবির ডিজাইন বুঝে নতুন ছবি তৈরি (আসল ছবি এডিট হয় না)
    notes.append("আসল ছবি এডিট হয়নি। ছবির স্টাইল দেখে নতুন ছবি তৈরি হয়েছে (ফ্রি ফলব্যাক)।")
    combined_prompt = eng
    try:
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        desc_resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze the visual style, colors, layout, and typography of this image. "
                                "Write a short English image generation prompt (max 28 words) to recreate this "
                                f"same design style with this change: '{instruction}'. "
                                "Keep any requested name inside double quotes. Output ONLY the prompt."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_img}"}},
                    ],
                }
            ],
            temperature=0.5,
            max_tokens=600,
        )
        text = (desc_resp.choices[0].message.content or "").strip()
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()  # কিছু মডেল চিন্তা-অংশ পাঠায়
        if text:
            combined_prompt = text
    except Exception as e:
        notes.append(f"ছবি বিশ্লেষণ ব্যর্থ: {_short(e)}")

    seed = uuid.uuid4().int % 100000
    url = (
        "https://image.pollinations.ai/prompt/"
        f"{urllib.parse.quote(combined_prompt)}?width=1024&height=1024&model=flux&nologo=true&seed={seed}"
    )
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image"):
            raise RuntimeError("ছবি পাওয়া যায়নি")
    except Exception as e:
        notes.append(f"Pollinations ব্যর্থ: {_short(e)}")
        raise RuntimeError("ছবি এডিট করা যায়নি। " + " | ".join(notes))
    return {
        "bytes": r.content,
        "mime": ctype,
        "prompt": combined_prompt,
        "source": "Pollinations Flux (ফ্রি স্টাইল-এডিট)",
        "notes": notes,
    }


# ---------- Web search (Tavily) ----------
def search_query(user_text):
    return _llm_rewrite(
        "Rewrite the user's message as one concise web search query, in the same language as the message. "
        "Output ONLY the query.",
        user_text,
    )[:200]


def web_search(query, max_results=5):
    r = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
        json={"query": query, "search_depth": "basic", "max_results": max_results},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", [])


# ---------- File reader ----------
@st.cache_data(show_spinner=False)
def read_file(name, data: bytes):
    if name.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    return data.decode("utf-8", errors="ignore")


# ---------- SQLite ডেটাবেস (স্থায়ী চ্যাট হিস্ট্রি) ----------
@contextmanager
def _db():
    """কানেকশন কাজ শেষে commit/rollback করে বন্ধ করে দেয় (লিক হয় না)"""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_safe(fn, *args, default=None):
    """ডেটাবেস সমস্যা হলে অ্যাপ বন্ধ না করে শুধু সতর্কবার্তা দেখায়"""
    try:
        return fn(*args)
    except Exception as e:
        st.session_state["db_error"] = str(e)[:200]
        return default


def init_db():
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                file_name TEXT,
                file_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachment TEXT,
                image TEXT,
                mime TEXT,
                force TEXT,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id)")


def db_list_chats():
    with _db() as conn:
        return conn.execute(
            "SELECT id, title, file_name, file_text FROM chats ORDER BY updated_at ASC, rowid ASC"
        ).fetchall()


def db_load_messages(cid):
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content, attachment, image, mime, force FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (cid,),
        ).fetchall()
    out = []
    for role, content, attachment, image, mime, force in rows:
        m = {"role": role, "content": content}
        if attachment:
            m["attachment"] = attachment
        if image:
            m["image"] = image
        if mime:
            m["mime"] = mime
        if force:
            m["force"] = force
        out.append(m)
    return out


def db_add_message(cid, chat, msg):
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO chats (id, title, file_name, file_text, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                file_name = excluded.file_name,
                file_text = excluded.file_text,
                updated_at = CURRENT_TIMESTAMP
            """,
            (cid, chat["title"], chat.get("file_name"), chat.get("file_text")),
        )
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, attachment, image, mime, force) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, msg["role"], msg["content"], msg.get("attachment"), msg.get("image"), msg.get("mime"), msg.get("force")),
        )


def db_pop_last_message(cid):
    with _db() as conn:
        row = conn.execute("SELECT MAX(id) FROM messages WHERE chat_id = ?", (cid,)).fetchone()
        if row and row[0] is not None:
            conn.execute("DELETE FROM messages WHERE id = ?", (row[0],))
        left = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (cid,)).fetchone()[0]
        if left == 0:
            conn.execute("DELETE FROM chats WHERE id = ?", (cid,))


def db_save_meta(cid, chat):
    with _db() as conn:
        conn.execute(
            "UPDATE chats SET title = ?, file_name = ?, file_text = ? WHERE id = ?",
            (chat["title"], chat.get("file_name"), chat.get("file_text"), cid),
        )


def db_delete_chat(cid):
    with _db() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (cid,))
        conn.execute("DELETE FROM chats WHERE id = ?", (cid,))


# ---------- Chat session state ----------
def cur():
    """বর্তমান চ্যাট; মেসেজ প্রথমবার দরকার হলে ডেটাবেস থেকে লোড হয়"""
    chat = st.session_state.chats[st.session_state.current]
    if chat["messages"] is None:
        chat["messages"] = _db_safe(db_load_messages, st.session_state.current, default=[]) or []
    return chat


def trim_chats():
    chats = st.session_state.chats
    while len(chats) > MAX_CHATS:
        oldest = next((c for c in chats if c != st.session_state.current), None)
        if oldest is None:
            break
        del chats[oldest]
        _db_safe(db_delete_chat, oldest)


def new_chat():
    chats = st.session_state.chats
    current = st.session_state.get("current")
    st.session_state.pending = False
    if current in chats and chats[current]["messages"] == []:
        return  # খালি চ্যাট আছে, নতুন না বানিয়ে সেটাই ব্যবহার হবে
    cid = uuid.uuid4().hex[:8]
    chats[cid] = {"title": "নতুন চ্যাট", "messages": []}
    st.session_state.current = cid
    trim_chats()


def switch_chat(cid):
    chats = st.session_state.chats
    prev = st.session_state.current
    if prev != cid and prev in chats and chats[prev]["messages"] == []:
        del chats[prev]  # খালি, সংরক্ষিত নয় এমন চ্যাট বাদ
    st.session_state.current = cid
    st.session_state.pending = False


def delete_current_chat():
    chats = st.session_state.chats
    cid = st.session_state.current
    chats.pop(cid, None)
    _db_safe(db_delete_chat, cid)
    if chats:
        st.session_state.current = list(chats)[-1]
        st.session_state.pending = False
    else:
        new_chat()


def push_message(msg):
    cid = st.session_state.current
    chat = cur()
    chat["messages"].append(msg)
    if msg["role"] == "user":  # সাম্প্রতিক চ্যাট তালিকার ওপরে আসবে
        st.session_state.chats[cid] = st.session_state.chats.pop(cid)
    _db_safe(db_add_message, cid, chat, msg)


def pop_message():
    cid = st.session_state.current
    chat = cur()
    if chat["messages"]:
        chat["messages"].pop()
        _db_safe(db_pop_last_message, cid)
    if not chat["messages"]:
        chat["title"] = "নতুন চ্যাট"


if "db_ready" not in st.session_state:
    _db_safe(init_db)
    st.session_state.db_ready = True
if "chats" not in st.session_state:
    st.session_state.chats = {}
    for _cid, _title, _fname, _ftext in (_db_safe(db_list_chats, default=[]) or []):
        _c = {"title": _title, "messages": None}
        if _fname:
            _c["file_name"] = _fname
        if _ftext:
            _c["file_text"] = _ftext
        st.session_state.chats[_cid] = _c
    new_chat()  # প্রতিবার খালি নতুন চ্যাট দিয়ে শুরু, পুরনোগুলো সাইডবারে থাকবে
if "pending" not in st.session_state:
    st.session_state.pending = False
if "img_counter" not in st.session_state:
    st.session_state.img_counter = 0


def regen_cb():
    msgs = cur()["messages"]
    if msgs and msgs[-1]["role"] == "assistant":
        pop_message()
        st.session_state.pending = True


def total_file_chars():
    n = len(cur().get("file_text") or "")
    if file_context and uploaded_file is not None:
        n += len(file_context)
    return n


# ---------- সাইডবার ----------
with st.sidebar:
    st.markdown("## 🤖 আমার AI")
    if st.button("➕ নতুন চ্যাট", key="new_chat_btn"):
        new_chat()
        st.rerun()

    if st.session_state.get("db_error"):
        st.warning(f"⚠️ চ্যাট সেভ করা যাচ্ছে না: {st.session_state['db_error']}")

    st.caption("চ্যাট হিস্টরি")
    for cid in reversed(list(st.session_state.chats)):
        c = st.session_state.chats[cid]
        icon = "🟢" if cid == st.session_state.current else "💬"
        if st.button(f"{icon} {c['title'][:26]}", key=f"chat_{cid}"):
            switch_chat(cid)
            st.rerun()

    st.divider()
    app_mode = st.radio(
        "কাজের ধরন (Mode):",
        ["💬 সাধারণ চ্যাট", "📦 WordPress JSON তৈরি", "🔌 WordPress Agent", "🎨 ছবি তৈরি"],
    )
    is_json_mode = app_mode.startswith("📦")
    is_agent_mode = app_mode.startswith("🔌")
    is_image_mode = app_mode.startswith("🎨")

    with st.expander("⚙️ সেটিংস", expanded=False):
        preset_model = st.selectbox(
            "AI মডেল বেছে নিন:",
            ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        )
        custom_model = st.text_input("Custom model (ঐচ্ছিক):", placeholder="যেমন: qwen/qwen3.8-27b")
        selected_model = custom_model.strip() or preset_model

        temperature = st.slider(
            "Creativity (temperature)",
            0.0,
            1.0,
            0.4 if (is_json_mode or is_agent_mode) else 0.7,
            0.05,
            key=f"temp_{app_mode}",
        )

        size_key = st.selectbox("ছবির সাইজ:", list(IMAGE_SIZES))
        image_quality = st.selectbox("ছবির কোয়ালিটি (OpenAI):", IMAGE_QUALITIES)
        if GEMINI_API_KEY:
            engine_choice = st.selectbox(
                "🧠 AI ইঞ্জিন (চ্যাট ও JSON):",
                ["Groq (দ্রুত)", "Gemini (বড় ফাইল, ভালো ছবি-বোঝা)"],
            )
        else:
            engine_choice = "Groq (দ্রুত)"

    use_search = False
    if app_mode.startswith("💬"):
        if TAVILY_API_KEY:
            use_search = st.toggle("🌐 ওয়েব সার্চ", value=False)
        else:
            st.caption("🌐 ওয়েব সার্চ চালু করতে TAVILY_API_KEY সেট করো।")

    if is_agent_mode:
        if WP_URL and WP_AUTH[0] and WP_AUTH[1]:
            st.success(f"✅ Connected: {WP_URL}")
        else:
            st.warning("WP_URL, WP_USER, WP_APP_PASSWORD সেট করো।")
        st.caption("Tool calling-এর জন্য gpt-oss-120b বা qwen3.8-27b ব্যবহার করো।")
    if is_image_mode:
        providers = []
        if OPENAI_API_KEY:
            providers.append("OpenAI")
        if HF_TOKEN:
            providers.append("FLUX (HF)")
        providers.append("Pollinations (ফ্রি)")
        st.caption("ছবি তৈরি: " + " → ".join(providers))
        st.caption("ছবি দিলে বা আগের ছবির পর 'ব্যাকগ্রাউন্ড বদলাও' লিখলে এডিট হবে।" if HAS_EDIT
                   else "ছবি এডিট করতে OPENAI_API_KEY বা HF_TOKEN লাগবে।")
    if app_mode.startswith("💬"):
        st.caption("চ্যাটে 'লোগো বানাও' লিখলে ছবি তৈরি, ছবি দিয়ে 'ব্যাকগ্রাউন্ড বদলাও' লিখলে এডিট। জোর করতে লিখুন: /create, /edit বা /chat")

    file_context = ""
    uploaded_file = None
    uploaded_image = None
    with st.expander("📎 ফাইল ও ছবি", expanded=False):
        uploaded_file = st.file_uploader(
            "PDF, TXT, PY বা কোড ফাইল:",
            type=["pdf", "txt", "py", "html", "css", "js", "json", "md"],
        )
        if uploaded_file is not None:
            try:
                file_context = read_file(uploaded_file.name, uploaded_file.getvalue())
                if not file_context.strip():
                    st.warning("ফাইলে কোনো text পাওয়া যায়নি (scanned PDF হতে পারে)।")
                    file_context = ""
                else:
                    st.success(f"✅ '{uploaded_file.name}' লোড হয়েছে!")
                    if len(file_context) > MAX_FILE_CHARS:
                        st.info(f"ফাইল বড়, শুধু প্রথম {MAX_FILE_CHARS} অক্ষর ব্যবহার হবে।")
            except Exception as e:
                st.error(f"ফাইল পড়তে সমস্যা হয়েছে: {e}")

        uploaded_image = st.file_uploader(
            "ছবি দিন (AI ছবি দেখে উত্তর দেবে):",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"img_up_{st.session_state.img_counter}",
        )
        if uploaded_image is not None:
            st.image(uploaded_image.getvalue(), width=160)
            if is_agent_mode:
                st.caption("Agent মোডে ছবি ব্যবহার হবে না।")

    if cur().get("file_name"):
        st.caption(f"📎 এই চ্যাটের ফাইল: {cur()['file_name']}")
        if GEMINI_API_KEY and total_file_chars() > GROQ_FILE_LIMIT:
            st.caption("📚 ফাইল বড়, তাই উত্তর Gemini দেবে।")
        if st.button("❌ ফাইল সরান", key="clear_chat_file"):
            cur().pop("file_name", None)
            cur().pop("file_text", None)
            if cur()["messages"]:
                _db_safe(db_save_meta, st.session_state.current, cur())
            st.rerun()

    st.divider()
    with st.expander("✏️ চ্যাটের নাম বদলান", expanded=False):
        new_title = st.text_input("নতুন নাম:", value=cur()["title"], key=f"rename_{st.session_state.current}")
        if st.button("সেভ করুন", key="rename_btn") and new_title.strip():
            cur()["title"] = new_title.strip()[:40]
            if cur()["messages"]:
                _db_safe(db_save_meta, st.session_state.current, cur())
            st.rerun()

    if st.button("🗑️ এই চ্যাট মুছুন", key="del_chat"):
        delete_current_chat()
        st.rerun()

    if cur()["messages"]:
        export = [{k: v for k, v in m.items() if k not in ("image", "mime")} for m in cur()["messages"]]
        st.download_button(
            label="💾 চ্যাট সেভ করুন (.json)",
            data=json.dumps(export, indent=2, ensure_ascii=False),
            file_name="chat-history.json",
            mime="application/json",
        )

    if not APP_PASSWORD:
        st.caption("⚠️ APP_PASSWORD সেট না থাকলে অ্যাপের লিংক যার কাছে আছে সে সব চ্যাট দেখতে পারবে।")

# ---------- System prompt ----------
def build_system_instruction(cap=None):
    if is_agent_mode:
        base = AGENT_PROMPT
    elif is_json_mode:
        base = ELEMENTOR_PROMPT
    else:
        base = CHAT_PROMPT

    base = base + f"\n\nToday's date: {date.today().isoformat()}."

    cap = cap or GROQ_FILE_LIMIT
    files = []
    if cur().get("file_text"):
        files.append((cur().get("file_name", "attached file"), cur()["file_text"]))
    if file_context and uploaded_file is not None:
        files.append((uploaded_file.name, file_context))
    if not files:
        return base

    text = base + "\n\nThe user attached the following file(s). Use them to answer accurately:"
    for name, content in files:
        text += f"\n--- FILE START: {name} ---\n{content[:cap]}\n--- FILE END ---"
    return text


# ---------- Helper functions ----------
def extract_json(text):
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    candidate = m.group(1) if m else text.strip()
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def render_message(m, idx):
    if m.get("attachment"):
        st.caption(f"📎 {m['attachment']}")
    st.markdown(m["content"])
    if m.get("image"):
        raw = base64.b64decode(m["image"])
        if m["role"] == "user":
            st.image(raw, width=260)
        else:
            st.image(raw, width=512)
            ext = "jpg" if "jpeg" in m.get("mime", "") else "png"
            st.download_button(
                "📥 ছবি ডাউনলোড করুন",
                data=raw,
                file_name=f"ai-image-{idx}.{ext}",
                mime=m.get("mime", "image/png"),
                key=f"img_{st.session_state.current}_{idx}",
            )
    if m["role"] == "assistant":
        json_data = extract_json(m["content"])
        if json_data:
            st.download_button(
                label="📥 WordPress JSON ফাইল ডাউনলোড করুন",
                data=json_data,
                file_name=f"wordpress-template-{idx}.json",
                mime="application/json",
                key=f"dl_{st.session_state.current}_{idx}",
            )


def pick_engine():
    """Gemini কী থাকলে: নিজে বেছে নিলে বা ফাইল বড় হলে Gemini, নইলে Groq"""
    if not GEMINI_API_KEY or is_agent_mode:
        return "groq"
    if engine_choice.startswith("Gemini") or total_file_chars() > GROQ_FILE_LIMIT:
        return "gemini"
    return "groq"


def stream_reply(model, messages, temp):
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temp,
        max_tokens=8000,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def get_history():
    history = cur()["messages"][-MAX_HISTORY:]
    while history and history[0]["role"] != "user":
        history = history[1:]
    return history


def build_api_messages(history, system_text, use_vision):
    clean = [{"role": m["role"], "content": m["content"]} for m in history]
    if not use_vision:
        return [{"role": "system", "content": system_text}] + clean

    # Vision মডেলে system prompt আলাদা না দিয়ে user মেসেজের সাথে মিশিয়ে দেওয়া হয়
    last = history[-1]
    data_url = f"data:{last.get('mime', 'image/png')};base64,{last['image']}"
    clean[-1] = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"{system_text}\n\nUser request: {last['content']}"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }
    return clean


def get_edit_source(last_user):
    """এডিটের ছবি: এই মেসেজের সাথের ছবি, না থাকলে ঠিক আগের AI-র তৈরি ছবি"""
    if last_user.get("image"):
        return last_user["image"], last_user.get("mime", "image/png")
    msgs = cur()["messages"]
    if len(msgs) >= 2 and msgs[-2]["role"] == "assistant" and msgs[-2].get("image"):
        return msgs[-2]["image"], msgs[-2].get("mime", "image/png")
    return None


def resolve_intent(last_user):
    if is_agent_mode:
        return "agent"
    has_image = bool(last_user.get("image"))
    force = last_user.get("force")
    if force == "create":
        return "create"
    if force == "edit":
        return "edit"
    if force == "chat":
        return "vision" if has_image else "chat"

    followup = (
        not has_image
        and HAS_EDIT
        and get_edit_source(last_user) is not None
        and wants_followup_edit(last_user["content"])
    )
    if is_image_mode:
        return "edit" if (has_image or followup) else "create"
    if is_json_mode:
        return "vision" if has_image else "chat"
    if last_user.get("attachment"):
        return "vision" if has_image else "chat"
    if followup:
        return "edit"
    intent = detect_visual_intent(last_user["content"], has_image)
    if intent == "edit" and not HAS_EDIT:
        intent = "vision"  # এডিটের কী না থাকলে সাধারণ ছবি-বিশ্লেষণ
    return intent


# ---------- ইনপুট ----------
if is_agent_mode:
    placeholder = "যেমন: একটি dark theme architecture Home page বানাও..."
elif is_json_mode:
    placeholder = "যেমন: একটি মডার্ন Hero Section এর Elementor JSON তৈরি করো..."
elif is_image_mode:
    placeholder = "ছবির বর্ণনা লিখুন, অথবা ছবি দিয়ে কী বদলাতে চান লিখুন..."
else:
    placeholder = "এখানে লিখুন, অথবা ক্লিপ আইকন থেকে ছবি ও ফাইল দিন..."

user_input = st.chat_input(
    placeholder,
    accept_file=True,
    file_type=["png", "jpg", "jpeg", "webp", "pdf", "txt", "py", "html", "css", "js", "json", "md"],
)
messages = cur()["messages"]

if user_input:
    force, prompt = parse_command((user_input.text or "").strip())
    inline_img_bytes = None
    inline_img_mime = None
    attach_name = None
    can_use_image = not is_agent_mode

    if user_input.files:
        f = user_input.files[0]
        if f.type and f.type.startswith("image/"):
            inline_img_bytes = f.getvalue()
            inline_img_mime = f.type
            if not can_use_image:
                st.info("Agent মোডে ছবি ব্যবহার হবে না। সাধারণ চ্যাট, JSON বা ছবি মোডে ছবি দাও।")
        else:
            try:
                extracted = read_file(f.name, f.getvalue())
            except Exception as e:
                extracted = None
                st.warning(f"ফাইল পড়তে সমস্যা হয়েছে: {e}")
            if extracted and extracted.strip():
                cur()["file_name"] = f.name
                cur()["file_text"] = extracted[:MAX_FILE_CHARS]
                attach_name = f.name
                if len(extracted) > MAX_FILE_CHARS:
                    st.info(f"ফাইল বড়, শুধু প্রথম {MAX_FILE_CHARS} অক্ষর ব্যবহার হবে।")
            elif extracted is not None:
                st.warning("ফাইলে কোনো text পাওয়া যায়নি (scanned PDF হতে পারে)।")

    if is_image_mode and inline_img_bytes and not prompt:
        st.info("ছবিটি এডিট করতে কী পরিবর্তন করতে চাও তা লিখে দাও।")

    if not prompt:
        if inline_img_bytes and can_use_image and not is_image_mode:
            prompt = "এই ছবিটি বিস্তারিত বিশ্লেষণ করে বর্ণনা করো।"
        elif attach_name:
            prompt = f"'{attach_name}' ফাইলটি বিশ্লেষণ করে সংক্ষেপে বোঝাও।"

    if prompt:
        user_msg = {"role": "user", "content": prompt}
        if force:
            user_msg["force"] = force
        if attach_name:
            user_msg["attachment"] = attach_name

        # চ্যাট বক্সের ভেতর থেকে ছবি দিলে সেটি নেওয়া হবে
        if inline_img_bytes and can_use_image:
            if len(inline_img_bytes) > MAX_IMAGE_BYTES:
                st.warning("ছবি ৩MB-এর বেশি বড়, তাই যুক্ত করা হয়নি। ছোট ছবি দাও।")
            else:
                user_msg["image"] = base64.b64encode(inline_img_bytes).decode()
                user_msg["mime"] = inline_img_mime or "image/png"
        # অথবা সাইডবার থেকে ছবি দিলে সেটি নেওয়া হবে
        elif uploaded_image is not None and can_use_image:
            raw_img = uploaded_image.getvalue()
            if len(raw_img) > MAX_IMAGE_BYTES:
                st.warning("ছবি ৩MB-এর বেশি বড়, তাই যুক্ত করা হয়নি। ছোট ছবি দাও।")
            else:
                user_msg["image"] = base64.b64encode(raw_img).decode()
                user_msg["mime"] = uploaded_image.type or "image/png"
                st.session_state.img_counter += 1

        if cur()["title"] == "নতুন চ্যাট":
            cur()["title"] = prompt.replace("\n", " ")[:30]
        push_message(user_msg)
        st.session_state.pending = True

# ---------- মেসেজ দেখানো ----------
if not messages and not st.session_state.pending:
    st.markdown(
        "<h2 style='text-align:center; margin-top:18vh;'>আজ কীভাবে সাহায্য করতে পারি?</h2>",
        unsafe_allow_html=True,
    )

last_idx = len(messages) - 1
for idx, m in enumerate(messages):
    with st.chat_message(m["role"]):
        render_message(m, idx)
        if (
            m["role"] == "assistant"
            and idx == last_idx
            and not st.session_state.pending
            and not is_agent_mode
        ):
            st.button("🔄 আবার তৈরি করো", key="regen_btn", on_click=regen_cb)

# ---------- উত্তর তৈরি ----------
if st.session_state.pending:
    if not messages or messages[-1]["role"] != "user":
        st.session_state.pending = False
    else:
        last_user = messages[-1]
        success = False
        with st.chat_message("assistant"):
            try:
                intent = resolve_intent(last_user)
                if intent in ("create", "edit"):
                    if intent == "create":
                        with st.spinner("🎨 ছবি তৈরি হচ্ছে..."):
                            img = generate_ai_image(last_user["content"], size_key, image_quality)
                        label = "🎨 ছবি তৈরি হয়েছে।"
                    else:
                        src = get_edit_source(last_user)
                        if not src:
                            raise RuntimeError("এডিট করার মতো কোনো ছবি পাওয়া যায়নি। আগে একটি ছবি দাও।")
                        with st.spinner("🖌️ ছবি এডিট হচ্ছে..."):
                            img = edit_ai_image(base64.b64decode(src[0]), last_user["content"], src[1], image_quality)
                        label = "🖌️ ছবি এডিট হয়েছে।"
                    note_text = f"\n\n⚠️ {' | '.join(img['notes'])}" if img.get("notes") else ""
                    reply_msg = {
                        "role": "assistant",
                        "content": f"{label}\n\n**Prompt:** {img['prompt']}\n\n*Source: {img['source']}*{note_text}",
                        "image": base64.b64encode(img["bytes"]).decode(),
                        "mime": img["mime"],
                    }
                    render_message(reply_msg, len(messages))
                elif intent == "agent":
                    if not (WP_URL and WP_AUTH[0] and WP_AUTH[1]):
                        raise ValueError("WP_URL, WP_USER, WP_APP_PASSWORD সেট করা নেই।")
                    history = [{"role": m["role"], "content": m["content"]} for m in get_history()]
                    with st.spinner("ভাবছি..."):
                        reply = run_agent(selected_model, build_system_instruction(GROQ_FILE_LIMIT), history, temperature)
                    st.markdown(reply)
                    reply_msg = {"role": "assistant", "content": reply}
                else:
                    history = get_history()
                    use_vision = bool(history and history[-1].get("image"))
                    search_block = ""
                    sources_md = ""

                    if use_search and TAVILY_API_KEY and intent == "chat" and app_mode.startswith("💬"):
                        try:
                            with st.spinner("🌐 ওয়েবে খোঁজা হচ্ছে..."):
                                results = web_search(search_query(last_user["content"]))
                            if results:
                                blocks, links = [], []
                                for i, item in enumerate(results, 1):
                                    title = (item.get("title") or "Source").replace("[", "(").replace("]", ")")
                                    url = item.get("url", "")
                                    blocks.append(f"[{i}] {title}\n{url}\n{(item.get('content') or '')[:700]}")
                                    links.append(f"{i}. [{title}]({url})")
                                search_block = (
                                    "\n\nWEB SEARCH RESULTS (use them when relevant, cite inline like [1], [2]; "
                                    "if they are not enough, say so):\n" + "\n\n".join(blocks)
                                )
                                sources_md = "\n\n**🌐 সূত্র:**\n" + "\n".join(links)
                        except Exception as e:
                            st.warning(f"ওয়েব সার্চ ব্যর্থ হয়েছে: {_short(e)}")

                    stream = None
                    if pick_engine() == "gemini":
                        try:
                            stream = _prime(gemini_stream(
                                build_system_instruction(MAX_FILE_CHARS) + search_block,
                                history, temperature, use_vision,
                            ))
                        except Exception as e:
                            st.warning(f"⚠️ Gemini ব্যর্থ ({_short(e)}), Groq দিয়ে উত্তর দেওয়া হচ্ছে।")

                    if stream is None:
                        sys_text = build_system_instruction(GROQ_FILE_LIMIT) + search_block
                        model_to_use = VISION_MODEL if use_vision else selected_model
                        api_messages = build_api_messages(history, sys_text, use_vision)
                        stream = stream_reply(model_to_use, api_messages, temperature)

                    reply = st.write_stream(stream)
                    if sources_md:
                        st.markdown(sources_md)
                        reply += sources_md
                    reply_msg = {"role": "assistant", "content": reply}

                push_message(reply_msg)
                success = True
            except Exception as e:
                pop_message()
                st.error(f"একটি সমস্যা হয়েছে: {e}")

        st.session_state.pending = False
        if success:
            st.rerun()
