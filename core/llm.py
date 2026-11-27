"""Codexio core/llm.py — model auto-discovery + fallback engine helpers.

If a model is shut down / renamed / not accessible (404 model_not_found), the app moves to the next live model and
remembers the dead one for a while, so the same error never repeats and answers stay fast.

Call `configure(...)` once from app.py after the Groq client is created.
Imports only third-party packages and `config` (never app.py, studios/ or other core modules).

Claude (Anthropic) lives here too, but it is deliberately opt-in: only the 3 tools listed in config.CLAUDE_TOOLS
(WordPress JSON, AI Automation, Elementor MCP) may call it. See "Claude" section at the bottom.
"""
import re
import time

import requests
import streamlit as st
from groq import Groq
from http.cookiejar import DefaultCookiePolicy
from requests.adapters import HTTPAdapter

from config import CLAUDE_DEFAULT_MODEL, CLAUDE_MODELS, claude_enabled_for, get_secret

client = None          # set by configure()
api_key = None         # Groq API key (used for cached model discovery)
VISION_MODEL = ""      # default Groq vision model


@st.cache_resource(show_spinner=False)
def get_groq_client(key, timeout=25.0, max_retries=0):
    """One shared Groq client per (key, timeout) for the whole server process, not one per Streamlit rerun.
    Its httpx pool keeps TCP/TLS connections alive between chat requests."""
    return Groq(api_key=key, timeout=timeout, max_retries=max_retries)


@st.cache_resource(show_spinner=False)
def get_session():
    """Shared requests.Session (connection pooling: TCP/TLS handshakes are reused).
    Cookies are disabled so the shared session never leaks cookies between users or sites."""
    s = requests.Session()
    s.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=40, max_retries=0)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_OPT_CLIENTS = {}  # (id(client), opts) -> client.with_options(...) so it is not rebuilt on every call


def _client_with(opts):
    if not opts:
        return client
    try:
        k = (id(client), tuple(sorted(opts.items())))
        c = _OPT_CLIENTS.get(k)
        if c is None:
            c = _OPT_CLIENTS[k] = client.with_options(**opts)
        return c
    except TypeError:  # unhashable option value
        return client.with_options(**opts)


def configure(groq_client, groq_api_key, vision_model):
    global client, api_key, VISION_MODEL
    client, api_key, VISION_MODEL = groq_client, groq_api_key, vision_model


# ---------- Auto model selection + fallback ----------
# কোনো মডেল বন্ধ/নাম-বদল/অ্যাক্সেস-হীন হলে (404 model_not_found) অ্যাপ নিজে পরের চালু মডেলে যায়
# এবং বন্ধ মডেলটি কিছুক্ষণ আর চেষ্টা করে না — তাই একই ভুল বারবার দেখা যায় না, উত্তরও দ্রুত আসে।
_DEAD_LONG = 6 * 3600   # মডেল নেই / অ্যাক্সেস নেই
_DEAD_SHORT = 60        # ব্যস্ত / রেট-লিমিট / টাইমআউট


def _dead_map():
    return st.session_state.setdefault("_dead_models", {})


def mark_dead(model, secs=_DEAD_LONG):
    if model:
        _dead_map()[model] = time.time() + secs


def is_dead(model):
    exp = _dead_map().get(model)
    if exp and exp < time.time():
        _dead_map().pop(model, None)
        return False
    return bool(exp)


def _err_kind(e):
    """'dead' = মডেল নেই/বন্ধ, 'busy' = সাময়িক (লিমিট/ব্যস্ত), 'skip' = এই মডেলে সাপোর্ট নেই, 'raise' = অন্য সমস্যা"""
    s = str(e).lower()
    code = getattr(e, "status_code", None)
    name = type(e).__name__
    if code == 404 or any(k in s for k in (
            "model_not_found", "does not exist", "decommission", "no longer available", "no longer supported",
            "model_not_active", "do not have access", "don't have access", "unknown model", "invalid model")):
        return "dead"
    if code in (413, 429, 500, 502, 503, 504, 529) or "rate_limit" in s or "tokens per" in s \
            or "overloaded" in s or "timeout" in name.lower() or "connection" in name.lower():
        return "busy"
    if code == 400 and any(k in s for k in ("tool", "function", "not support", "unsupported", "vision", "image", "reasoning")):
        return "skip"
    return "raise"


