"""Codexio studios/wordpress.py — WordPress extras.

  * Encrypted persistent site manager glue (uses core.db + Fernet): load / persist / forget
  * Elementor MCP "Live Page Preview & Actions" card with Approve & Publish
The MCP agent loop, WP automation (blog / WooCommerce / webhook) still live in app.py because they share app state.
"""
import re
import urllib.parse

import requests
import streamlit as st
import streamlit.components.v1 as components

from core.db import delete_saved_site, load_saved_sites, save_site


# ---------------------------------------------------------------- persistent encrypted sites
def hydrate_saved_sites(db, fernet, sites):
    """Merges DB-saved sites into the in-session list (by name). Returns number of undecryptable rows."""
    if not fernet:
        return 0
    try:
        saved, bad = load_saved_sites(db, fernet)
    except Exception as e:
        st.session_state["db_error"] = str(e)[:200]
        return 0
    for s in saved:
        if all(x["name"] != s["name"] for x in sites):
            sites.append(s)
    return bad


def persist_site(db, fernet, entry):
    """Saves one site (credentials encrypted). Returns True on success."""
    if not fernet:
        return False
    try:
        save_site(db, fernet, entry)
        return True
    except Exception as e:
        st.session_state["db_error"] = str(e)[:200]
        return False


def forget_site(db, name):
    try:
        delete_saved_site(db, name)
    except Exception as e:
        st.session_state["db_error"] = str(e)[:200]


# ---------------------------------------------------------------- live page preview + publish
_URL_RE = re.compile(r"https?://[^\s)>\]\"'<]+")
_ID_PATTERNS = (
    re.compile(r"[?&]post=(\d+)"),
    re.compile(r"[?&](?:page_id|p)=(\d+)"),
)
_TEXT_ID_RE = re.compile(r"(?:post|page)[\s_]*id\D{0,6}(\d+)", re.I)


def _host(u):
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _resolve_id(base_url, auth, url):
    """Pretty permalinks have no id in the URL: look the page up by slug (best effort)."""
    slug = urllib.parse.urlparse(url).path.strip("/").split("/")[-1]
    if not slug:
        return None
    try:
        for kind in ("pages", "posts"):
            r = requests.get(f"{base_url}/wp-json/wp/v2/{kind}", auth=auth, timeout=10,
                             params={"slug": slug, "status": "any", "_fields": "id"})
            if r.status_code == 200 and r.json():
                return int(r.json()[0]["id"])
    except Exception:
        pass
    return None


def extract_pages(text, base_url, auth=None):
    """Finds pages created/updated by the agent in its reply. Only links on the connected site are considered.
    Returns [{"id": int|None, "url": str}]."""
    base_url = (base_url or "").rstrip("/")
    bh = _host(base_url)
    if not bh:
        return []
    pages, seen = [], set()
    for raw in _URL_RE.findall(text or ""):
        u = raw.rstrip(".,;:*`")
        if _host(u) != bh or "/wp-json/" in u:
            continue
        pid = None
        for pat in _ID_PATTERNS:
            m = pat.search(u)
            if m:
                pid = int(m.group(1))
                break
        if pid is None and "/wp-admin/" not in u:
            pid = _resolve_id(base_url, auth, u) if auth else None
        key = pid or u
        if key in seen:
            continue
        seen.add(key)
        view = u if "/wp-admin/" not in u else (f"{base_url}/?p={pid}" if pid else u)
        pages.append({"id": pid, "url": view})
    if not pages:  # reply mentions only an id
        m = _TEXT_ID_RE.search(text or "")
        if m:
            pid = int(m.group(1))
            pages.append({"id": pid, "url": f"{base_url}/?p={pid}"})
    return pages[:3]


def publish_page(base_url, auth, page_id):
    """Sets a page (or post) to 'publish' via the WP REST API. Returns the public link."""
    base_url = base_url.rstrip("/")
    last = None
    for kind in ("pages", "posts"):
        r = requests.post(f"{base_url}/wp-json/wp/v2/{kind}/{int(page_id)}", auth=auth, timeout=30,
                          json={"status": "publish"})
        if r.status_code == 200:
            return (r.json() or {}).get("link") or f"{base_url}/?p={page_id}"
        last = r
        if r.status_code != 404:
            break
    try:
        msg = last.json().get("message")
    except Exception:
        msg = last.text[:150]
    raise RuntimeError(f"{last.status_code}: {msg}")


def render_page_actions(text, base_url, auth, key):
    """Expander rendered right below an assistant message in Agent mode."""
    pages = extract_pages(text, base_url, auth)
    if not pages:
        return
    base_url = base_url.rstrip("/")
    with st.expander("👁️ Live Page Preview & Actions", expanded=False):
        for i, p in enumerate(pages):
            pid = p["id"]
            st.markdown(f"**{'পেজ #' + str(pid) if pid else 'পেজ'}** · [🔗 খোলো]({p['url']})"
                        + (f" · [✏️ Elementor এডিটর]({base_url}/wp-admin/post.php?post={pid}&action=elementor)" if pid else ""))
            components.iframe(p["url"], height=460, scrolling=True)
            st.caption("প্রিভিউ ফাঁকা দেখালে: ড্রাফট পেজ লগইন ছাড়া দেখা যায় না, অথবা সাইট iframe ব্লক করে — তখন উপরের লিংক/এডিটর ব্যবহার করো।")
            if pid:
                if st.button("✅ Approve & Publish", key=f"wp_pub_{key}_{i}"):
                    try:
                        with st.spinner("পাবলিশ হচ্ছে..."):
                            link = publish_page(base_url, auth, pid)
                        st.success(f"✅ পাবলিশ হয়েছে: {link}")
                    except Exception as e:
                        st.error(f"পাবলিশ করা যায়নি: {str(e)[:250]}")
