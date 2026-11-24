// Home app front end. Plain JS, no build step. Every section loads on its
// own; if one fails it shows an error card and the others keep working.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const K = (root, k) => root.querySelector(`[data-k="${k}"]`);

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let resp;
  try {
    resp = await fetch(path, init);
  } catch (e) {
    throw new Error("Can't reach the server. Are you at home or on Tailscale?");
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* not JSON */ }
  if (resp.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Please sign in."); }
  if (!resp.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : `Error (HTTP ${resp.status})`);
  }
  return data;
}

function setStatus(el, msg, kind) { el.textContent = msg || ""; el.className = "status" + (kind ? " " + kind : ""); }
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  for (const kid of kids) if (kid) e.append(kid);
  return e;
}

// --- Hub-format markdown preview (safe: builds DOM nodes, no innerHTML) ----
function renderRecipe(md, root) {
  root.replaceChildren();
  let list = null;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    if (line.startsWith("# ")) continue; // shown as the card title
    if (line.startsWith("## ")) { root.append(el("h3", { text: line.slice(3) })); list = null; continue; }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    const num = line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || num) {
      const tag = num ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); root.append(list); }
      list.append(el("li", { text: (bullet || num)[1] }));
      continue;
    }
    list = null;
    root.append(el("p", { class: "muted", text: line }));
  }
}

function titleOf(md) {
  for (const line of md.split("\n")) {
    const s = line.trim();
    if (!s) continue;
    return s.startsWith("# ") ? s.slice(2).trim() : s.replace(/^[*#\s]+|[*\s]+$/g, "");
  }
  return "";
}

// --- Back button / back swipe ----------------------------------
// Without history entries, Android's back gesture closes the whole app. Each
// thing "back" should undo (an open menu or dialog, an opened recipe or issue,
// being in a section other than Today) is a layer with an undo function. The
// browser history just holds one entry per layer ({rosie: depth}); after any
// change, sync() pushes or goes back so the two match. Changes made in the same
// click cancel out, so closing the menu and opening a section is one step.
// On Today with nothing open there are no layers, so back leaves the app.
const nav = (() => {
  const stack = [];
  let depth = Number(history.state?.rosie) || 0, pending = false, queued = false;
  function sync() {
    if (pending) return; // a history.go() is in flight; popstate calls us again
    if (stack.length > depth) {
      while (depth < stack.length) history.pushState({ rosie: ++depth }, "");
    } else if (stack.length < depth) {
      pending = true;
      history.go(stack.length - depth);
    }
  }
  function schedule() {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; sync(); });
  }
  window.addEventListener("popstate", (e) => {
    pending = false;
    depth = Number(e.state?.rosie) || 0;
    while (stack.length > depth) stack.pop().undo(); // the user went back
    schedule();
  });
  schedule(); // a reload leaves stale entries behind us; drop them
  const api = {
    push(undo) { const l = { undo }; stack.push(l); schedule(); return l; },
    // Removes a layer the page closed itself (and anything above it), without undoing.
    remove(l) { const i = stack.indexOf(l); if (i >= 0) { stack.splice(i); schedule(); } },
    clear() { if (stack.length) { stack.length = 0; schedule(); } },
    // One layer for a view that can be opened again while open (it won't stack).
    slot(undo) {
      let l = null;
      return {
        open() { if (!l || !stack.includes(l)) l = api.push(() => { l = null; undo(); }); },
        close() { if (l) { api.remove(l); l = null; } },
      };
    },
    // A modal <dialog>: back closes it; closing it any other way drops the layer.
    dialog(dlg) {
      const l = api.push(() => { if (dlg.open) dlg.close(); });
      dlg.addEventListener("close", () => api.remove(l), { once: true });
    },
  };
  return api;
})();

// --- Add a recipe's ingredients to a shopping list --------------------------
async function openIngredientPicker(md) {
  const dlg = $("#picker");
  const status = K(dlg, "picker-status"), ul = K(dlg, "picker-items"), chips = K(dlg, "picker-lists");
  setStatus(status, "");
  const [{ items }, { lists }] = await Promise.all([
    api("/api/lists/parse-ingredients", { method: "POST", body: { markdown: md } }),
    api("/api/lists"),
  ]);
  if (!items.length) { alert("This recipe has no ingredient list to add."); return; }
  const stores = lists.filter((l) => l.kind === "store");
  let target = stores[0]?.id;
  try { target = Number(localStorage.getItem("picker-list")) || target; } catch (e) { /* ignore */ }
  if (!stores.some((l) => l.id === target)) target = stores[0]?.id;
  chips.replaceChildren();
  for (const l of stores) {
    const b = el("button", { type: "button", class: l.id === target ? "on" : "", text: l.name });
    b.addEventListener("click", () => {
      target = l.id;
      for (const c of chips.children) c.classList.toggle("on", c === b);
    });
    chips.append(b);
  }
  ul.replaceChildren();
  for (const it of items) {
    const cb = el("input", { type: "checkbox" });
    cb.checked = !it.staple; cb.dataset.text = it.text;
    ul.append(el("li", {}, el("label", {}, cb, it.text)));
  }
  const add = K(dlg, "picker-add");
  add.onclick = async () => {
    const texts = [...ul.querySelectorAll("input:checked")].map((c) => c.dataset.text);
    if (!texts.length) { setStatus(status, "Tick at least one item.", "err"); return; }
    add.disabled = true;
    try {
      const r = await api(`/api/lists/${target}/items`, { method: "POST", body: { texts } });
      try { localStorage.setItem("picker-list", String(target)); } catch (e) { /* ignore */ }
      const name = stores.find((l) => l.id === target).name;
      const dup = r.skipped_duplicates.length ? ` (${r.skipped_duplicates.length} already on it)` : "";
      setStatus(status, `Added ${r.added.length} to ${name}${dup}.`, "ok");
      setTimeout(() => dlg.close(), 900);
    } catch (e) { setStatus(status, e.message, "err"); }
    finally { add.disabled = false; }
  };
  dlg.showModal();
  nav.dialog(dlg);
}

// --- Sections ---------------------------------------------------------------
const sections = {};