_GROQ_SKIP = r"whisper|tts|playai|orpheus|guard|safeguard|embed|moderation|transcri|speech"
# পছন্দের ক্রম (তোমার আগের নামগুলো আগে রাখা হয়েছে; যেটা বন্ধ সেটা অটো বাদ পড়বে)
_GROQ_PREF = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b", "qwen/qwen3-32b",
              "openai/gpt-oss-20b", "llama-3.1-8b-instant")
_GROQ_FAST = ("openai/gpt-oss-20b", "llama-3.1-8b-instant")


@st.cache_data(ttl=1800, show_spinner=False)
def _discover_groq_models(key):
    """Groq-এ এই কী দিয়ে এখন সত্যিই চালু মডেলের তালিকা"""
    cl = get_groq_client(key, 6.0, 0)
    out = []
    for m in cl.models.list().data:
        if getattr(m, "active", True) is False:
            continue
        out.append(m.id)
    if not out:
        raise RuntimeError("empty model list")  # খালি হলে cache হয় না
    return out


def _groq_rank(name):
    if name in _GROQ_PREF:
        return (0, _GROQ_PREF.index(name), 0.0, name)
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b", name.lower())]
    return (1, 0, -(max(sizes) if sizes else 0.0), name)


def groq_text_models():
    """চ্যাট-যোগ্য চালু মডেল, সেরা আগে (বন্ধ মডেল বাদ)"""
    try:
        found = [m for m in _discover_groq_models(api_key) if not re.search(_GROQ_SKIP, m, re.I)]
    except Exception:
        found = []
    if not found:
        found = list(_GROQ_PREF)
    found = sorted(dict.fromkeys(found), key=_groq_rank)
    return [m for m in found if not is_dead(m)] or found


def groq_chain(first=None, kind="text", limit=4):
    """চেষ্টার ক্রম: প্রথমে বেছে নেওয়া মডেল, তারপর বাকি চালু মডেল। kind = text | fast | vision"""
    try:
        pool = list(_discover_groq_models(api_key))
    except Exception:
        pool = []
    if kind == "vision":
        base = [VISION_MODEL] + [m for m in pool if re.search(r"scout|maverick|vision|-vl|llama-4", m, re.I)]
    elif kind == "fast":
        base = [m for m in _GROQ_FAST if not pool or m in pool] + groq_text_models()
    else:
        base = groq_text_models()
    chain = ([first] if first else []) + base
    chain = list(dict.fromkeys(m for m in chain if m))
    live = [m for m in chain if not is_dead(m)]
    return (live or chain)[:limit]


def groq_create(first=None, kind="text", opts=None, **kw):
    """client.chat.completions.create + অটো-ফলব্যাক। (response, ব্যবহৃত_মডেল) ফেরত দেয়।
    stream=True হলে response একটি স্ট্রিম; স্ট্রিম শুরুর আগের ত্রুটিতেই মডেল বদলায় (লেখা দ্বিগুণ হয় না)।"""
    cl = _client_with(opts)
    last = None
    for m in groq_chain(first, kind):
        try:
            res = cl.chat.completions.create(model=m, **kw)
            st.session_state["_groq_used"] = m
            if first and m != first:
                try:
                    st.toast(f"ℹ️ {first} চলছে না — {m} দিয়ে উত্তর দেওয়া হচ্ছে")
                except Exception:
                    pass
            return res, m
        except Exception as e:
            last = e
            kind_e = _err_kind(e)
            if kind_e == "dead":
                mark_dead(m)
            elif kind_e == "busy":
                mark_dead(m, _DEAD_SHORT)
            elif kind_e != "skip":
                raise
    raise last if last else RuntimeError("কোনো Groq মডেল পাওয়া যায়নি")


