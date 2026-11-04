// Tests for public/render.js, the code that turns untrusted model text into HTML.
// No DOM here, so assertions use a small tag scanner. Run with: npm run test:frontend
const test = require("node:test");
const assert = require("node:assert/strict");
const { esc, renderMarkdown, parseSse } = require("../public/render.js");

/** [{ name, closing, attrs: {k: v} }] for every tag in `html`. */
function tags(html) {
  return [...html.matchAll(/<(\/?)([a-zA-Z][\w-]*)([^>]*)>/g)].map((m) => ({
    name: m[2].toLowerCase(),
    closing: m[1] === "/",
    attrs: Object.fromEntries([...m[3].matchAll(/([\w-]+)="([^"]*)"/g)].map((a) => [a[1], a[2]])),
    raw: m[3],
  }));
}
const names = (html) => tags(html).filter((t) => !t.closing).map((t) => t.name);

const ALLOWED_TAGS = new Set(["p", "br", "strong", "em", "code", "ul", "ol", "li", "h3", "a"]);
const ALLOWED_A_ATTRS = new Set(["href", "target", "rel", "class", "title"]);
const SAFE_HREF = /^(https?:\/\/[^\s"<>]+|#card-(open-meteo|wikipedia))$/;

/** The security invariant: whatever the model wrote, the output only contains whitelisted markup. */
function assertSafe(html) {
  for (const t of tags(html)) {
    assert.ok(ALLOWED_TAGS.has(t.name), `unexpected <${t.name}> in: ${html}`);
    if (t.closing) continue;
    if (t.name !== "a") assert.equal(t.raw.trim(), "", `attributes on <${t.name}> in: ${html}`);
    for (const [k, v] of Object.entries(t.attrs)) {
      assert.ok(ALLOWED_A_ATTRS.has(k), `unexpected attribute ${k} in: ${html}`);
      if (k === "href") assert.match(v, SAFE_HREF);
    }
    if (t.name === "a") assert.deepEqual(Object.keys(t.attrs).filter((k) => /^on/i.test(k)), []);
  }
}

test("esc escapes the five HTML-significant characters and tolerates null", () => {
  assert.equal(esc(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(42), "42");
});

test("empty input renders nothing", () => {
  assert.equal(renderMarkdown(""), "");
  assert.equal(renderMarkdown("  \n \n"), "");
});

// ---- security

const hrefs = (html) => tags(html).filter((t) => t.name === "a" && !t.closing && !t.attrs.class).map((t) => t.attrs.href);

const HOSTILE = [
  `<img src=x onerror=alert(1)>`,
  `<script>alert(2)</script>`,
  `<svg onload=alert(3)>`,
  `<iframe src="javascript:alert(4)"></iframe>`,
  `[click](javascript:alert(5))`,
  `[click](JaVaScRiPt:alert(6))`,
  `[data](data:text/html;base64,PHNjcmlwdD4=)`,
  `[x](https://example.com/"onmouseover="alert(7))`,
  `[x](https://example.com/'onmouseover='alert(8))`,
  `[<b>bold</b>](https://example.com)`,
  `**<u>x</u>** and *<i>y</i>* and \`<s>z</s>\``,
  `</p><p onclick=alert(9)>`,
  `# <h1 onclick=x>heading</h1>`,
  `- <li onclick=x>item</li>`,
  `<a href="https://evil.test">raw anchor</a>`,
  "&lt;script&gt;already escaped&lt;/script&gt;",
  `[Open-Meteo](javascript:alert(10)) [Wikipedia]( javascript:alert(11))`,
];

test("hostile model output only ever produces whitelisted tags and attributes", () => {
  for (const payload of HOSTILE) {
    assertSafe(renderMarkdown(payload));
    assertSafe(renderMarkdown(`Intro\n\n- ${payload}\n\n1. ${payload}\n\n## ${payload}\n\n${payload}`));
  }
});

test("hostile text stays visible as text instead of disappearing or executing", () => {
  const html = renderMarkdown("<script>alert(2)</script>");
  assert.ok(html.includes("&lt;script&gt;alert(2)&lt;/script&gt;"));
  assert.deepEqual(names(html), ["p"]);
});

test("only http(s) links are made; other schemes stay literal text", () => {
  const ok = renderMarkdown("[site](https://example.com/a)");
  assert.deepEqual(tags(ok).find((t) => t.name === "a").attrs.href, "https://example.com/a");
  assert.equal(tags(ok).find((t) => t.name === "a").attrs.rel, "noopener noreferrer");
  assert.deepEqual(names(renderMarkdown("[x](javascript:alert(1))")), ["p"]);
  assert.deepEqual(names(renderMarkdown("[x](ftp://example.com)")), ["p"]);
});

test("a quote in a link URL cannot break out of the href attribute", () => {
  const html = renderMarkdown(`[x](https://example.com/"onmouseover="alert(1))`);
  const a = tags(html).find((t) => t.name === "a");
  assert.deepEqual(Object.keys(a.attrs).sort(), ["href", "rel", "target"]);
  assert.ok(a.attrs.href.includes("&quot;"));
});

// ---- Markdown features

test("paragraphs split on blank lines and single newlines become line breaks", () => {
  assert.equal(renderMarkdown("one\ntwo\n\nthree"), "<p>one<br>two</p><p>three</p>");
});

test("bold, italic and inline code", () => {
  assert.equal(renderMarkdown("**b** __b2__ *i* `c`"), "<p><strong>b</strong> <strong>b2</strong> <em>i</em> <code>c</code></p>");
});

test("code content is not formatted further, and a NUL in the input cannot fake a code span", () => {
  assert.equal(renderMarkdown("`a **b** [Wikipedia] c`"), "<p><code>a **b** [Wikipedia] c</code></p>");
  assert.equal(renderMarkdown("x\u00000\u0000y `real`"), "<p>x0y <code>real</code></p>");
});

test("ordered and bullet lists, including the bullet styles models use", () => {
  assert.equal(renderMarkdown("1. a\n2) b"), "<ol><li>a</li><li>b</li></ol>");
  for (const bullet of ["-", "*", "•"]) {
    assert.equal(renderMarkdown(`${bullet} a\n${bullet} b`), "<ul><li>a</li><li>b</li></ul>");
  }
});

test("a bold lead-in inside a list item is formatted", () => {
  assert.equal(renderMarkdown("1.  **Explore Parks:** Visit one"), "<ol><li><strong>Explore Parks:</strong> Visit one</li></ol>");
});

test("switching list type or going back to prose closes the list", () => {
  assert.deepEqual(names(renderMarkdown("- a\n1. b\ntext")), ["ul", "li", "ol", "li", "p"]);
});

test("headings of any level become one heading size", () => {
  for (const h of ["# T", "### T", "###### T"]) assert.equal(renderMarkdown(h), "<h3>T</h3>");
  assert.equal(renderMarkdown("####### seven"), "<p>####### seven</p>");
});

test("stray asterisks and underscores in ordinary text are left alone", () => {
  assert.equal(renderMarkdown("2*3*4 and snake_case_name"), "<p>2*3*4 and snake_case_name</p>");
  assert.equal(renderMarkdown("5 * 3 = 15"), "<p>5 * 3 = 15</p>");
});

test("windows line endings and trailing spaces do not leak into the output", () => {
  assert.equal(renderMarkdown("a  \r\nb"), "<p>a<br>b</p>");
});

// ---- citations

test("source tags become jump links to their cards", () => {
  const html = renderMarkdown("Warm [Open-Meteo]. Big city [Wikipedia].");
  const links = tags(html).filter((t) => t.name === "a" && !t.closing);
  assert.deepEqual(links.map((t) => t.attrs.href), ["#card-open-meteo", "#card-wikipedia"]);
  assert.ok(links.every((t) => t.attrs.class === "cite"));
  assert.ok(html.includes(">[Open-Meteo]</a>"));
});

test("unknown bracketed text is not a citation, and a real link keeps its own target", () => {
  assert.deepEqual(names(renderMarkdown("see [Other]")), ["p"]);
  const html = renderMarkdown("[Wikipedia](https://en.wikipedia.org/wiki/Lisbon)");
  assert.deepEqual(hrefs(html), ["https://en.wikipedia.org/wiki/Lisbon"]);
});

// ---- landmark links

const NEARBY = [
  { title: "Balboa Theatre (San Diego)", url: "https://en.wikipedia.org/wiki/Balboa_Theatre_(San_Diego)" },
  { title: "Pantoja Park", url: "https://en.wikipedia.org/wiki/Pantoja_Park" },
  { title: "Horton Plaza", url: "https://en.wikipedia.org/wiki/Horton_Plaza" },
  { title: "Horton Plaza Park", url: "https://en.wikipedia.org/wiki/Horton_Plaza_Park" },
];

test("landmark names link to their articles, ignoring a trailing parenthetical in the title", () => {
  const html = renderMarkdown("Visit the Balboa Theatre and Pantoja Park.", NEARBY);
  assert.deepEqual(hrefs(html), [NEARBY[0].url, NEARBY[1].url]);
  assertSafe(html);
});

test("the longest matching name wins", () => {
  assert.deepEqual(hrefs(renderMarkdown("Try Horton Plaza Park or Horton Plaza.", NEARBY)), [NEARBY[3].url, NEARBY[2].url]);
});

test("every occurrence is linked, but only whole words and exact case", () => {
  assert.equal(hrefs(renderMarkdown("Pantoja Park, then Pantoja Park again", NEARBY)).length, 2);
  assert.deepEqual(hrefs(renderMarkdown("Pantoja Parking and pantoja park", NEARBY)), []);
});

test("landmark names inside bold work, and ones inside an existing link are not linked twice", () => {
  assert.deepEqual(hrefs(renderMarkdown("**Pantoja Park** is nice", NEARBY)), [NEARBY[1].url]);
  const html = renderMarkdown("[Pantoja Park](https://example.com/x)", NEARBY);
  assert.deepEqual(hrefs(html), ["https://example.com/x"]);
});

test("only article URLs from Wikipedia are ever used, and very short names are skipped", () => {
  const evil = [{ title: "Evil Place", url: "javascript:alert(1)" }, { title: "Foo Bar", url: "https://evil.test/" },
                { title: "Zoo", url: "https://en.wikipedia.org/wiki/Zoo" }];
  assert.deepEqual(hrefs(renderMarkdown("Evil Place, Foo Bar and the Zoo", evil)), []);
});

test("names with apostrophes and regex characters match safely", () => {
  const places = [{ title: "St. Paul's (Cathedral)", url: "https://en.wikipedia.org/wiki/St._Paul's" },
                  { title: "A+B (Café) Hall", url: "https://en.wikipedia.org/wiki/AB" }];
  // The href is HTML-escaped inside the attribute (the browser decodes &#39; back to an apostrophe).
  assert.deepEqual(hrefs(renderMarkdown("Go to St. Paul's today", places)), ["https://en.wikipedia.org/wiki/St._Paul&#39;s"]);
  // Only a trailing parenthetical is stripped; "+" and "()" in the middle of a name match literally.
  assert.deepEqual(hrefs(renderMarkdown("Visit A+B (Café) Hall", places)), ["https://en.wikipedia.org/wiki/AB"]);
  assert.doesNotThrow(() => renderMarkdown("anything", [{ title: "((((", url: "https://en.wikipedia.org/x" }]));
});

test("no nearby places, or none supplied, leaves the text untouched", () => {
  assert.equal(renderMarkdown("Pantoja Park"), "<p>Pantoja Park</p>");
  assert.equal(renderMarkdown("Pantoja Park", []), "<p>Pantoja Park</p>");
  assert.equal(renderMarkdown("Pantoja Park", null), "<p>Pantoja Park</p>");
});

// ---- server-sent events

test("parseSse returns complete events and keeps the unfinished rest for the next chunk", () => {
  const first = 'event: briefing\ndata: {"city":"Lisbon"}\n\nevent: delta\ndata: {"text":"Ni';
  const a = parseSse(first);
  assert.deepEqual(a.events, [{ event: "briefing", data: { city: "Lisbon" } }]);
  const b = parseSse(a.rest + 'ce"}\n\n: ping\n\nevent: done\ndata: {"answer":"Nice"}\n\n');
  assert.deepEqual(b.events, [
    { event: "delta", data: { text: "Nice" } },
    { event: "done", data: { answer: "Nice" } },
  ]);
  assert.equal(b.rest, "");
});

test("parseSse copes with CRLF line endings, a split boundary, and garbled events", () => {
  assert.deepEqual(parseSse('event: delta\r\ndata: {"text":"a"}\r\n\r\n').events, [{ event: "delta", data: { text: "a" } }]);
  const split = parseSse('event: delta\ndata: {"text":"a"}\n');  // boundary not complete yet
  assert.deepEqual(split.events, []);
  assert.deepEqual(parseSse(split.rest + "\n").events, [{ event: "delta", data: { text: "a" } }]);
  assert.deepEqual(parseSse("event: delta\ndata: {oops\n\nevent: done\ndata: {}\n\n").events, [{ event: "done", data: {} }]);
});