sections.kitchen = {
  mount(root, ctx) {
    root.append($("#tpl-kitchen").content.cloneNode(true));
    const inputCard = $('[data-step="input"]', root), previewCard = $('[data-step="preview"]', root);
    const status = K(root, "status"), status2 = K(root, "status2");
    let current = { md: "", source: "" };

    const showSub = (sub) => {
      root.querySelectorAll(".subtabs button").forEach((x) => x.classList.toggle("on", x.dataset.sub === sub));
      root.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== sub; });
      try { localStorage.setItem("kitchen-sub", sub); } catch (e) { /* ignore */ }
      savedSlot.close();
      if (!chatsPanel.hidden) showChats(false);
      if (sub === "saved") { savedView.hidden = true; savedBrowse.hidden = false; loadSaved(); }
      if (sub === "lists") lists.show();
    };
    root.querySelectorAll(".subtabs button").forEach((b) => b.addEventListener("click", () => showSub(b.dataset.sub)));

    const showPreview = (md, source) => {
      current = { md, source: source || "" };
      K(root, "title").textContent = titleOf(md) || "Recipe";
      K(root, "source").textContent = source ? `From ${source}` : "";
      K(root, "md").value = md;
      renderRecipe(md, K(root, "rendered"));
      inputCard.hidden = true; previewCard.hidden = false;
      setStatus(status2, "");
    };
    K(root, "md").addEventListener("input", (e) => {
      current.md = e.target.value;
      K(root, "title").textContent = titleOf(current.md) || "Recipe";
      renderRecipe(current.md, K(root, "rendered"));
    });

    const refreshBudget = () => api("/api/kitchen/status").then((s) => {
      const parts = [];
      if (!s.import_configured) parts.push("AI import is not set up yet (no API key).");
      else parts.push(`AI this month: $${s.month_spent_usd.toFixed(2)} of $${s.monthly_cap_usd.toFixed(2)}.`);
      if (!s.send_configured) parts.push("Sending to the Hub is not set up yet.");
      K(root, "budget").textContent = parts.join(" ");
    }).catch(() => {});
    refreshBudget();

    K(root, "paste").addEventListener("click", async () => {
      try { K(root, "text").value = await navigator.clipboard.readText(); setStatus(status, ""); }
      catch (e) { setStatus(status, "Couldn't read the clipboard. Long-press the box and choose Paste.", "err"); }
    });
    K(root, "clear").addEventListener("click", () => { K(root, "text").value = ""; K(root, "url").value = ""; picked = []; drawThumbs(); setStatus(status, ""); });

    // --- Screenshots / photos / PDF (#22) ------------------------------------
    // Images are shrunk here (readable width, long screenshots cut into
    // overlapping pieces) so uploads stay small and inside the API's limits.
    let picked = []; // [{name, media_type, data (base64), thumb}]
    const MAX_W = 1568, MAX_H = 2576, OVERLAP = 80, MAX_FILES = 10;
    const toB64 = (blob) => new Promise((ok, bad) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(",", 2)[1]); r.onerror = () => bad(r.error);
      r.readAsDataURL(blob);
    });
    const loadImage = (file) => new Promise((ok, bad) => {
      const img = new Image(), u = URL.createObjectURL(file);
      img.onload = () => { URL.revokeObjectURL(u); ok(img); };
      img.onerror = () => { URL.revokeObjectURL(u); bad(new Error(`Couldn't read ${file.name || "that image"}.`)); };
      img.src = u;
    });
    async function imageParts(file) {
      const img = await loadImage(file);
      const scale = Math.min(1, MAX_W / img.naturalWidth);
      const w = Math.round(img.naturalWidth * scale), h = Math.round(img.naturalHeight * scale);
      const parts = [];
      for (let top = 0; top < h; top += MAX_H - OVERLAP) {
        const ph = Math.min(MAX_H, h - top);
        const c = document.createElement("canvas");
        c.width = w; c.height = ph;
        const g = c.getContext("2d");
        g.fillStyle = "#fff"; g.fillRect(0, 0, w, ph);
        g.drawImage(img, 0, -top, w, h);
        const url = c.toDataURL("image/jpeg", 0.85);
        parts.push({ name: file.name || "pasted image", media_type: "image/jpeg", data: url.split(",", 2)[1], thumb: url });
        if (top + ph >= h) break;
      }
      return parts;
    }
    function drawThumbs() {
      const ul = K(root, "thumbs");
      ul.replaceChildren(...picked.map((f, i) => {
        const rm = el("button", { type: "button", class: "thumb-rm", "aria-label": `Remove ${f.name}`, text: "×" });
        rm.addEventListener("click", () => { picked.splice(i, 1); drawThumbs(); });
        const face = f.thumb ? el("img", { src: f.thumb, alt: f.name }) : el("span", { class: "thumb-pdf", text: "PDF" });
        return el("li", {}, face, rm);
      }));
      K(root, "import").textContent = picked.length ? `Import from ${picked.length} file${picked.length > 1 ? "s" : ""}` : "Import with AI";
    }
    async function addFiles(list) {
      setStatus(status, "Preparing…");
      try {
        for (const f of list) {
          if (f.type === "application/pdf") {
            if (f.size > 20 * 1024 * 1024) throw new Error("That PDF is over 20 MB.");
            if (picked.some((x) => x.media_type === "application/pdf")) throw new Error("One PDF at a time, please.");
            picked.push({ name: f.name, media_type: "application/pdf", data: await toB64(f), thumb: "" });
          } else if (f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name || "")) {
            picked.push(...await imageParts(f));
          } else throw new Error(`${f.name}: only images or a PDF.`);
        }
        if (picked.length > MAX_FILES) {
          picked = picked.slice(0, MAX_FILES);
          throw new Error(`Up to ${MAX_FILES} pieces at once (long screenshots count as several).`);
        }
        setStatus(status, "");
      } catch (e) { setStatus(status, e.message, "err"); }
      drawThumbs();
    }
    K(root, "files").addEventListener("change", (e) => { addFiles([...e.target.files]); e.target.value = ""; });
    inputCard.addEventListener("paste", (e) => {
      const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
      if (imgs.length) { e.preventDefault(); addFiles(imgs); }
    });

    K(root, "import").addEventListener("click", async (ev) => {
      const url = K(root, "url").value.trim(), text = K(root, "text").value.trim();
      if (picked.length) {
        ev.target.disabled = true;
        setStatus(status, "Reading the images… (about 15–40 seconds)");
        try {
          const files = picked.map(({ media_type, data }) => ({ media_type, data }));
          const r = await api("/api/kitchen/import-files", { method: "POST", body: { files, note: text, source_url: url } });
          showPreview(r.markdown, r.source_url);
          picked = []; drawThumbs(); setStatus(status, "");
          refreshBudget();
        } catch (e) { setStatus(status, e.message, "err"); }
        finally { ev.target.disabled = false; }
        return;
      }
      if (!url && !text) { setStatus(status, "Paste a link or the recipe text, or choose a screenshot.", "err"); return; }
      ev.target.disabled = true;
      setStatus(status, url && !text ? "Reading the page… (about 10–30 seconds)" : "Reading the recipe…");
      try {
        const r = await api("/api/kitchen/import", { method: "POST", body: { url, text } });
        showPreview(r.markdown, r.source_url);
        if (r.truncated) setStatus(status2, "The page was very long; only the first part was read. Check the recipe is complete.", "err");
        refreshBudget();
      } catch (e) {
        setStatus(status, e.message, "err");
        if (url) K(root, "text-box").open = true;
      } finally { ev.target.disabled = false; }
    });

    K(root, "as-is").addEventListener("click", () => {
      const text = K(root, "text").value.trim();
      if (!text) { K(root, "text-box").open = true; setStatus(status, "Paste the recipe text first. It is sent unchanged.", "err"); return; }
      showPreview(text, K(root, "url").value.trim());
    });

    K(root, "send").addEventListener("click", async (ev) => {
      ev.target.disabled = true; setStatus(status2, "Sending…");
      try {
        await api("/api/kitchen/send", { method: "POST", body: { markdown: current.md } });
        setStatus(status2, "Sent. It should show on the Hub in a few seconds.", "ok");
      } catch (e) { setStatus(status2, e.message, "err"); }
      finally { ev.target.disabled = false; }
    });
    K(root, "save").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      try {
        await api("/api/kitchen/recipes", { method: "POST", body: { markdown: current.md, source_url: current.source } });
        setStatus(status2, "Saved to the collection.", "ok");
      } catch (e) { setStatus(status2, e.message, "err"); ev.target.disabled = false; }
    });
    K(root, "to-list").addEventListener("click", () => openIngredientPicker(current.md).catch((e) => setStatus(status2, e.message, "err")));
    K(root, "again").addEventListener("click", () => {
      previewCard.hidden = true; inputCard.hidden = false;
      K(root, "save").disabled = false;
      K(root, "url").value = ""; K(root, "text").value = ""; setStatus(status, "");
    });

    // --- Chat over the collection ------------------------------------------
    const log = K(root, "chat-log"), chatStatus = K(root, "chat-status");
    let history = []; // [{role, content}] as the server expects
    let chatId = null; // saved chat this conversation belongs to (#23)
    const hint = log.querySelector(".chat-hint");
    const bubble = (role, text) => {
      log.querySelector(".chat-hint")?.remove();
      const b = el("div", { class: `bubble ${role}`, text });
      log.append(b); b.scrollIntoView?.({ block: "end", behavior: "smooth" });
    };
    const recipeCard = (title, md, canSave) => {
      const box = el("div", { class: "recipe" });
      renderRecipe(md, box);
      const st = el("p", { class: "status" });
      const send = el("button", { type: "button", class: "primary", text: "Send to kitchen" });
      send.addEventListener("click", async () => {
        send.disabled = true; setStatus(st, "Sending…");
        try { await api("/api/kitchen/send", { method: "POST", body: { markdown: md } }); setStatus(st, "Sent to the Hub.", "ok"); }
        catch (e) { setStatus(st, e.message, "err"); } finally { send.disabled = false; }
      });
      const toList = el("button", { type: "button", text: "Add to list" });
      toList.addEventListener("click", () => openIngredientPicker(md).catch((e) => setStatus(st, e.message, "err")));
      const row = el("div", { class: "row" }, send, toList);
      if (canSave) {
        const save = el("button", { type: "button", text: "Save" });
        save.addEventListener("click", async () => {
          save.disabled = true;
          try { await api("/api/kitchen/recipes", { method: "POST", body: { markdown: md } }); setStatus(st, "Saved to the collection.", "ok"); }
          catch (e) { setStatus(st, e.message, "err"); save.disabled = false; }
        });
        row.append(save);
      }
      log.append(el("section", { class: "card recipe-card" }, el("h2", { text: title }), box, row, st));
    };
    const ask = async () => {
      const text = K(root, "chat-text").value.trim();
      if (!text) return;
      const btn = K(root, "chat-send");
      btn.disabled = true; setStatus(chatStatus, "Thinking… (about 10–30 seconds)");
      bubble("user", text);
      const msgs = [...history, { role: "user", content: text }];
      K(root, "chat-text").value = "";
      try {
        const r = await api("/api/kitchen/chat", { method: "POST", body: { messages: msgs, chat_id: chatId } });
        history = [...msgs, { role: "assistant", content: r.history_text }];
        if (chatId === null) K(root, "chat-title").textContent = "";
        chatId = r.chat_id;
        if (r.reply) bubble("assistant", r.reply);
        for (const s of r.saved) recipeCard(s.title, s.markdown, false);
        for (const x of r.recipes) recipeCard(x.title, x.markdown, true);
        setStatus(chatStatus, `AI this month: $${r.month_spent_usd.toFixed(2)}`);
        refreshBudget();
      } catch (e) {
        log.lastElementChild?.remove(); // drop the unanswered question bubble
        K(root, "chat-text").value = text;
        setStatus(chatStatus, e.message, "err");
      } finally { btn.disabled = false; }
    };
    K(root, "chat-send").addEventListener("click", ask);
    K(root, "chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
    const newChat = () => {
      history = []; chatId = null; log.replaceChildren(hint); setStatus(chatStatus, "");
      K(root, "chat-title").textContent = "";
    };
    K(root, "chat-new").addEventListener("click", () => { newChat(); showChats(false); });

    // --- Earlier chats (#23) -------------------------------------------------
    const chatsPanel = K(root, "chats-panel"), chatsToggle = K(root, "chats-toggle");
    const chatsSlot = nav.slot(() => showChats(false));
    const showChats = (open) => {
      chatsPanel.hidden = !open;
      if (open) chatsSlot.open(); else chatsSlot.close();
      chatsToggle.setAttribute("aria-expanded", String(open));
      chatsToggle.textContent = open ? "Hide earlier chats" : "Earlier chats";
      if (open) loadChats();
    };
    chatsToggle.addEventListener("click", () => showChats(chatsPanel.hidden));
    const ago = (t) => {
      const d = new Date(t * 1000), days = Math.floor((Date.now() - d) / 86400000);
      if (days < 1 && d.getDate() === new Date().getDate()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      if (days < 7) return d.toLocaleDateString([], { weekday: "short" });
      return d.toLocaleDateString();
    };
    async function loadChats() {
      const ul = K(root, "chats-list");
      try {
        const { chats } = await api("/api/kitchen/chats");
        K(root, "chats-empty").hidden = chats.length > 0;
        ul.replaceChildren(...chats.map((c) => {
          const open = el("button", { type: "button", class: c.id === chatId ? "on" : "" }, c.title,
            el("small", { text: `${ago(c.updated_at)} · ${c.questions} question${c.questions === 1 ? "" : "s"}` }));
          open.addEventListener("click", () => openChat(c.id).catch((e) => setStatus(chatStatus, e.message, "err")));
          const del = el("button", { type: "button", class: "chat-del", "aria-label": `Delete chat: ${c.title}`, text: "×" });
          del.addEventListener("click", async () => {
            if (!confirm(`Delete "${c.title}"?`)) return;
            try { await api(`/api/kitchen/chats/${c.id}`, { method: "DELETE" }); if (c.id === chatId) newChat(); loadChats(); }
            catch (e) { setStatus(chatStatus, e.message, "err"); }
          });
          return el("li", { class: "chat-row" }, open, del);
        }));
      } catch (e) { ul.replaceChildren(el("li", { class: "status err", text: e.message })); }
    }
    async function openChat(id) {
      const c = await api(`/api/kitchen/chats/${id}`);
      log.replaceChildren();
      for (const x of c.display) {
        if (x.card) recipeCard(x.title, x.markdown, x.card === "recipe");
        else log.append(el("div", { class: `bubble ${x.role}`, text: x.text }));
      }
      history = c.messages; chatId = c.id;
      K(root, "chat-title").textContent = c.title;
      setStatus(chatStatus, "Carry on where you left off. The first answer re-reads your recipes (about $0.30).");
      showChats(false);
      window.scrollTo(0, 0);
    }

    // --- Shared lists (live between phones) ---------------------------------
    const lists = (() => {
      const chips = K(root, "list-chips"), body = K(root, "list-body"), lstatus = K(root, "list-status");
      let all = [], current = null, data = null, rev = 0, polling = false, openEditor = null;
      try { current = Number(localStorage.getItem("list")) || null; } catch (e) { /* ignore */ }

      const renderChips = () => {
        chips.replaceChildren();
        for (const l of all) {
          const b = el("button", { type: "button", class: l.id === current ? "on" : "" }, l.name,
            l.open ? el("span", { class: "count", text: String(l.open) }) : null);
          b.addEventListener("click", () => { current = l.id; openEditor = null; try { localStorage.setItem("list", String(l.id)); } catch (e) { /* ignore */ } load(); });
          chips.append(b);
        }
      };

      const itemRow = (it) => {
        const li = el("li");
        const row = el("div", { class: "item" + (it.checked ? " done" : "") });
        const tick = el("button", { type: "button", class: "tick", "aria-label": it.checked ? "Uncheck" : "Check off", text: it.checked ? "✓" : "○" });
        tick.addEventListener("click", async () => {
          it.checked = !it.checked; render();
          try { await api(`/api/lists/items/${it.id}`, { method: "PATCH", body: { checked: it.checked } }); }
          catch (e) { setStatus(lstatus, e.message, "err"); load(); }
        });
        const label = el("button", { type: "button", class: "label", text: it.text });
        label.addEventListener("click", () => { openEditor = openEditor === it.id ? null : it.id; render(); });
        row.append(tick, label);
        if (it.aisle) row.append(el("span", { class: "badge", text: it.aisle }));
        li.append(row);
        if (openEditor === it.id) li.append(editor(it));
        return li;
      };

      const editor = (it) => {
        const box = el("div", { class: "editor" });
        const text = el("input", { value: it.text, "aria-label": "Item" });
        box.append(text);
        let sel = null, aisle = null;
        if (data.list.kind === "store") {
          sel = el("select", { "aria-label": "Section" });
          for (const s of data.sections) {
            const o = el("option", { value: s, text: s });
            if (s === (it.section || "Other")) o.selected = true;
            sel.append(o);
          }
          aisle = el("input", { value: it.aisle || "", placeholder: data.list.name === "Costco" ? "Bay (optional)" : "Aisle (optional)", inputmode: "text", maxlength: "20" });
          box.append(el("label", {}, "Section (remembered for next time)", sel), el("label", {}, "Aisle (remembered for this store)", aisle));
        }
        const save = el("button", { type: "button", class: "primary", text: "Save" });
        const del = el("button", { type: "button", text: "Remove" });
        save.addEventListener("click", async () => {
          const patch = {};
          if (text.value.trim() && text.value.trim() !== it.text) patch.text = text.value.trim();
          if (sel && sel.value !== it.section) patch.section = sel.value;
          if (aisle && aisle.value.trim() !== (it.aisle || "")) patch.aisle = aisle.value.trim();
          openEditor = null;
          try { if (Object.keys(patch).length) await api(`/api/lists/items/${it.id}`, { method: "PATCH", body: patch }); }
          catch (e) { setStatus(lstatus, e.message, "err"); }
          load();
        });
        del.addEventListener("click", async () => {
          openEditor = null;
          try { await api(`/api/lists/items/${it.id}`, { method: "DELETE" }); } catch (e) { setStatus(lstatus, e.message, "err"); }
          load();
        });
        box.append(el("div", { class: "row" }, save, del));
        return box;
      };

      const render = () => {
        body.replaceChildren();
        if (!data) return;
        const open = data.items.filter((i) => !i.checked), done = data.items.filter((i) => i.checked);
        if (!data.items.length) body.append(el("p", { class: "muted", text: "Nothing on this list." }));
        if (data.list.kind === "store") {
          const order = new Map(data.sections.map((s, i) => [s, i]));
          const groups = new Map();
          for (const it of open) {
            const sec = it.section || "Sorting…";
            if (!groups.has(sec)) groups.set(sec, []);
            groups.get(sec).push(it);
          }
          const keys = [...groups.keys()].sort((a, b) => (order.get(a) ?? -1) - (order.get(b) ?? -1));
          for (const sec of keys) {
            const its = groups.get(sec).sort((a, b) => (a.aisle || "").localeCompare(b.aisle || "", undefined, { numeric: true }));
            body.append(el("h3", { class: "sec-head", text: sec }));
            const ul = el("ul", { class: "items" });
            for (const it of its) ul.append(itemRow(it));
            body.append(ul);
          }
        } else if (open.length) {
          const ul = el("ul", { class: "items" });
          for (const it of open) ul.append(itemRow(it));
          body.append(ul);
        }
        if (done.length) {
          body.append(el("h3", { class: "sec-head", text: data.list.kind === "store" ? `In the cart (${done.length})` : `Done (${done.length})` }));
          const ul = el("ul", { class: "items" });
          for (const it of done) ul.append(itemRow(it));
          body.append(ul);
        }
        K(root, "arrange").hidden = data.list.kind !== "store";
        K(root, "clear-checked").disabled = !done.length;
      };

      const load = async () => {
        try {
          const r = await api("/api/lists");
          all = r.lists;
          if (!all.some((l) => l.id === current)) current = all[0]?.id;
          renderChips();
          data = await api(`/api/lists/${current}`);
          rev = data.rev; setStatus(lstatus, "");
          render();
        } catch (e) { setStatus(lstatus, e.message, "err"); }
      };

      const visible = () => !document.hidden && !root.hidden && !K(root, "list-body").closest("[data-pane]").hidden;
      const poll = async () => {
        if (polling) return;
        polling = true;
        while (visible()) {
          try {
            const r = await api(`/api/lists/changes?since=${rev}`);
            if (r.rev !== rev && visible() && openEditor === null) await load();
            else if (r.rev !== rev) rev = r.rev;
          } catch (e) { await new Promise((res) => setTimeout(res, 5000)); }
        }
        polling = false;
      };
      document.addEventListener("visibilitychange", () => { if (visible()) { load(); poll(); } });

      K(root, "add-form").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const input = K(root, "add-text"), text = input.value.trim();
        if (!text) return;
        input.value = "";
        try {
          const r = await api(`/api/lists/${current}/items`, { method: "POST", body: { texts: [text] } });
          setStatus(lstatus, r.skipped_duplicates.length ? `Already on the list: ${r.skipped_duplicates.join(", ")}` : "");
          await load();
        } catch (e) { input.value = text; setStatus(lstatus, e.message, "err"); }
        input.focus();
      });
      K(root, "clear-checked").addEventListener("click", async () => {
        try { await api(`/api/lists/${current}/clear-checked`, { method: "POST" }); } catch (e) { setStatus(lstatus, e.message, "err"); }
        load();
      });

      // Arrange sections (walk order) with up/down buttons.
      const panel = K(root, "arrange-panel"), alist = K(root, "arrange-list");
      let order = [];
      const renderOrder = () => {
        alist.replaceChildren();
        order.forEach((s, i) => {
          const up = el("button", { type: "button", "aria-label": `Move ${s} up`, text: "↑" });
          const down = el("button", { type: "button", "aria-label": `Move ${s} down`, text: "↓" });
          up.disabled = i === 0; down.disabled = i === order.length - 1;
          up.addEventListener("click", () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; renderOrder(); });
          down.addEventListener("click", () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; renderOrder(); });
          alist.append(el("li", {}, el("span", { text: s }), up, down));
        });
      };
      K(root, "arrange").addEventListener("click", () => { order = [...data.sections]; renderOrder(); panel.hidden = false; panel.scrollIntoView?.({ behavior: "smooth" }); });
      K(root, "arrange-done").addEventListener("click", async () => {
        try { await api(`/api/lists/${current}/sections`, { method: "PUT", body: { sections: order } }); panel.hidden = true; load(); }
        catch (e) { setStatus(lstatus, e.message, "err"); }
      });

      const openList = (id) => { current = id; openEditor = null; try { localStorage.setItem("list", String(id)); } catch (e) { /* ignore */ } };
      return { show: async () => { await load(); poll(); }, open: openList };
    })();

    // --- Saved recipes: search, cook time, open in place ---------------------
    const savedBrowse = K(root, "saved-browse"), savedView = K(root, "saved-view");
    const savedSlot = nav.slot(() => closeSaved());
    let savedMd = "", savedScroll = 0, savedTimer = 0, savedSeq = 0;
    async function loadSaved() {
      const ul = K(root, "saved-list"), q = K(root, "saved-q").value.trim();
      const seq = ++savedSeq;
      try {
        const { recipes } = await api(`/api/kitchen/recipes?q=${encodeURIComponent(q)}`);
        if (seq !== savedSeq) return; // a newer search finished first
        ul.replaceChildren();
        const empty = K(root, "saved-empty");
        empty.textContent = q ? `Nothing matches "${q}".` : "No saved recipes yet.";
        empty.hidden = recipes.length > 0;
        for (const r of recipes) {
          const b = el("button", { type: "button" }, r.title, el("small", { text: r.meta || r.by || "" }));
          b.addEventListener("click", () => openSaved(r.id).catch((e) => setStatus(K(root, "saved-status"), e.message, "err")));
          ul.append(el("li", {}, b));
        }
      } catch (e) { ul.replaceChildren(el("li", { class: "status err", text: e.message })); }
    }
    async function openSaved(id) {
      const full = await api(`/api/kitchen/recipes/${id}`);
      savedMd = full.markdown;
      savedScroll = window.scrollY;
      K(root, "saved-title").textContent = titleOf(savedMd) || "Recipe";
      renderRecipe(savedMd.replace(/^# .*\n?/, ""), K(root, "saved-rendered"));
      K(root, "saved-source").textContent = full.source_url ? `From ${full.source_url}` : "";
      setStatus(K(root, "saved-status"), "");
      savedBrowse.hidden = true; savedView.hidden = false;
      savedSlot.open();
      window.scrollTo(0, 0);
    }
    const closeSaved = () => {
      savedSlot.close();
      savedView.hidden = true; savedBrowse.hidden = false;
      window.scrollTo(0, savedScroll);
    };
    K(root, "saved-back").addEventListener("click", closeSaved);
    K(root, "saved-q").addEventListener("input", () => { clearTimeout(savedTimer); savedTimer = setTimeout(loadSaved, 250); });
    K(root, "saved-send").addEventListener("click", async (ev) => {
      const st = K(root, "saved-status");
      ev.target.disabled = true; setStatus(st, "Sending…");
      try { await api("/api/kitchen/send", { method: "POST", body: { markdown: savedMd } }); setStatus(st, "Sent. It should show on the Hub in a few seconds.", "ok"); }
      catch (e) { setStatus(st, e.message, "err"); } finally { ev.target.disabled = false; }
    });
    K(root, "saved-to-list").addEventListener("click", () => openIngredientPicker(savedMd).catch((e) => setStatus(K(root, "saved-status"), e.message, "err")));

    let startSub = "lists";
    try { startSub = localStorage.getItem("kitchen-sub") || startSub; } catch (e) { /* ignore */ }
    if (ctx.parts && ctx.parts.lists === false && startSub === "lists") startSub = "import";
    showSub(ctx.shared ? "import" : startSub);
    // The Today page's shopping buttons open a list here.
    window.addEventListener("rosie:open-list", (e) => { lists.open(e.detail); showSub("lists"); });

    // Android share target: /?url=…&text=…
    if (ctx.shared) {
      const { url, text } = ctx.shared;
      if (url) K(root, "url").value = url;
      if (text && text !== url) { K(root, "text").value = text; K(root, "text-box").open = true; }
    }
  },
};

