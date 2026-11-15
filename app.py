import io
import os
import re
import json
import uuid
import base64
import urllib.parse

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

# --- ChatGPT স্টাইল কাস্টম CSS ---
st.markdown(
    """
    <style>
    .stApp {
        background-color: #212121;
        color: #ececec;
    }
    [data-testid="stSidebar"] {
        background-color: #171717;
        border-right: 1px solid #2f2f2f;
    }
    .block-container {
        max-width: 820px;
        padding-top: 2rem;
        padding-bottom: 6rem;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background-color: #2f2f2f;
        border-radius: 18px;
        padding: 12px 18px;
        margin-left: auto;
        width: fit-content;
        max-width: 80%;
        border: 1px solid #3c3c3c;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
        background-color: transparent;
        padding: 12px 0px;
    }
    [data-testid="stSidebar"] .stButton > button {
        width: 100%;
        border-radius: 10px;
        background-color: #212121;
        color: #ececec;
        border: 1px solid #383838;
        text-align: left;
        padding: 8px 12px;
        transition: all 0.2s ease;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: #2f2f2f;
        border-color: #555555;
    }
    [data-testid="stChatInput"] {
        background-color: #2f2f2f !important;
        border-radius: 24px !important;
        border: 1px solid #424242 !important;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header[data-testid="stHeader"] {
        background-color: rgba(33, 33, 33, 0.8);
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

HF_TOKEN = get_secret("HF_TOKEN")
client = Groq(api_key=api_key)

WP_URL = get_secret("WP_URL").rstrip("/")
WP_AUTH = (get_secret("WP_USER"), get_secret("WP_APP_PASSWORD"))

MAX_HISTORY = 12
MAX_FILE_CHARS = 20000
MAX_IMAGE_BYTES = 3_000_000
MAX_CHATS = 20
VISION_MODEL = "qwen/qwen3.8-27b"  # Groq-এর ছবি দেখার (Vision) মডেল
HF_IMAGE_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

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


# ---------- Image generation ----------
def enhance_image_prompt(user_prompt):
    """বাংলা বা সাধারণ প্রম্পটকে ছবির জন্য নিখুঁত ইংরেজি প্রম্পটে রূপান্তর করে"""
    try:
        res = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": "Convert the user's image request into a detailed, high-quality English image generation prompt. Output ONLY the English prompt, nothing else.",
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=800,
        )
        text = (res.choices[0].message.content or "").strip()
        return text or user_prompt
    except Exception:
        return user_prompt


def generate_ai_image(prompt):
    """ছবি তৈরি: HF_TOKEN থাকলে FLUX, না থাকলে (বা fail করলে) ফ্রি Pollinations"""
    eng_prompt = enhance_image_prompt(prompt)

    if HF_TOKEN:
        try:
            r = requests.post(
                HF_IMAGE_URL,
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                json={"inputs": eng_prompt},
                timeout=120,
            )
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image"):
                return {"bytes": r.content, "mime": ctype.split(";")[0],
                        "prompt": eng_prompt, "source": "FLUX (Hugging Face)"}
        except Exception:
            pass

    seed = uuid.uuid4().int % 100000
    url = (
        "https://image.pollinations.ai/prompt/"
        f"{urllib.parse.quote(eng_prompt)}?width=1024&height=1024&nologo=true&seed={seed}"
    )
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
    if not ctype.startswith("image"):
        raise RuntimeError("ছবি তৈরি করা যায়নি, একটু পরে আবার চেষ্টা করো।")
    return {"bytes": r.content, "mime": ctype, "prompt": eng_prompt, "source": "Pollinations"}


# ---------- File reader ----------
@st.cache_data(show_spinner=False)
def read_file(name, data: bytes):
    if name.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    return data.decode("utf-8", errors="ignore")


# ---------- Chat session state ----------
def new_chat():
    cid = uuid.uuid4().hex[:8]
    st.session_state.chats[cid] = {"title": "নতুন চ্যাট", "messages": []}
    st.session_state.current = cid
    st.session_state.pending = False
    while len(st.session_state.chats) > MAX_CHATS:
        oldest = next(iter(st.session_state.chats))
        if oldest == cid:
            break
        del st.session_state.chats[oldest]


if "chats" not in st.session_state:
    st.session_state.chats = {}
    new_chat()
if "pending" not in st.session_state:
    st.session_state.pending = False
if "img_counter" not in st.session_state:
    st.session_state.img_counter = 0


def cur():
    return st.session_state.chats[st.session_state.current]


def regen_cb():
    msgs = cur()["messages"]
    if msgs and msgs[-1]["role"] == "assistant":
        msgs.pop()
        st.session_state.pending = True


# ---------- সাইডবার ----------
with st.sidebar:
    st.markdown("## 🤖 আমার AI")
    if st.button("➕ নতুন চ্যাট", key="new_chat_btn"):
        new_chat()
        st.rerun()

    st.caption("চ্যাট হিস্টরি")
    for cid in reversed(list(st.session_state.chats)):
        c = st.session_state.chats[cid]
        icon = "🟢" if cid == st.session_state.current else "💬"
        if st.button(f"{icon} {c['title'][:26]}", key=f"chat_{cid}"):
            st.session_state.current = cid
            st.session_state.pending = False
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

    if is_agent_mode:
        if WP_URL and WP_AUTH[0] and WP_AUTH[1]:
            st.success(f"✅ Connected: {WP_URL}")
        else:
            st.warning("WP_URL, WP_USER, WP_APP_PASSWORD সেট করো।")
        st.caption("Tool calling-এর জন্য gpt-oss-120b বা qwen3.8-27b ব্যবহার করো।")
    if is_image_mode:
        st.caption("HF_TOKEN থাকলে FLUX, না থাকলে ফ্রি জেনারেটর ব্যবহার হবে।")

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
            if is_agent_mode or is_image_mode:
                st.caption("এই মোডে ছবি ব্যবহার হবে না।")

    st.divider()
    if st.button("🗑️ এই চ্যাট মুছুন", key="del_chat"):
        del st.session_state.chats[st.session_state.current]
        if not st.session_state.chats:
            new_chat()
        else:
            st.session_state.current = list(st.session_state.chats)[-1]
        st.session_state.pending = False
        st.rerun()

    if cur()["messages"]:
        export = [{k: v for k, v in m.items() if k not in ("image", "mime")} for m in cur()["messages"]]
        st.download_button(
            label="💾 চ্যাট সেভ করুন (.json)",
            data=json.dumps(export, indent=2, ensure_ascii=False),
            file_name="chat-history.json",
            mime="application/json",
        )

# ---------- System prompt ----------
if is_agent_mode:
    base_instruction = AGENT_PROMPT
elif is_json_mode:
    base_instruction = ELEMENTOR_PROMPT
else:
    base_instruction = CHAT_PROMPT

if file_context:
    system_instruction = (
        f"{base_instruction}\n\n"
        f"The user uploaded a file named '{uploaded_file.name}'. "
        f"Use the following file content to answer their questions accurately:\n"
        f"--- FILE CONTENT START ---\n{file_context[:MAX_FILE_CHARS]}\n--- FILE CONTENT END ---"
    )
else:
    system_instruction = base_instruction


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


# ---------- ইনপুট ----------
if is_agent_mode:
    placeholder = "যেমন: একটি dark theme architecture Home page বানাও..."
elif is_json_mode:
    placeholder = "যেমন: একটি মডার্ন Hero Section এর Elementor JSON তৈরি করো..."
elif is_image_mode:
    placeholder = "যেমন: সূর্যাস্তের সময় নদীর ধারে একটি নৌকা..."
else:
    placeholder = "এখানে আপনার প্রশ্ন লিখুন..."

prompt = st.chat_input(placeholder)
messages = cur()["messages"]

if prompt:
    user_msg = {"role": "user", "content": prompt}
    if uploaded_image is not None and not (is_agent_mode or is_image_mode):
        raw_img = uploaded_image.getvalue()
        if len(raw_img) > MAX_IMAGE_BYTES:
            st.warning("ছবি ৩MB-এর বেশি বড়, তাই যুক্ত করা হয়নি। ছোট ছবি দাও।")
        else:
            user_msg["image"] = base64.b64encode(raw_img).decode()
            user_msg["mime"] = uploaded_image.type or "image/png"
            st.session_state.img_counter += 1  # আপলোডার খালি করে দেয়
    if cur()["title"] == "নতুন চ্যাট":
        cur()["title"] = prompt.strip().replace("\n", " ")[:30]
    messages.append(user_msg)
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
                if is_image_mode:
                    with st.spinner("🎨 ছবি তৈরি হচ্ছে..."):
                        img = generate_ai_image(last_user["content"])
                    reply_msg = {
                        "role": "assistant",
                        "content": f"🎨 ছবি তৈরি হয়েছে।\n\n**Prompt:** {img['prompt']}\n\n*Source: {img['source']}*",
                        "image": base64.b64encode(img["bytes"]).decode(),
                        "mime": img["mime"],
                    }
                    render_message(reply_msg, len(messages))
                elif is_agent_mode:
                    if not (WP_URL and WP_AUTH[0] and WP_AUTH[1]):
                        raise ValueError("WP_URL, WP_USER, WP_APP_PASSWORD সেট করা নেই।")
                    history = [{"role": m["role"], "content": m["content"]} for m in get_history()]
                    with st.spinner("ভাবছি..."):
                        reply = run_agent(selected_model, system_instruction, history, temperature)
                    st.markdown(reply)
                    reply_msg = {"role": "assistant", "content": reply}
                else:
                    history = get_history()
                    use_vision = bool(history and history[-1].get("image"))
                    model_to_use = VISION_MODEL if use_vision else selected_model
                    api_messages = build_api_messages(history, system_instruction, use_vision)
                    reply = st.write_stream(stream_reply(model_to_use, api_messages, temperature))
                    reply_msg = {"role": "assistant", "content": reply}

                messages.append(reply_msg)
                success = True
            except Exception as e:
                messages.pop()
                st.error(f"একটি সমস্যা হয়েছে: {e}")

        st.session_state.pending = False
        if success:
            st.rerun()