def _strip_think(chunks):
    """Qwen/DeepSeek জাতীয় মডেলের <think>…</think> অংশ উত্তর থেকে বাদ দেয়"""
    buf, in_think = "", False
    for c in chunks:
        buf += c
        out = ""
        while buf:
            if in_think:
                i = buf.find("</think>")
                if i == -1:
                    buf = buf[-7:]
                    break
                buf, in_think = buf[i + 8:], False
            else:
                i = buf.find("<think>")
                if i == -1:
                    keep = max((k for k in range(1, 7) if buf.endswith("<think>"[:k])), default=0)
                    out += buf[:len(buf) - keep]
                    buf = buf[len(buf) - keep:]
                    break
                out += buf[:i]
                buf, in_think = buf[i + 7:], True
        if out:
            yield out
    if buf and not in_think:
        yield buf


# ---------- token estimate ----------
def est_tokens(s):
    """টোকেনের মোটামুটি হিসাব (বাংলা অক্ষর ~১ টোকেন, ইংরেজি ~৩ অক্ষরে ১)"""
    s = s or ""
    non_ascii = sum(1 for ch in s if ord(ch) > 127)
    return non_ascii + (len(s) - non_ascii) // 3 + 1


# =====================================================================================================
# Claude (Anthropic) — ONLY for: WordPress JSON, AI Automation, Elementor MCP  (config.CLAUDE_TOOLS)
# =====================================================================================================
class ClaudeError(RuntimeError):
    """Claude failure with a user-friendly Bengali message. `kind` is one of:
    no_key | auth | credits | model | rate | busy | bad_request | network | unknown"""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def claude_key():
    return get_secret("ANTHROPIC_API_KEY")


def claude_available():
    return bool(claude_key())


@st.cache_resource(show_spinner=False)
def get_claude_client(key):
    """One shared Anthropic client per API key for the whole server process (connection pooling).
    max_retries=2 lets the SDK retry 429/5xx itself with back-off."""
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=120.0, max_retries=2)


def claude_client():
    key = claude_key()
    if not key:
        raise ClaudeError("no_key", "❌ ANTHROPIC_API_KEY সেট করা নেই। Secrets বা .env ফাইলে key দাও (console.anthropic.com)। "
                                    "এই টুলটি Claude ছাড়া চলবে না, তাই এখন Groq/Gemini দিয়ে চেষ্টা হবে।")
    return get_claude_client(key)


def claude_model_id(label_or_id=None):
    """Sidebar label or raw ID -> a model ID that is in config.CLAUDE_MODELS (falls back to the default)."""
    v = label_or_id or ""
    if v in CLAUDE_MODELS:
        return CLAUDE_MODELS[v]
    return v if v in CLAUDE_MODELS.values() else CLAUDE_DEFAULT_MODEL