// --- Today: overview (#25) -------------------------------------------------
// --- Calendar helpers (#26) -------------------------------------------------
// Events come from Home Assistant through /api/calendar. We send our own local
// midnight, so "today" means today on this phone.
const pad2 = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
function localMidnightISO(d = new Date()) {
  const m = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const off = -m.getTimezoneOffset(), a = Math.abs(off);
  return `${ymd(m)}T00:00:00${off >= 0 ? "+" : "-"}${pad2(Math.floor(a / 60))}:${pad2(a % 60)}`;
}
const fromYmd = (k) => { const [y, mo, d] = k.split("-").map(Number); return new Date(y, mo - 1, d); };
function dayLabel(key, todayKey) {
  const d = fromYmd(key), t = fromYmd(todayKey);
  const diff = Math.round((d - t) / 86400000);
  const date = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  return diff === 0 ? `Today · ${date}` : diff === 1 ? `Tomorrow · ${date}` : date;
}
function loadCalendar(days, from = new Date()) {
  return api(`/api/calendar?days=${days}&start=${encodeURIComponent(localMidnightISO(from))}`);
}
const fmtDay = (d) => d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const fmtTime = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const nextDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
// An event's local [start, end). All-day events run midnight to midnight.
function evSpan(ev) {
  if (ev.all_day) {
    const s = fromYmd(ev.start.slice(0, 10));
    const e = ev.end ? fromYmd(ev.end.slice(0, 10)) : nextDay(s);
    return [s, e > s ? e : nextDay(s)];
  }
  const s = new Date(ev.start), e = ev.end ? new Date(ev.end) : s;
  return [s, e > s ? e : s];
}
// "Mon, Nov 17, 5:00 PM – Mon, Nov 24, 5:00 PM" for events that cross midnight, else "".
function evRange(ev) {
  const [s, e] = evSpan(ev);
  if (ev.all_day) {
    const last = new Date(e.getFullYear(), e.getMonth(), e.getDate() - 1);
    return ymd(last) > ymd(s) ? `${fmtDay(s)} – ${fmtDay(last)}` : "";
  }
  const lastMoment = new Date(e - 1);
  return e > s && ymd(lastMoment) > ymd(s) ? `${fmtDay(s)}, ${fmtTime(s)} – ${fmtDay(e)}, ${fmtTime(e)}` : "";
}
function evRow(ev, time) {
  const dot = el("span", { class: "cal-dot", "aria-hidden": "true" });
  dot.style.setProperty("--c", ev.color);
  const sub = [ev.calendar, ev.location].filter(Boolean).join(" · ");
  const range = evRange(ev);
  return el("li", { class: "cal-ev" }, dot,
    el("span", { class: "cal-time", text: time }),
    el("div", { class: "cal-what" }, el("span", { text: ev.title }), el("small", { text: sub }),
      range ? el("small", { class: "cal-range", text: range }) : null));
}
// Days a multi-day event from a tagged calendar covers (on-call weeks), from
// `from` up to `until`: [{key, text, color}]. The first and last day say when.
function evTagDays(ev, from, until) {
  const out = [];
  if (!ev.tag || !evRange(ev)) return out;
  const [s, e] = evSpan(ev);
  const first = new Date(s.getFullYear(), s.getMonth(), s.getDate());
  for (let d = first < from ? from : first; d < e && d < until; d = nextDay(d)) {
    let text = ev.tag;
    if (!ev.all_day && s > d) text += ` from ${fmtTime(s)}`;
    else if (!ev.all_day && e < nextDay(d)) text += ` until ${fmtTime(e)}`;
    out.push({ key: ymd(d), text, color: ev.color, title: `${ev.calendar}: ${evRange(ev)}` });
  }
  return out;
}
// Groups events by day and draws them. An event that lasts several days shows
// once, on its first day (or today, if it began earlier), with its date range,
// so the list isn't cluttered. Calendars with a `tag` (on call) also put a
// small coloured tag on every day the event covers, so those days stand out.
// onDay(key) makes the day headings open that day. Returns {node, shown, total}.
function renderAgenda(events, { limit = Infinity, onDay = null, days = 28 } = {}) {
  const today = fromYmd(ymd(new Date())), todayKey = ymd(today);
  const until = new Date(today.getFullYear(), today.getMonth(), today.getDate() + days);
  const groups = new Map(), tags = new Map();
  const group = (key) => { if (!groups.has(key)) groups.set(key, []); return groups.get(key); };
  for (const ev of events) {
    let key = ymd(evSpan(ev)[0]);
    if (key < todayKey) key = todayKey;
    group(key).push(ev);
    for (const t of evTagDays(ev, today, until)) {
      group(t.key);
      if (!tags.has(t.key)) tags.set(t.key, []);
      tags.get(t.key).push(t);
    }
  }
  const wrap = el("div", { class: "agenda" });
  let shown = 0;
  for (const key of [...groups.keys()].sort()) {
    if (shown >= limit) break;
    const block = el("div", { class: "cal-block" });
    const label = dayLabel(key, todayKey);
    if (onDay) {
      const b = el("button", { type: "button", class: "cal-head-btn", text: label, "aria-label": `Show everything on ${label}` });
      b.addEventListener("click", () => onDay(key));
      block.append(el("h3", { class: "cal-head" }, b));
    } else {
      block.append(el("h3", { class: "cal-head", text: label }));
    }
    const dayTags = tags.get(key) || [];
    if (dayTags.length) {
      block.classList.add("tagged");
      block.style.setProperty("--c", dayTags[0].color);
      const row = el("div", { class: "cal-tags" });
      for (const t of dayTags) {
        const chip = el("span", { class: "cal-tag", title: t.title, text: t.text });
        chip.style.setProperty("--c", t.color);
        row.append(chip);
      }
      block.append(row);
    }
    const evs = groups.get(key);
    if (evs.length) {
      const ul = el("ul", { class: "cal-day" });
      for (const ev of evs) {
        if (shown >= limit) break;
        shown++;
        const st = evSpan(ev)[0];
        const time = ev.all_day ? "All day" : ymd(st) < todayKey ? "Ongoing" : fmtTime(st);
        ul.append(evRow(ev, time));
      }
      block.append(ul);
    }
    wrap.append(block);
  }
  return { node: wrap, shown, total: events.length };
}
// One day: everything happening on it, including events that started earlier
// or carry on past midnight (an on-call week shows on every day of the week).
function renderDay(events, key) {
  const d0 = fromYmd(key), d1 = nextDay(d0);
  const rows = [];
  for (const ev of events) {
    const [s, e] = evSpan(ev);
    if (!(s < d1 && (e > d0 || (+e === +s && s >= d0)))) continue;
    let time, rank = 1;
    if (ev.all_day || (s <= d0 && e >= d1)) { time = "All day"; rank = 0; }
    else if (s < d0) time = `Until ${fmtTime(e)}`;
    else if (e > d1) time = `From ${fmtTime(s)}`;
    else time = fmtTime(s);
    rows.push({ ev, time, rank, at: s < d0 ? d0 : s });
  }
  rows.sort((a, b) => a.rank - b.rank || a.at - b.at || a.ev.title.localeCompare(b.ev.title));
  if (!rows.length) return el("p", { class: "muted", text: "Nothing on the calendar this day." });
  const ul = el("ul", { class: "cal-day" });
  for (const r of rows) ul.append(evRow(r.ev, r.time));
  return ul;
}

