"""Codexio studios/app_builder.py — App Builder extras.

  * render_starter_chips   - 4 one-click starter prompts when the App Builder chat is empty
  * render_publish_button  - "🚀 Publish Live Link": single-file HTML -> Netlify (NETLIFY_AUTH_TOKEN)
                             or public GitHub Gist + htmlpreview.github.io (GITHUB_TOKEN)
The sandbox preview / live code editor stay in app.py (they share app-wide UI state).
"""
import io
import time
import zipfile

import requests
import streamlit as st

from config import STARTER_CHIPS

NETLIFY_API = "https://api.netlify.com/api/v1"
GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------- starter chips
def render_starter_chips(on_pick):
    """Shows the 4 starter buttons (2 x 2). `on_pick(prompt)` is called as a button callback, so the prompt is stored
    before the next rerun and app.py can submit it like a typed message."""
    st.markdown("##### ⚡ এক ক্লিকে শুরু করো")
    for row in range(0, len(STARTER_CHIPS), 2):
        cols = st.columns(2)
        for col, (label, prompt) in zip(cols, STARTER_CHIPS[row:row + 2]):
            col.button(label, key=f"starter_{row}_{label[:6]}", use_container_width=True,
                       on_click=on_pick, args=(prompt,))
    st.caption("অথবা নিচের বক্সে নিজের অ্যাপের আইডিয়া লেখো।")


# ---------------------------------------------------------------- deploy back-ends
def _err(r):
    try:
        j = r.json()
        return f"{r.status_code}: {j.get('message') or j.get('error') or r.text[:160]}"
    except Exception:
        return f"{r.status_code}: {r.text[:160]}"


def _zip_index(html):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", html)
    return buf.getvalue()


def deploy_netlify(html, token, site=None):
    """Creates (or re-uses `site` = {"id","url"}) a Netlify site and deploys index.html as a zip.
    Returns {"provider","url","site"}."""
    h = {"Authorization": f"Bearer {token}"}
    if not site:
        r = requests.post(f"{NETLIFY_API}/sites", headers=h, json={}, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError("Netlify সাইট তৈরি ব্যর্থ — " + _err(r))
        j = r.json()
        site = {"id": j["id"], "url": j.get("ssl_url") or j.get("url") or f"https://{j.get('name')}.netlify.app"}
    r = requests.post(f"{NETLIFY_API}/sites/{site['id']}/deploys", timeout=90,
                      headers={**h, "Content-Type": "application/zip"}, data=_zip_index(html))
    if r.status_code not in (200, 201):
        raise RuntimeError("Netlify ডেপ্লয় ব্যর্থ — " + _err(r))
    dep = r.json()
    for _ in range(30):  # up to ~30s until the deploy is live
        state = dep.get("state")
        if state == "ready":
            break
        if state == "error":
            raise RuntimeError("Netlify ডেপ্লয়ে সমস্যা: " + str(dep.get("error_message") or "unknown"))
        time.sleep(1)
        rr = requests.get(f"{NETLIFY_API}/deploys/{dep['id']}", headers=h, timeout=20)
        if rr.status_code == 200:
            dep = rr.json()
    return {"provider": "Netlify", "url": site["url"], "site": site}


def deploy_gist(html, token):
    """Public GitHub Gist with index.html -> clickable live preview via htmlpreview.github.io."""
    r = requests.post(
        f"{GITHUB_API}/gists", timeout=30,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"description": "App built with Codexio", "public": True, "files": {"index.html": {"content": html}}},
    )
    if r.status_code not in (200, 201):
        raise RuntimeError("GitHub Gist তৈরি ব্যর্থ — " + _err(r))
    j = r.json()
    raw = (j.get("files", {}).get("index.html") or {}).get("raw_url")
    if not raw:
        raise RuntimeError("Gist-এর raw লিংক পাওয়া যায়নি")
    return {"provider": "GitHub Gist", "url": "https://htmlpreview.github.io/?" + raw, "gist": j.get("html_url", "")}


def publish_html(html, netlify_token="", github_token="", site=None):
    """Netlify first; if it fails and a GitHub token exists, falls back to Gist."""
    errors = []
    if netlify_token:
        try:
            return deploy_netlify(html, netlify_token, site)
        except Exception as e:
            errors.append(str(e))
    if github_token:
        try:
            return deploy_gist(html, github_token)
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors) or "কোনো ডেপ্লয় টোকেন নেই")


# ---------------------------------------------------------------- UI
def render_publish_button(html, key, netlify_token="", github_token=""):
    """Place directly below the app preview. `key` must be unique per generated app."""
    store = st.session_state.setdefault("_deploys", {})
    has_token = bool(netlify_token or github_token)
    help_txt = ("সিঙ্গেল-ফাইল অ্যাপটি এক ক্লিকে লাইভ লিংকে পাবলিশ করো।" if has_token else
                "Secrets-এ NETLIFY_AUTH_TOKEN (Netlify → User settings → Personal access tokens) অথবা "
                "GITHUB_TOKEN (GitHub → Settings → Developer settings → token with 'gist' scope) যোগ করলে এই বাটন চালু হবে।")
    if st.button("🚀 Publish Live Link", key=f"pub_{key}", disabled=not has_token, help=help_txt):
        prev = (store.get(key) or {}).get("site")
        with st.spinner("পাবলিশ হচ্ছে..."):
            try:
                store[key] = publish_html(html, netlify_token, github_token, prev)
            except Exception as e:
                st.error(f"পাবলিশ করা যায়নি: {str(e)[:300]}")
    rec = store.get(key)
    if rec:
        st.success(f"✅ লাইভ হয়েছে ({rec['provider']})")
        st.link_button("🌐 লাইভ লিংক খোলো", rec["url"])
        st.code(rec["url"], language=None)
        if rec["provider"] == "GitHub Gist":
            st.caption("⚠️ Gist পাবলিক — লিংক জানা যে কেউ কোড দেখতে পারবে।")
    if not has_token:
        st.caption("ℹ️ লাইভ লিংকের জন্য Secrets-এ `NETLIFY_AUTH_TOKEN` বা `GITHUB_TOKEN` দাও।")
