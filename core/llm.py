"""Codexio core/llm.py — model auto-discovery + fallback engine helpers.

If a model is shut down / renamed / not accessible (404 model_not_found), the app moves to the next live model and
remembers the dead one for a while, so the same error never repeats and answers stay fast.

Call `configure(...)` once from app.py after the Groq client is created.
Imports only third-party packages (never app.py, studios/ or other core modules).
"""
import re
import time

import streamlit as st
from groq import Groq

client = None          # set by configure()
api_key = None         # Groq API key (used for cached model discovery)
VISION_MODEL = ""      # default Groq vision model


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
    cl = Groq(api_key=key, timeout=6.0, max_retries=0)
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
    cl = client.with_options(**opts) if opts else client
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