sections.today = {
  mount(root) {
    const hello = el("section", { class: "card today-hello" });
    const todoCard = el("section", { class: "card" });
    const shopCard = el("section", { class: "card" });
    const calCard = el("section", { class: "card" }, el("h2", { text: "This week" }));
    root.append(hello, todoCard, shopCard, calCard);
    let todoId = null, busy = false;

    const greeting = () => {
      const h = new Date().getHours();
      return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    };
    const openList = (id) => {
      window.rosieShow?.("kitchen");
      setTimeout(() => window.dispatchEvent(new CustomEvent("rosie:open-list", { detail: id })), 0);
    };

    async function loadWeek() {
      let c;
      try { c = await loadCalendar(7); }
      catch (e) { calCard.replaceChildren(el("h2", { text: "This week" }), el("p", { class: "status err", text: e.message })); return; }
      const kids = [el("h2", { text: "This week" })];
      if (!c.calendars.length) {
        kids.push(el("p", { class: "muted", text: "No calendars yet. They show here once they're connected in Home Assistant and listed in home.toml." }));
      } else if (!c.events.length) {
        kids.push(el("p", { class: "muted", text: "Nothing on the calendar this week." }));
      } else {
        const a = renderAgenda(c.events, { limit: 10, days: 7 });
        kids.push(a.node);
      }
      for (const err of c.errors) kids.push(el("p", { class: "status err", text: err }));
      if (c.calendars.length) {
        const open = el("button", { type: "button", class: "link", text: "Open Calendar" });
        open.addEventListener("click", () => window.rosieShow?.("calendar"));
        kids.push(open);
      }
      calCard.replaceChildren(...kids);
    }

    async function load() {
      if (busy) return;
      loadWeek();
      let t;
      try { t = await api("/api/today"); } catch (e) { errorCard(root, e.message); return; }
      todoId = t.todo.list_id;
      hello.replaceChildren(
        el("h2", { text: `${greeting()}, ${t.name}` }),
        el("p", { class: "muted", text: new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) }));

      const ul = el("ul", { class: "items today-todo" });
      for (const it of t.todo.items) {
        const tick = el("button", { type: "button", class: "tick", "aria-label": `Check off ${it.text}`, text: "○" });
        tick.addEventListener("click", async () => {
          busy = true; tick.disabled = true; tick.textContent = "✓";
          try { await api(`/api/lists/items/${it.id}`, { method: "PATCH", body: { checked: true } }); }
          catch (e) { tick.disabled = false; tick.textContent = "○"; }
          finally { busy = false; setTimeout(load, 500); }
        });
        ul.append(el("li", { class: "item" }, tick, el("span", { class: "label", text: it.text })));
      }
      const more = t.todo.open - t.todo.items.length;
      const all = el("button", { type: "button", class: "link", text: more > 0 ? `See all ${t.todo.open}` : "Open the To-do list" });
      all.addEventListener("click", () => todoId && openList(todoId));
      const add = el("form", { class: "add-row" },
        el("input", { placeholder: "Add a to-do", autocomplete: "off", enterkeyhint: "done", "aria-label": "Add a to-do" }),
        el("button", { type: "submit", class: "primary", text: "Add" }));
      add.addEventListener("submit", async (e) => {
        e.preventDefault();
        const inp = add.querySelector("input"), text = inp.value.trim();
        if (!text || !todoId) return;
        try { await api(`/api/lists/${todoId}/items`, { method: "POST", body: { texts: [text] } }); inp.value = ""; load(); }
        catch (err) { errorCard(root, err.message); }
      });
      todoCard.replaceChildren(
        el("h2", { text: t.todo.open ? `To-do (${t.todo.open})` : "To-do" }),
        t.todo.items.length ? ul : el("p", { class: "muted", text: "Nothing to do. Nice." }),
        add, all);

      const chips = el("div", { class: "chips" });
      for (const l of t.shopping) {
        const b = el("button", { type: "button" }, l.name, el("span", { class: "count", text: l.open ? String(l.open) : "–" }));
        b.setAttribute("aria-label", `${l.name}: ${l.open} item${l.open === 1 ? "" : "s"}`);
        b.addEventListener("click", () => openList(l.id));
        chips.append(b);
      }
      shopCard.replaceChildren(el("h2", { text: "Shopping" }), chips);
    }
    load();
    window.addEventListener("rosie:shown", (e) => { if (e.detail === "today") load(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && !root.hidden) load(); });
  },
};

