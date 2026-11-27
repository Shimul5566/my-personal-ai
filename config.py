"""Codexio v3.0.0 — configuration.

Secrets loader, shared constants and starter prompts.
This module must stay dependency-free (no imports from core/ or studios/) so that nothing can import in a circle.
No Streamlit calls happen at import time (st.set_page_config must remain the first Streamlit call in app.py).
"""
import os

APP_VERSION = "3.0.0"  # Modular architecture + Live Deploy + Encrypted multi-site manager + Usage tracker


def get_secret(name, default=""):
    """Environment variable first, then Streamlit secrets (Cloud) — never raises."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


# ---- Claude (Anthropic) — used ONLY by the 3 tools in CLAUDE_TOOLS to save API credits ----
# Sidebar label -> exact model ID. The first entry is the default.
CLAUDE_MODELS = {
    "Claude Sonnet 5.5 (Best for WordPress JSON & MCP)": "claude-sonnet-5-5",
    "Claude Haiku 5.5 (Fast & Low Cost)": "claude-haiku-5-5",
    "Claude Fable 5.2 (Complex Reasoning)": "claude-fable-5-2",
}
CLAUDE_DEFAULT_LABEL = next(iter(CLAUDE_MODELS))
CLAUDE_DEFAULT_MODEL = CLAUDE_MODELS[CLAUDE_DEFAULT_LABEL]

# The ONLY tools allowed to call Claude. Everything else (general chat, App Builder, Webflow, Agent without MCP ...)
# stays on Groq / Gemini.
CLAUDE_TOOLS = frozenset({"wp_json", "ai_automation", "elementor_mcp"})


def claude_enabled_for(tool):
    """True if `tool` is one of the 3 Claude-routed tools (wp_json / ai_automation / elementor_mcp)."""
    return tool in CLAUDE_TOOLS


# ---- Groq free-tier safety for App Builder (TPM limit causes 413/429 with bigger values) ----
GROQ_APP_BUDGET = 3500   # max input tokens sent to Groq in App Builder fallback
GROQ_APP_MAX_OUT = 3500  # max output tokens from Groq in App Builder fallback

# ---- App Builder starter chips: (button label, full prompt) ----
STARTER_CHIPS = [
    (
        "🛒 E-commerce Store (bKash/Nagad)",
        "একটি মোবাইল-ফার্স্ট ই-কমার্স স্টোর অ্যাপ বানাও: প্রোডাক্ট গ্রিড (ছবির জায়গায় গ্রেডিয়েন্ট+ইমোজি), ক্যাটেগরি ফিল্টার, সার্চ, "
        "কার্ট (পরিমাণ বাড়ানো/কমানো, মোট দাম ৳ টাকায়), চেকআউট ফর্ম (নাম, ফোন, ঠিকানা), পেমেন্ট অপশন: bKash / Nagad / Cash on Delivery "
        "(শুধু ডেমো UI), অর্ডার কনফার্মেশন স্ক্রিন। ডার্ক থিম, সুন্দর অ্যানিমেশন, কার্ট যেন রিফ্রেশে হারিয়ে না যায়।",
    ),
    (
        "💰 Personal Expense Tracker",
        "একটি পার্সোনাল খরচ ট্র্যাকার অ্যাপ বানাও: আয়/খরচ যোগ করা (পরিমাণ, ক্যাটেগরি, তারিখ, নোট), মাসিক সামারি কার্ড (মোট আয়, "
        "মোট খরচ, ব্যালেন্স ৳), ক্যাটেগরি অনুযায়ী ডোনাট চার্ট, লেনদেনের তালিকা (এডিট/ডিলিট), মাসিক বাজেট সেট করে সীমা পার হলে সতর্কবার্তা। "
        "ডার্ক থিম, মোবাইল-ফ্রেন্ডলি, ডেটা যেন রিফ্রেশে হারিয়ে না যায়।",
    ),
    (
        "🍔 Restaurant Menu & Cart",
        "একটি রেস্টুরেন্ট মেনু ও অর্ডার অ্যাপ বানাও: ক্যাটেগরি ট্যাব (বার্গার, পিৎজা, ড্রিঙ্কস, ডেজার্ট), আইটেম কার্ডে দাম ও '+ যোগ করো' বাটন, "
        "ভাসমান কার্ট বাটন, কার্ট ড্রয়ার (পরিমাণ পরিবর্তন, মোট দাম ৳), টেবিল নম্বর/ফোন দিয়ে অর্ডার কনফার্ম। উষ্ণ রং, বড় টাচ-ফ্রেন্ডলি বাটন, মোবাইল-ফার্স্ট।",
    ),
    (
        "💼 Freelancer Portfolio",
        "একজন ফ্রিল্যান্সারের জন্য পোর্টফোলিও অ্যাপ/ল্যান্ডিং পেজ বানাও: হিরো (নাম, পেশা, CTA বাটন), স্কিলস (প্রগ্রেস বার), সার্ভিস কার্ড, "
        "প্রজেক্ট গ্যালারি (ফিল্টারসহ), ক্লায়েন্ট রিভিউ স্লাইডার, কন্টাক্ট ফর্ম ও WhatsApp বাটন। আধুনিক ডার্ক থিম, স্মুথ স্ক্রল, মোবাইল-ফ্রেন্ডলি।",
    ),
]
