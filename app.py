    if st.session_state.get("messages"):
        st.download_button(
            label="💾 চ্যাট সেভ করুন (.json)",
            data=json.dumps(st.session_state.messages, indent=2, ensure_ascii=False),
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
    if not isinstance(parsed, (dict, list)):
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
        if chunk.choices and chunk.choices.delta.content:
            yield chunk.choices.delta.content


def get_history():
    history = st.session_state.messages[-MAX_HISTORY:]
    while history and history[0]["role"] != "user":
        history = history[1:]
    return history


# ---------- আগের মেসেজ দেখানো ----------
for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            show_download(message["content"], idx)

# ---------- নতুন ইনপুট (চ্যাট বক্সের ভেতরেই ফাইল আপলোড বাটনসহ) ----------
if is_agent_mode:
    placeholder = "যেমন: একটি dark theme architecture Home page বানাও..."
elif is_json_mode:
    placeholder = "যেমন: একটি মডার্ন Hero Section এর Elementor JSON তৈরি করো..."
else:
    placeholder = "এখানে আপনার প্রশ্ন লিখুন বা ফাইল যুক্ত করুন..."

user_submission = st.chat_input(
    placeholder,
    accept_file=True,
    file_type=["pdf", "txt", "py", "html", "css", "js", "json", "md"],
)

if user_submission:
    prompt_text = user_submission.text or ""
    attached_files = user_submission.files

    attached_context = ""
    attached_names = []
    for f in attached_files:
        try:
            text_data = read_file(f.name, f.getvalue())
            if text_data.strip():
                attached_context += f"\n--- FILE: {f.name} ---\n{text_data[:MAX_FILE_CHARS]}\n"
                attached_names.append(f.name)
        except Exception as e:
            st.error(f"ফাইল পড়তে সমস্যা হয়েছে: {e}")

    display_msg = prompt_text
    if attached_names:
        display_msg = f"📎 **সংযুক্ত ফাইল:** {', '.join(attached_names)}\n\n{prompt_text}"

    if display_msg.strip():
        st.session_state.messages.append({"role": "user", "content": display_msg})
        with st.chat_message("user"):
            st.markdown(display_msg)

        final_system = system_instruction
        if attached_context:
            final_system += f"\n\nUse the attached file content to answer accurately:\n{attached_context}"

        success = False
        with st.chat_message("assistant"):
            try:
                history = get_history()
                if is_agent_mode:
                    if not (WP_URL and WP_AUTH[0] and WP_AUTH[1]):
                        raise ValueError("WP_URL, WP_USER, WP_APP_PASSWORD সেট করা নেই।")
                    with st.spinner("ভাবছি..."):
                        reply = run_agent(selected_model, final_system, history, temperature)
                    st.markdown(reply)
                else:
                    api_messages = [{"role": "system", "content": final_system}] + history
                    reply = st.write_stream(stream_reply(selected_model, api_messages, temperature))

                st.session_state.messages.append({"role": "assistant", "content": reply})
                success = True
            except Exception as e:
                st.session_state.messages.pop()
                st.error(f"একটি সমস্যা হয়েছে: {e}")

        if success:
            st.rerun()
