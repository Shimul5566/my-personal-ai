import io
import os
import re
import json
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv
from groq import Groq
from pypdf import PdfReader

load_dotenv()

st.set_page_config(page_title="আমার পার্সোনাল AI", page_icon="🤖", layout="centered")


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
    st.title("🔒 লগইন")
    pw = st.text_input("পাসওয়ার্ড দিন:", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("ভুল পাসওয়ার্ড")
    st.stop()

st.title("🤖 আমার পার্সোনাল AI চ্যাটবট")
st.caption("ফাইল রিডার, WordPress JSON Generator, WordPress Agent ও সাধারণ চ্যাট")

api_key = get_secret("GROQ_API_KEY")
if not api_key:
    st.error("GROQ_API_KEY পাওয়া যায়নি। Secrets বা .env ফাইলে key সেট করো।")
    st.stop()

client = Groq(api_key=api_key)

WP_URL = get_secret("WP_URL").rstrip("/")
WP_AUTH = (get_secret("WP_USER"), get_secret("WP_APP_PASSWORD"))

MAX_HISTORY = 12
MAX_FILE_CHARS = 20000

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


@st.cache_data(show_spinner=False)
def read_file(name, data: bytes):
    if name.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    return data.decode("utf-8", errors="ignore")


with st.sidebar:
    st.header("⚙️ সেটিংস")
    preset_model = st.selectbox(
        "AI মডেল বেছে নিন:",
        ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
    )
    custom_model = st.text_input("Custom model (ঐচ্ছিক):", placeholder="যেমন: qwen/qwen3.8-27b")
    selected_model = custom_model.strip() or preset_model

    app_mode = st.radio(
        "কাজের ধরন (Mode):",
        ["💬 সাধারণ চ্যাট", "📦 WordPress JSON তৈরি", "🔌 WordPress Agent"],
    )
    is_json_mode = app_mode.startswith("📦")
    is_agent_mode = app_mode.startswith("🔌")

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

    st.divider()
    st.subheader("📎 স্থায়ী ফাইল আপলোড")
    uploaded_file = st.file_uploader(
        "PDF, TXT, PY বা কোড ফাইল দিন:",
        type=["pdf", "txt", "py", "html", "css", "js", "json", "md"],
    )

    file_context = ""
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

    st.divider()
    if st.button("🗑️ নতুন চ্যাট শুরু করুন"):
        st.session_state.messages = []
        st.rerun()

    if st.session_state.get("messages"):
        st.download_button(
            label="💾 চ্যাট সেভ করুন (.json)",
            data=json.dumps(st.session_state.messages, indent=2, ensure_ascii=False),
            file_name="chat-history.json",
            mime="application/json",
        )

if is_agent_mode:
    base_instruction = AGENT_PROMPT
elif is_json_mode:
    base_instruction = ELEMENTOR_PROMPT
else:
    base_instruction = CHAT_PROMPT

if file_context:
    system_instruction = (
        f"{base_instruction}\n\n"
