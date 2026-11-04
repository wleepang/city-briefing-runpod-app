// Pure rendering helpers for the briefing page: no DOM access, so they can be unit tested in Node.
// Loaded by index.html as a plain script (functions become globals) and by tests via require().
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

// Minimal, safe Markdown for the model's answer. Everything is HTML-escaped FIRST, then a small set of
// patterns is turned into tags, so model output can never inject markup. Supports headings, bold,
// italic, `code`, bullet/numbered lists, paragraphs, and http(s) links.
function inlineMd(escaped) {
  // Code spans are lifted out first so nothing inside them gets formatted, then put back at the end.
  const codes = [];
  const out = escaped
    .replace(/\u0000/g, "")
    .replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`)
    .replace(/\*\*([^*]+?)\*\*|__([^_]+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
    .replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?![*\w])/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    // The model's source tags jump to the matching card further down the page.
    .replace(/\[(Open-Meteo|Wikipedia)\](?!\()/g, (m, src) => `<a class="cite" href="#card-${src.toLowerCase()}" title="Jump to the ${src} data">${m}</a>`);
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

// Link landmark names the model mentions to their Wikipedia articles. Works on text segments only
// (never inside tags or existing links), and only for URLs our own backend returned.
function linkPlaces(html, nearby) {
  const terms = (nearby || [])
    .filter((n) => String(n.url).startsWith("https://en.wikipedia.org/"))
    .map((n) => ({ name: esc(n.title.replace(/\s*\([^)]*\)\s*$/, "")), url: n.url }))
    .filter((t) => t.name.length >= 4)
    .sort((a, b) => b.name.length - a.name.length);
  if (!terms.length) return html;
  const urlByName = Object.fromEntries(terms.map((t) => [t.name, t.url]));
  const re = new RegExp("(?<!\\w)(?:" + terms.map((t) => t.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")(?!\\w)", "g");
  let inAnchor = false;
  return html.split(/(<[^>]+>)/).map((part) => {
    if (part.startsWith("<")) {
      if (/^<a[\s>]/i.test(part)) inAnchor = true;
      else if (/^<\/a>/i.test(part)) inAnchor = false;
      return part;
    }
    return inAnchor ? part : part.replace(re, (m) => `<a href="${esc(urlByName[m])}" target="_blank" rel="noopener noreferrer">${m}</a>`);
  }).join("");
}

function renderMarkdown(text, nearby) {
  const out = [];
  let para = [];
  let list = null; // { tag, items }
  const flushPara = () => { if (para.length) out.push(`<p>${para.map(inlineMd).join("<br>")}</p>`); para = []; };
  const flushList = () => { if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inlineMd(i)}</li>`).join("")}</${list.tag}>`); list = null; };

  for (const raw of esc(text).split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if (heading) { flushPara(); flushList(); out.push(`<h3>${inlineMd(heading[1])}</h3>`); continue; }
    if (bullet || numbered) {
      flushPara();
      const tag = bullet ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      (list ||= { tag, items: [] }).items.push((bullet || numbered)[1]);
      continue;
    }
    flushList();
    para.push(line.trim());
  }
  flushPara(); flushList();
  return linkPlaces(out.join(""), nearby);
}

// Split what has arrived of a server-sent-event stream into complete events and the unfinished remainder
// (keep the remainder and add the next chunk to it). Comment lines (keepalives) and garbled events are dropped.
function parseSse(buffer) {
  const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = blocks.pop();
  const events = [];
  for (const block of blocks) {
    let event = "message";
    const data = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) continue;
    try { events.push({ event, data: JSON.parse(data.join("\n")) }); } catch { /* garbled event */ }
  }
  return { events, rest };
}

if (typeof module !== "undefined") module.exports = { esc, inlineMd, linkPlaces, renderMarkdown, parseSse };