sections.calendar = {
  mount(root) {
    const card = el("section", { class: "card" }, el("p", { class: "muted", text: "Loading…" }));
    root.append(card);
    let day = null; // null = the 4-week list; else "YYYY-MM-DD"
    let seq = 0;
    const daySlot = nav.slot(() => { day = null; load(); });
    const openDay = (key) => { day = key; daySlot.open(); load(); window.scrollTo?.(0, 0); };
    const showList = () => { day = null; daySlot.close(); load(); };
    const shift = (n) => { const d = fromYmd(day); openDay(ymd(new Date(d.getFullYear(), d.getMonth(), d.getDate() + n))); };

    function picker(value) {
      const input = el("input", { type: "date", class: "cal-pick", "aria-label": "Pick a day" });
      input.value = value || "";
      input.addEventListener("change", () => { if (input.value) openDay(input.value); });
      return input;
    }

    async function loadDay(my) {
      const key = day;
      const head = el("div", { class: "cal-daybar" });
      const prev = el("button", { type: "button", class: "secondary", text: "‹", "aria-label": "Previous day" });
      const next = el("button", { type: "button", class: "secondary", text: "›", "aria-label": "Next day" });
      const back = el("button", { type: "button", class: "link", text: "Back to 4 weeks" });
      prev.addEventListener("click", () => shift(-1));
      next.addEventListener("click", () => shift(1));
      back.addEventListener("click", showList);
      head.append(prev, picker(key), next);
      let c;
      try { c = await loadCalendar(1, fromYmd(key)); }
      catch (e) { if (my === seq) card.replaceChildren(head, el("p", { class: "status err", text: e.message }), back); return; }
      if (my !== seq) return;
      const kids = [el("h2", { text: dayLabel(key, ymd(new Date())) }), head];
      for (const err of c.errors) kids.push(el("p", { class: "status err", text: err }));
      kids.push(renderDay(c.events, key), back);
      card.replaceChildren(...kids);
      root.querySelector(".cal-unlisted")?.remove();
    }

    async function loadList(my) {
      let c;
      try { c = await loadCalendar(28); }
      catch (e) { if (my === seq) card.replaceChildren(el("p", { class: "status err", text: e.message })); return; }
      if (my !== seq) return;
      const kids = [el("h2", { text: "Next 4 weeks" })];
      if (!c.configured) kids.push(el("p", { class: "muted", text: "Home Assistant isn't connected (no HA_TOKEN)." }));
      if (c.calendars.length) {
        const legend = el("div", { class: "cal-legend" });
        for (const k of c.calendars) {
          const dot = el("span", { class: "cal-dot", "aria-hidden": "true" });
          dot.style.setProperty("--c", k.color);
          legend.append(el("span", {}, dot, k.label));
        }
        kids.push(legend, el("label", { class: "cal-goto" }, "See one day", picker("")));
      } else {
        kids.push(el("p", { class: "muted", text: "No calendars are listed yet. Connect them in Home Assistant, then list them under [[calendars]] in home.toml." }));
      }
      for (const err of c.errors) kids.push(el("p", { class: "status err", text: err }));
      if (c.calendars.length) {
        kids.push(c.events.length ? renderAgenda(c.events, { onDay: openDay }).node : el("p", { class: "muted", text: "Nothing in the next 4 weeks." }));
      }
      card.replaceChildren(...kids);
      root.querySelector(".cal-unlisted")?.remove();
      if (c.unlisted && c.unlisted.length) {
        const toml = c.unlisted.map((u) => `[[calendars]]\nentity = "${u.entity}"\nlabel = "${(u.name || u.entity).replace(/["\\]/g, "")}"`).join("\n\n");
        root.append(el("section", { class: "card cal-unlisted" },
          el("h2", { text: "Not shown yet" }),
          el("p", { class: "muted", text: "Home Assistant has these calendars, but home.toml doesn't list them. To show one, add its lines to home.toml and redeploy. Only you (admin) see this." }),
          el("pre", { text: toml })));
      }
    }

    function load() { const my = ++seq; return day ? loadDay(my) : loadList(my); }
    load();
    // Coming back to Calendar from another section starts at the list again.
    window.addEventListener("rosie:shown", (e) => { if (e.detail === "calendar") { day = null; load(); } });
  },
};