def claude_error(e):
    """Any exception from the anthropic SDK -> ClaudeError with a clear message (already-wrapped errors pass through)."""
    if isinstance(e, ClaudeError):
        return e
    s = str(e)
    low = s.lower()
    code = getattr(e, "status_code", None)
    name = type(e).__name__
    if "credit balance" in low or "billing" in low or "purchase credits" in low or code == 402:
        return ClaudeError("credits", "💳 Claude API-তে ক্রেডিট শেষ (Credit balance too low)। console.anthropic.com → "
                                      "Plans & Billing থেকে ক্রেডিট যোগ করো। এর মধ্যে Groq/Gemini দিয়ে চেষ্টা হবে।")
    if code == 401 or "authentication" in name.lower() or "invalid x-api-key" in low:
        return ClaudeError("auth", "🔑 ANTHROPIC_API_KEY ভুল বা মেয়াদ শেষ। console.anthropic.com থেকে নতুন key নিয়ে Secrets-এ বসাও।")
    if code == 403 or "permission" in name.lower():
        return ClaudeError("auth", "🚫 এই API key দিয়ে এই Claude মডেলে অ্যাক্সেস নেই। সাইডবার থেকে অন্য Claude মডেল বেছে নাও।")
    if code == 404 or "notfound" in name.lower() or "model_not_found" in low:
        return ClaudeError("model", "🤖 বেছে নেওয়া Claude মডেলটি পাওয়া যায়নি বা আপনার key-তে চালু নেই। সাইডবার থেকে অন্য মডেল বেছে নাও।")
    if code == 429 or "ratelimit" in name.lower():
        return ClaudeError("rate", "⏳ Claude-এর রেট লিমিট পার হয়েছে। কিছুক্ষণ পর আবার চেষ্টা করো।")
    if code in (500, 502, 503, 504, 529) or "overloaded" in low or "internalserver" in name.lower():
        return ClaudeError("busy", "⏳ Claude সার্ভার এখন ব্যস্ত। একটু পরে আবার চেষ্টা করো।")
    if "connection" in name.lower() or "timeout" in name.lower():
        return ClaudeError("network", "🌐 Claude-এ সংযোগ করা যায়নি (নেটওয়ার্ক/টাইমআউট)।")
    if code == 400:
        return ClaudeError("bad_request", f"Claude অনুরোধ নেয়নি (400): {s[:240]}")
    return ClaudeError("unknown", f"Claude ত্রুটি: {s[:240]}")


def _claude_system(system_text):
    # prompt caching: long system prompt / file text resent on every turn becomes cheaper and faster
    return [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}] if system_text else []


def claude_stream(system_text, messages, model=None, max_tokens=6000, temperature=None):
    """Streams text from Claude (generator of str). `messages` is already in Anthropic format.
    Raises ClaudeError (never a raw SDK exception). Errors before the first token are safe to fall back from."""
    client = claude_client()
    model = claude_model_id(model)
    kw = dict(model=model, max_tokens=max_tokens, system=_claude_system(system_text), messages=messages)
    if temperature is not None:
        kw["temperature"] = min(max(float(temperature), 0.0), 1.0)
    st.session_state["_claude_used"] = model  # set before streaming so the UI label is right
    got = False
    try:
        try:
            ctx = client.messages.stream(**kw)
            stream = ctx.__enter__()
        except Exception as e:
            if "temperature" in kw and "temperature" in str(e).lower():  # some models reject temperature
                kw.pop("temperature")
                ctx = client.messages.stream(**kw)
                stream = ctx.__enter__()
            else:
                raise
        try:
            for text in stream.text_stream:
                if text:
                    got = True
                    yield text
        finally:
            ctx.__exit__(None, None, None)
    except ClaudeError:
        raise
    except Exception as e:
        raise claude_error(e) from e
    if not got:
        raise ClaudeError("unknown", "Claude কোনো উত্তর দেয়নি")


def claude_create(system_text, messages, model=None, max_tokens=8000, temperature=None, tools=None):
    """Non-streaming call (used by the Elementor MCP tool-use loop). Returns the response as a plain dict
    ({"content": [...], "stop_reason": ...}). Raises ClaudeError."""
    client = claude_client()
    model = claude_model_id(model)
    kw = dict(model=model, max_tokens=max_tokens, system=_claude_system(system_text), messages=messages)
    if tools:
        kw["tools"] = tools
    if temperature is not None:
        kw["temperature"] = min(max(float(temperature), 0.0), 1.0)
    try:
        try:
            resp = client.messages.create(**kw)
        except Exception as e:
            if "temperature" in kw and "temperature" in str(e).lower():
                kw.pop("temperature")
                resp = client.messages.create(**kw)
            else:
                raise
    except Exception as e:
        raise claude_error(e) from e
    st.session_state["_claude_used"] = model
    return resp.model_dump(exclude_none=True)  # blocks go back to the API as-is: no None fields


def claude_allowed(tool):
    """Routing gate: True only for wp_json / ai_automation / elementor_mcp AND when an API key exists."""
    return claude_enabled_for(tool) and claude_available()
