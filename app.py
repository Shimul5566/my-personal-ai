import os
import streamlit as st
from dotenv import load_dotenv
from groq import Groq

# .env ফাইল থেকে API Key লোড করা হচ্ছে
load_dotenv()

# পেজের টাইটেল এবং আইকন সেট করা
st.set_page_config(page_title="আমার পার্সোনাল AI", page_icon="🤖", layout="centered")

st.title("🤖 আমার পার্সোনাল AI চ্যাটবট")
st.caption("Powered by Groq Cloud (সম্পূর্ণ ফ্রি ও আনলিমিটেড)")

# Groq ক্লায়েন্ট সেটআপ
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# সাইডবারে মডেল পরিবর্তন ও চ্যাট ক্লিয়ার করার অপশন
with st.sidebar:
    st.header("⚙️ সেটিংস")
    selected_model = st.selectbox(
        "AI মডেল বেছে নিন:",
        ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    )
    if st.button("🗑️ নতুন চ্যাট শুরু করুন"):
        st.session_state.messages = [
            {"role": "system", "content": "You are a helpful AI assistant. Answer clearly in the language the user uses."}
        ]
        st.rerun()

# চ্যাট হিস্ট্রি মেমোরিতে রাখা
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "You are a helpful AI assistant. Answer clearly in the language the user uses."}
    ]

# আগের মেসেজগুলো স্ক্রিনে দেখানো (system মেসেজ বাদে)
for message in st.session_state.messages:
    if message["role"] != "system":
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# ইউজারের নতুন ইনপুট বক্স
if prompt := st.chat_input("এখানে আপনার প্রশ্ন লিখুন..."):
    # ইউজারের মেসেজ স্ক্রিনে দেখানো ও সেভ করা
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # AI-এর উত্তর জেনারেট করা
    with st.chat_message("assistant"):
        try:
            response = client.chat.completions.create(
                model=selected_model,
                messages=st.session_state.messages,
                temperature=0.7,
            )
            reply = response.choices[0].message.content
            st.markdown(reply)
            # AI-এর উত্তর মেমোরিতে সেভ রাখা
            st.session_state.messages.append({"role": "assistant", "content": reply})
        except Exception as e:
            st.error(f"একটি সমস্যা হয়েছে: {e}")