sections.internet = {
  mount(root) {
    root.append($("#tpl-internet").content.cloneNode(true));
    // Controls: the [[rooms]] with section = "internet" in home.toml (the 5G
    // switch, the modem power cycle). Same allow-list and confirm rules as Home.
    const controls = K(root, "controls");
    let busy = false;
    const act = async (t, action) => {
      const what = action === "run" ? `Run ${t.label}?` : `Turn ${t.label} ${action}?`;
      if (t.confirm && !confirm(what)) return;
      busy = true;
      try { await api("/api/home/action", { method: "POST", body: { entity: t.entity, action, confirmed: t.confirm } }); }
      catch (e) { errorCard(root, e.message); }
      finally { busy = false; }
      setTimeout(load, 1500);
    };
    const loadControls = async () => {
      let s;
      try { s = await api("/api/home/status?section=internet"); } catch (e) { controls.replaceChildren(); return; }
      controls.replaceChildren();
      if (!s.configured) return;
      for (const room of s.rooms) {
        const grid = el("div", { class: "htiles" });
        for (const t of room.tiles) {
          if (t.kind !== "toggle" && t.kind !== "script") continue;
          const off = !t.available;
          const b = el("button", { type: "button", class: "htile" + (t.on && t.kind === "toggle" ? " on" : "") },
            el("span", { class: "label", text: t.label }),
            el("span", { class: "summary", text: off ? "Unavailable" : t.kind === "script" ? "Run" : t.on ? "On" : "Off" }));
          b.disabled = off;
          b.addEventListener("click", () => act(t, t.kind === "script" ? "run" : t.on ? "off" : "on"));
          grid.append(b);
        }
        controls.append(el("h3", { class: "sec-head", text: room.name }), grid);
      }
    };
    const load = async () => {
      if (busy) return;
      loadControls();
      try {
        const s = await api("/api/internet/status");
        const box = K(root, "tiles");
        box.replaceChildren();
        for (const t of s.tiles) {
          const ul = el("ul", { class: "details" });
          if (t.until) ul.append(el("li", { text: `Back to cable by ${new Date(t.until * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} at the latest` }));
          for (const d of t.details) ul.append(el("li", { text: d }));
          box.append(el("a", { class: `tile ${t.state}`, href: t.link, target: "_blank", rel: "noopener" },
            el("span", { class: "label", text: t.label }), el("span", { class: "summary", text: t.summary }), ul));
        }
        K(root, "checked").textContent = s.checked_at
          ? `Last checked ${new Date(s.checked_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${s.stale ? " (old data: monitoring may be down)" : ""}. Tap a tile for the Grafana dashboard.`
          : "No monitoring data.";
      } catch (e) { errorCard(root, e.message); }
    };
    load();
    this.timer = setInterval(() => { if (!document.hidden && !root.hidden) load(); }, 60000);
  },
};

