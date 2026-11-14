import os
import json
import re
import streamlit as st
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

st.set_page_config(page_title="আমার পার্সোনাল AI", page_icon="🤖", layout="centered")
st.title("🤖 আমার পার্সোনাল AI চ্যাটবট")
st.caption("WordPress JSON Generator ও সাধারণ চ্যাট সহকারী")

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    st.error("GROQ_API_KEY পাওয়া যায়নি। .env ফাইলে key সেট করো।")
    st.stop()

client = Groq(api_key=api_key)

MAX_HISTORY = 12  # শেষ কতগুলো মেসেজ API-তে পাঠানো হবে

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

# ---------- সাইডবার ----------
with st.sidebar:
    st.header("⚙️ সেটিংস")
    selected_model = st.selectbox(
        "AI মডেল বেছে নিন:",
        ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
    )
    app_mode = st.radio("কাজের ধরন (Mode):", ["💬 সাধারণ চ্যাট", "📦 WordPress JSON তৈরি"])
    temperature = st.slider("Creativity (temperature)", 0.0, 1.0,
                            0.4 if app_mode.startswith("📦") else 0.7, 0.05)

    if st.button("🗑️ নতুন চ্যাট শুরু করুন"):
        st.session_state.messages = []
        st.rerun()

    if st.session_state.get("messages"):
        st.download_button(
            "💾 চ্যাট সেভ করুন (.json)",
            data=json.dumps(st.session_state.messages, indent=2, ensure_ascii=False),
            file_name="chat-history.json",
            mime="application/json",
        )

system_instruction = ELEMENTOR_PROMPT if app_mode.startswith("📦") else CHAT_PROMPT

if "messages" not in st.session_state:
    st.session_state.messages = []


# ---------- Helper functions ----------
def extract_json(text):
    m = re.search(r"```(?:json)?\s*([\s\(\S\)]*?)\s*```", text)
    candidate = m.group(1) if m else text.strip()
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(parsed, (dict, list)):  # শুধু object/array
        return None
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def show_download(content, idx):
    json_data = extract_json(content)
    if json_data:
        st.download_button(
            label="📥 WordPress JSON ফাইল ডাউনলোড করুন",
            data=json_data,
            file_name=f"wordpress-template-{idx}.json",
            mime="application/json",
            key=f"dl_{idx}",
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


# ---------- আগের মেসেজ দেখানো ----------
for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            show_download(message["content"], idx)

# ---------- নতুন ইনপুট ----------
placeholder = ("যেমন: একটি মডার্ন Hero Section এর Elementor JSON তৈরি করো..."
               if app_mode.startswith("📦") else "এখানে আপনার প্রশ্ন লিখুন...")

if prompt := st.chat_input(placeholder):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            api_messages = [{"role": "system", "content": system_instruction}] \
                + st.session_state.messages[-MAX_HISTORY:]

            reply = st.write_stream(stream_reply(selected_model, api_messages, temperature))
            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.rerun()

        except Exception as e:
            st.session_state.messages.pop()  # ব্যর্থ হলে user মেসেজ সরিয়ে দাও
            st.error(f"একটি সমস্যা হয়েছে: {e}")
