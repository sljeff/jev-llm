/* jev-llm 前端:左聊天 + 右三层决策展示。 */
const $ = (id) => document.getElementById(id);
const chat = $("chat"), msgInput = $("msg"), sendBtn = $("send"), dTitle = $("dTitle");

let history = [];   // [{role, text}] 服务端无状态,历史由浏览器保存

/* 思考中: 标题旁绿点呼吸; 结束: 熄灭 */
function setStatus(thinking) {
  dTitle.classList.toggle("thinking", thinking);
}

function scrollDown() { chat.scrollTop = chat.scrollHeight; }

function addMsg(role) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  chat.appendChild(el);
  scrollDown();
  return el;
}

const label = (p) => p === "end" ? "⟨end⟩" : p === "more" ? "⟨none⟩" : p;

function renderBars(boxId, top, picked, placeholder) {
  const box = $(boxId);
  box.innerHTML = "";
  if (!top || !top.length) {
    box.innerHTML = `<div class="placeholder">${placeholder}</div>`;
    return;
  }
  const max = top[0][1] || 1;
  for (const [name, p] of top) {
    const row = document.createElement("div");
    row.className = "bar-row" + (name === picked ? " winner" : "");
    const lab = document.createElement("span");
    lab.className = "label";
    lab.textContent = label(name);
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.style.width = Math.max(p / max * 100, 2) + "%";
    bar.appendChild(fill);
    const pct = document.createElement("span");
    pct.className = "pct";
    pct.textContent = Math.round(p * 100) + "%";
    row.append(lab, bar, pct);
    box.appendChild(row);
  }
}

function resetPanel() {
  $("pickedPos").textContent = "";
  $("pickedLetter").textContent = "";
  $("pickedWord").textContent = "";
  renderBars("posBars", null, null, "Waiting for input…");
  renderBars("letterBars", null, null, "Waiting for input…");
  renderBars("wordBars", null, null, "Waiting for input…");
}

function handleEvent(ev, bubble, cursor) {
  if (ev.type === "pos") {           // 第一层: 词性
    $("pickedPos").textContent = "→ " + label(ev.picked);
    renderBars("posBars", ev.top, ev.picked, "");
  } else if (ev.type === "letter") { // 第二层: 首字母
    $("pickedLetter").textContent = "→ " + ev.picked;
    renderBars("letterBars", ev.top, ev.picked, "");
  } else if (ev.type === "word") {   // 第三层: 选词(picked 可能是 more 翻页)
    $("pickedWord").textContent = "→ " + label(ev.picked);
    renderBars("wordBars", ev.top, ev.picked, "");
    if (ev.word) {
      bubble.insertBefore(document.createTextNode(ev.word + " "), cursor);
      scrollDown();
    }
  } else if (ev.type === "loop") {
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = "(Jev started repeating — stopped)";
    bubble.appendChild(note);
  } else if (ev.type === "error") {
    addMsg("error").textContent = ev.message;
  }
}

async function generate(userText) {
  history.push({ role: "user", text: userText });
  addMsg("user").textContent = userText;

  const bubble = addMsg("jev");
  const cursor = document.createElement("i");
  cursor.className = "cursor";
  bubble.appendChild(cursor);
  resetPanel();

  setStatus(true);
  msgInput.disabled = true;
  sendBtn.disabled = true;
  msgInput.value = "";

  let reply = "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const ev = JSON.parse(line.slice(6));
        handleEvent(ev, bubble, cursor);
        if (ev.type === "final") reply = ev.reply;
      }
    }
  } catch (err) {
    addMsg("error").textContent = "Request failed: " + err.message;
  } finally {
    cursor.remove();
    if (reply) {
      bubble.classList.add("done");
      history.push({ role: "assistant", text: reply });
    } else if (!bubble.textContent) {
      bubble.textContent = "(no output)";
      bubble.classList.add("done");
    }
    setStatus(false);
    msgInput.disabled = false;
    sendBtn.disabled = false;
    msgInput.focus();
    scrollDown();
  }
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = msgInput.value.trim();
  if (!text || msgInput.disabled) return;
  generate(text);
});