function placeholder(id) {
  return {
    async mount(root) {
      root.append($("#tpl-placeholder").content.cloneNode(true));
      const s = await api(`/api/${id}/status`);
      K(root, "message").textContent = s.message;
    },
  };
}
sections.media = placeholder("media");
sections.home = {
  mount(root) {
    const wrap = el("div"), foot = el("p", { class: "muted" });
    root.append(wrap, foot);
    let busy = false;
    const act = async (t, action, value, extra = {}) => {
      let confirmed = false;
      if (t.confirm) {
        const what = action === "run" ? `Run ${t.label}?` : `Turn ${t.label} ${action}?`;
        if (!confirm(what)) return;
        confirmed = true;
      }
      busy = true;
      try { await api("/api/home/action", { method: "POST", body: { entity: t.entity, action, value, confirmed, ...extra } }); }
      catch (e) { errorCard(root, e.message); }
      finally { busy = false; }
      setTimeout(load, 600);
    };
    const tileEl = (t) => {
      const off = !t.available;
      if (t.kind === "thermostat" && t.range) {
        // heat_cool ("auto"): heat-to and cool-to targets, kept at least one step apart
        const lo = t.low, hi = t.high, st = t.step;
        const setLH = (l, h) => act(t, "set_temperature", null, { low: l, high: h });
        const pair = (name, val, down, up, canDown, canUp) => {
          const m = el("button", { type: "button", "aria-label": `Lower ${name}`, text: "−" });
          const p = el("button", { type: "button", "aria-label": `Raise ${name}`, text: "+" });
          m.disabled = off || !canDown; p.disabled = off || !canUp;
          m.addEventListener("click", down); p.addEventListener("click", up);
          return el("div", { class: "thermo-pair" }, el("span", { text: `${name} ${val}°` }), m, p);
        };
        return el("div", { class: "htile thermo" },
          el("span", { class: "label", text: t.label }),
          el("span", { class: "summary", text: off ? "Unavailable" : `${t.current ?? "–"}° now · auto` }),
          pair("Heat to", lo, () => setLH(lo - st, hi), () => setLH(lo + st, hi), lo - st >= t.min, lo + st < hi),
          pair("Cool to", hi, () => setLH(lo, hi - st), () => setLH(lo, hi + st), hi - st > lo, hi + st <= t.max));
      }
      if (t.kind === "thermostat") {
        const target = t.target ?? t.min;
        const minus = el("button", { type: "button", "aria-label": "Lower", text: "−" });
        const plus = el("button", { type: "button", "aria-label": "Raise", text: "+" });
        minus.disabled = off || target - t.step < t.min; plus.disabled = off || target + t.step > t.max;
        minus.addEventListener("click", () => act(t, "set_temperature", target - t.step));
        plus.addEventListener("click", () => act(t, "set_temperature", target + t.step));
        return el("div", { class: "htile thermo" },
          el("span", { class: "label", text: t.label }),
          el("span", { class: "summary", text: off ? "Unavailable" : `${t.current ?? "–"}° now · set ${target}°` }),
          el("div", { class: "row" }, minus, plus));
      }
      if (t.kind === "status") {
        return el("div", { class: "htile status" }, el("span", { class: "label", text: t.label }),
          el("span", { class: "summary", text: off ? "Unavailable" : t.state.replace(/_/g, " ") }));
      }
      const b = el("button", { type: "button", class: "htile" + (t.on && t.kind === "toggle" ? " on" : "") },
        el("span", { class: "label", text: t.label }),
        el("span", { class: "summary", text: off ? "Unavailable" : t.kind === "script" ? "Run" : t.on ? "On" : "Off" }));
      b.disabled = off;
      b.addEventListener("click", () => act(t, t.kind === "script" ? "run" : t.on ? "off" : "on"));
      return b;
    };
    const load = async () => {
      if (busy) return;
      try {
        const s = await api("/api/home/status");
        root.querySelector(".error-tile")?.remove();
        wrap.replaceChildren();
        if (!s.configured) {
          wrap.append(el("section", { class: "card" }, el("p", { text: s.message })));
        } else {
          for (const room of s.rooms) {
            const grid = el("div", { class: "htiles" });
            for (const t of room.tiles) grid.append(tileEl(t));
            wrap.append(el("h3", { class: "sec-head", text: room.name }), grid);
          }
        }
        foot.replaceChildren();
        if (s.dashboard_url) foot.append("Everything else: ", el("a", { href: s.dashboard_url, target: "_blank", rel: "noopener", text: "open Home Assistant" }), ".");
      } catch (e) { errorCard(root, e.message); }
    };
    load();
    setInterval(() => { if (!document.hidden && !root.hidden) load(); }, 30000);
  },
};
// --- Developer: Forgejo issues + chat (admin only, #10) --------------------
sections.developer = {
  async mount(root) {
    let st;
    try { st = await api("/api/developer/status"); } catch (e) { errorCard(root, e.message); return; }
    if (!st.configured) { root.append(el("section", { class: "card" }, el("p", { class: "muted", text: st.message }))); return; }
    let repo = st.repos[0], state = "open", current = null, history = [], qTimer = 0;
    try { const r = localStorage.getItem("dev-repo"); if (st.repos.includes(r)) repo = r; } catch (e) { /* ignore */ }

    const chips = el("div", { class: "chips" });
    const stateSel = el("select", { "aria-label": "Which issues" },
      el("option", { value: "open", text: "Open" }), el("option", { value: "closed", text: "Closed" }), el("option", { value: "all", text: "All" }));
    const q = el("input", { type: "search", class: "search", placeholder: "Search issues", autocomplete: "off", enterkeyhint: "search", "aria-label": "Search issues" });
    const newBtn = el("button", { type: "button", text: "New issue" });
    const listStatus = el("p", { class: "status", role: "status" });
    const ul = el("ul", { class: "list" });
    const browse = el("div", {}, chips, el("div", { class: "row dev-filters" }, stateSel, newBtn), q, listStatus, el("section", { class: "card" }, ul));
    const view = el("div", { hidden: "" });
    const log = el("div", { class: "chat-log" });
    const chatText = el("textarea", { rows: "2", placeholder: "Ask about these issues…" });
    const askBtn = el("button", { type: "button", class: "primary", text: "Ask" });
    const resetBtn = el("button", { type: "button", text: "New chat" });
    const chatStatus = el("p", { class: "status", role: "status" });
    const chatCard = el("section", { class: "card chat-input" }, chatText, el("div", { class: "row" }, askBtn, resetBtn), chatStatus);
    root.append(browse, view, el("h2", { class: "dev-chat-head", text: "Chat" }), log, chatCard);
    if (!st.write_enabled) newBtn.hidden = true;
    if (!st.ai) chatCard.replaceChildren(el("p", { class: "muted", text: "Chat needs the Anthropic API key." }));

    const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : "");
    const resetChat = () => {
      history = []; log.replaceChildren(); setStatus(chatStatus, "");
      chatText.placeholder = current ? `Ask about #${current.number}…` : "Ask about these issues…";
    };

    const drawChips = () => {
      chips.replaceChildren(...st.repos.map((r) => {
        const b = el("button", { type: "button", class: r === repo ? "on" : "", text: r.split("/")[1] });
        b.addEventListener("click", () => { repo = r; try { localStorage.setItem("dev-repo", r); } catch (e) { /* ignore */ } current = null; showBrowse(); });
        return b;
      }));
    };
    async function loadList() {
      setStatus(listStatus, "Loading…");
      try {
        const { issues } = await api(`/api/developer/issues?repo=${encodeURIComponent(repo)}&state=${state}&q=${encodeURIComponent(q.value.trim())}`);
        ul.replaceChildren(...issues.map((i) => {
          const meta = [i.state === "closed" ? "closed" : "", i.labels.join(", "), i.comments ? `${i.comments} comment${i.comments > 1 ? "s" : ""}` : "", when(i.updated_at)].filter(Boolean).join(" · ");
          const b = el("button", { type: "button" }, `#${i.number} ${i.title}`, el("small", { text: meta }));
          b.addEventListener("click", () => openIssue(i.number));
          return el("li", {}, b);
        }));
        setStatus(listStatus, issues.length ? "" : "No issues here.");
      } catch (e) { setStatus(listStatus, e.message, "err"); }
    }
    const issueSlot = nav.slot(() => showBrowse());
    function showBrowse() {
      issueSlot.close();
      view.hidden = true; browse.hidden = false; current = null;
      drawChips(); resetChat(); loadList();
    }
    async function openIssue(number) {
      setStatus(listStatus, "Opening…");
      let i;
      try { i = await api(`/api/developer/issue?repo=${encodeURIComponent(repo)}&number=${number}`); }
      catch (e) { setStatus(listStatus, e.message, "err"); return; }
      setStatus(listStatus, "");
      current = i;
      const back = el("button", { type: "button", class: "link", text: "‹ All issues" });
      back.addEventListener("click", showBrowse);
      const head = el("section", { class: "card" },
        back,
        el("h2", { text: `#${i.number} ${i.title}` }),
        el("p", { class: "muted", text: [i.state, i.labels.join(", "), `opened by ${i.user} ${when(i.created_at)}`].filter(Boolean).join(" · ") }),
        el("div", { class: "issue-body", text: i.body || "(no description)" }),
        el("a", { href: i.url, target: "_blank", rel: "noopener", text: "Open in Forgejo" }));
      const thread = i.thread.map((c) => el("section", { class: "card comment" },
        el("p", { class: "muted", text: `${c.user} · ${when(c.created_at)}` }), el("div", { class: "issue-body", text: c.body })));
      view.replaceChildren(head, ...thread);
      if (st.write_enabled) view.append(composer("comment", "", ""));
      browse.hidden = true; view.hidden = false;
      issueSlot.open();
      resetChat(); window.scrollTo(0, 0);
    }

    // A draft (from chat or typed) that the admin edits, then posts after confirming.
    function composer(kind, title, body) {
      const t = el("input", { value: title, placeholder: "Title", maxlength: "200" });
      const b = el("textarea", { rows: kind === "issue" ? "8" : "4", placeholder: kind === "issue" ? "Description (markdown)" : "Write a comment…" });
      b.value = body;
      const where = kind === "issue" ? repo : `${repo}#${current && current.number}`;
      const post = el("button", { type: "button", class: "primary", text: kind === "issue" ? `Create issue in ${repo.split("/")[1]}` : "Post comment" });
      const msg = el("p", { class: "status", role: "status" });
      const box = el("section", { class: "card draft" }, el("p", { class: "muted", text: kind === "issue" ? `New issue · ${repo}` : `Comment on #${current && current.number}` }));
      if (kind === "issue") box.append(t);
      box.append(b);
      if (st.write_enabled) box.append(el("div", { class: "row" }, post));
      else {
        const copy = el("button", { type: "button", text: "Copy" });
        copy.addEventListener("click", () => navigator.clipboard.writeText((kind === "issue" ? t.value + "\n\n" : "") + b.value).then(() => setStatus(msg, "Copied.", "ok"), () => setStatus(msg, "Couldn't copy.", "err")));
        box.append(el("div", { class: "row" }, copy), el("p", { class: "muted", text: "Posting is switched off (FORGEJO_WRITE)." }));
      }
      box.append(msg);
      const number = current && current.number;
      post.addEventListener("click", async () => {
        if (kind === "issue" && !t.value.trim()) { setStatus(msg, "Give it a title first.", "err"); return; }
        if (!b.value.trim() && kind === "comment") { setStatus(msg, "The comment is empty.", "err"); return; }
        if (!confirm(kind === "issue" ? `Create "${t.value.trim()}" in ${repo}?` : `Post this comment on ${where}?`)) return;
        post.disabled = true; setStatus(msg, "Posting…");
        try {
          if (kind === "issue") {
            const r = await api("/api/developer/issues", { method: "POST", body: { repo, title: t.value, body: b.value, confirmed: true } });
            msg.replaceChildren("Created ", el("a", { href: r.url, target: "_blank", rel: "noopener", text: `#${r.number}` }), ".");
            msg.className = "status ok";
          } else {
            await api("/api/developer/comments", { method: "POST", body: { repo, number, body: b.value, confirmed: true } });
            setStatus(msg, "Posted.", "ok");
            if (current && current.number === number) setTimeout(() => openIssue(number), 800);
          }
        } catch (e) { setStatus(msg, e.message, "err"); post.disabled = false; }
      });
      return box;
    }

    const bubble = (role, text) => { const b = el("div", { class: `bubble ${role}`, text }); log.append(b); b.scrollIntoView?.({ block: "end", behavior: "smooth" }); };
    async function ask() {
      const text = chatText.value.trim();
      if (!text) return;
      askBtn.disabled = true; setStatus(chatStatus, "Thinking… (about 10–30 seconds)");
      bubble("user", text);
      const msgs = [...history, { role: "user", content: text }];
      chatText.value = "";
      try {
        const r = await api("/api/developer/chat", { method: "POST", body: { repo, number: current ? current.number : null, state, messages: msgs } });
        history = [...msgs, { role: "assistant", content: r.history_text }];
        if (r.reply) bubble("assistant", r.reply);
        for (const d of r.drafts) {
          if (d.kind === "comment" && !current) continue;
          log.append(composer(d.kind, d.title, d.body));
        }
        setStatus(chatStatus, `AI this month: $${r.month_spent_usd.toFixed(2)}`);
      } catch (e) {
        log.lastElementChild?.remove();
        chatText.value = text;
        setStatus(chatStatus, e.message, "err");
      } finally { askBtn.disabled = false; }
    }
    askBtn.addEventListener("click", ask);
    chatText.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask(); });
    resetBtn.addEventListener("click", resetChat);
    stateSel.addEventListener("change", () => { state = stateSel.value; resetChat(); loadList(); });
    q.addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(loadList, 300); });
    newBtn.addEventListener("click", () => {
      const c = composer("issue", "", "");
      log.append(c); c.scrollIntoView?.({ block: "start", behavior: "smooth" });
      c.querySelector("input").focus();
    });
    showBrowse();
  },
};

function errorCard(root, msg) {
  root.querySelector(".error-tile")?.remove();
  root.prepend(el("section", { class: "card error-tile" },
    el("strong", { text: "This section isn't working right now." }), el("p", { class: "muted", text: msg })));
}

// --- Shell -----------------------------------------------------------------
function showLogin() {
  $("#login").hidden = false; $("#views").hidden = true; $("#menu-btn").hidden = true;
  $("#section-name").textContent = "";
  if ($("#drawer").open) closeMenu();
}

async function start() {
  const q = new URLSearchParams(location.search);
  const sharedUrl = q.get("url") || (q.get("text") || "").match(/https?:\/\/\S+/)?.[0] || "";
  const shared = q.has("url") || q.has("text") ? { url: sharedUrl, text: q.get("text") || "" } : null;
  if (shared) history.replaceState(history.state, "", "/");

  let me;
  try { me = await api("/api/me"); } catch (e) { if (!$("#login").hidden) return; errorCard($("#views"), e.message); return; }
  $("#login").hidden = true; $("#views").hidden = false; $("#menu-btn").hidden = false;

  const views = $("#views");
  views.replaceChildren();
  const mounted = {};
  let saved = "kitchen";
  try { saved = localStorage.getItem("tab") || saved; } catch (e) { /* storage blocked */ }

  // Back from any other section returns to Today (or the first section).
  const home = me.sections.some((s) => s.id === "today") ? "today" : me.sections[0].id;
  const show = async (id, { initial = false } = {}) => {
    nav.clear(); // switching sections drops the old section's open views
    if (!initial && id !== home) nav.push(() => show(home));
    for (const v of views.children) v.hidden = v.dataset.id !== id;
    $("#drawer").dataset.current = id;
    try { localStorage.setItem("tab", id); } catch (e) { /* ignore */ }
    const s = me.sections.find((x) => x.id === id);
    $("#section-name").textContent = s.title;
    window.dispatchEvent(new CustomEvent("rosie:shown", { detail: id }));
    if (!mounted[id]) {
      mounted[id] = true;
      const root = views.querySelector(`[data-id="${id}"]`);
      if (!s.ok || !sections[id]) { errorCard(root, "This section failed to load on the server."); return; }
      try { await sections[id].mount(root, { shared: id === "kitchen" ? shared : null, parts: me.parts || {} }); }
      catch (e) { errorCard(root, e.message); }
    }
  };

  for (const s of me.sections) {
    views.append(el("div", { "data-id": s.id, hidden: "" }));
  }
  // A fresh launch opens on Today (#25); a share opens Kitchen's Import tab.
  const has = (id) => me.sections.some((s) => s.id === id);
  const first = shared ? "kitchen" : has("today") ? "today" : has(saved) ? saved : me.sections[0].id;
  window.rosieShow = show;
  buildMenu(me, show);
  show(first, { initial: true });
}

// --- App menu (☰): sections, links, account (#24) ------------------------
function buildMenu(me, show) {
  const secs = $("#drawer-sections"), links = $("#drawer-links");
  secs.replaceChildren(...me.sections.map((s) => {
    const b = el("button", { type: "button", "data-id": s.id, class: s.ok ? "" : "broken", text: s.title });
    b.addEventListener("click", () => { closeMenu(); show(s.id); });
    return el("li", {}, b);
  }));
  links.replaceChildren(...(me.links || []).map((l) =>
    el("li", {}, el("a", { href: l.url, target: "_blank", rel: "noopener", text: l.label }))));
  $("#drawer-links-head").hidden = !(me.links || []).length;
  const u = me.user || {};
  $("#drawer-user").textContent = u.display_name ? `Signed in as ${u.display_name}` : "";
}
function openMenu() {
  const d = $("#drawer");
  for (const b of $("#drawer-sections").querySelectorAll("button")) {
    const cur = b.dataset.id === d.dataset.current;
    b.classList.toggle("on", cur);
    if (cur) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  d.classList.remove("closing");
  d.showModal();
  menuLayer = nav.push(closeMenu);
  $("#menu-btn").setAttribute("aria-expanded", "true");
}
let menuLayer = null;
function closeMenu() {
  const d = $("#drawer");
  if (menuLayer) { nav.remove(menuLayer); menuLayer = null; }
  if (!d.open || d.classList.contains("closing")) return;
  $("#menu-btn").setAttribute("aria-expanded", "false");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { d.close(); return; }
  d.classList.add("closing");
  setTimeout(() => { d.classList.remove("closing"); d.close(); }, 180);
}
$("#menu-btn").addEventListener("click", openMenu);
$("#drawer").addEventListener("click", (e) => {
  // A click on the dialog itself (not the panel) is the dimmed backdrop.
  if (e.target === e.currentTarget || e.target.closest("[data-close]")) closeMenu();
});
$("#drawer").addEventListener("cancel", (e) => { e.preventDefault(); closeMenu(); }); // Esc
$("#drawer-links").addEventListener("click", (e) => { if (e.target.closest("a")) closeMenu(); });

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const st = $("#login-status");
  setStatus(st, "Signing in…");
  try {
    await api("/api/login", { method: "POST", body: { username: $("#username").value.trim(), password: $("#password").value } });
    $("#password").value = ""; setStatus(st, "");
    start();
  } catch (e) { setStatus(st, e.message, "err"); }
});

$("#signout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (e) { /* ignore */ }
  location.reload();
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
start();
