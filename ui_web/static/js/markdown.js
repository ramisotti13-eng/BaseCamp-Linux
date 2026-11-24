// A small Markdown renderer for the version history (CHANGELOG.md and the
// release notes of an update). It builds DOM nodes and never sets innerHTML:
// release notes come from the network. HTML in the text is left out (the
// release pages carry <img> and <table> for GitHub), and links open in the
// browser through `onLink`, http(s) only.
import { h } from "./ui.js";

const INLINE = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))/g;

/** Text with **bold**, `code` and [links](https://...) as nodes. */
function inline(text, onLink) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(h("strong", null, inline(m[2], onLink)));     // `code` inside bold
    else if (m[3]) out.push(h("code", null, m[4]));
    else {
      const url = m[7];
      out.push(h("a", { href: url, onclick: (e) => { e.preventDefault(); if (onLink) onLink(url); } }, m[6]));
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const stripHtml = (s) => s.replace(/<[^>]*>/g, "");

/** A <div class="md"> for the Markdown text `md`. */
export function renderMarkdown(md, { onLink } = {}) {
  const root = h("div", { class: "md" });
  const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n");
  let para = [];
  let lists = [];          // the open <ul>s with their indent: [{ul, indent}]
  let code = null;         // the lines of an open ``` block
  const flushPara = () => { if (para.length) { root.append(h("p", null, inline(para.join(" "), onLink))); para = []; } };
  const closeLists = () => { lists = []; };
  for (const raw of lines) {
    if (code) {
      if (/^\s*```/.test(raw)) { root.append(h("pre", null, code.join("\n"))); code = null; }
      else code.push(raw);
      continue;
    }
    if (/^\s*```/.test(raw)) { flushPara(); closeLists(); code = []; continue; }
    const line = stripHtml(raw);
    if (!line.trim()) {
      // A line that held only HTML is not a paragraph break inside a list item.
      if (raw.trim()) continue;
      flushPara(); closeLists(); continue;
    }
    const head = line.match(/^(#{1,6})\s+(.*)$/);
    if (head) {
      flushPara(); closeLists();
      root.append(h("div", { class: "md-h md-h" + Math.min(head[1].length, 4) }, inline(head[2], onLink)));
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { flushPara(); closeLists(); continue; }
    if (/^\s*\|/.test(line)) { flushPara(); closeLists(); root.append(h("pre", { class: "md-table" }, line.trim())); continue; }
    const item = line.match(/^(\s*)[-*]\s+(.*)$/);
    if (item) {
      flushPara();
      const indent = item[1].length;
      while (lists.length && lists[lists.length - 1].indent > indent) lists.pop();
      if (!lists.length || lists[lists.length - 1].indent < indent) {
        const ul = h("ul");
        const parentLi = lists.length ? lists[lists.length - 1].ul.lastElementChild : null;
        (parentLi || root).append(ul);
        lists.push({ ul, indent });
      }
      lists[lists.length - 1].ul.append(h("li", null, inline(item[2], onLink)));
      continue;
    }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) { flushPara(); closeLists(); root.append(h("p", { class: "md-quote" }, inline(quote[1], onLink))); continue; }
    if (lists.length && /^\s+\S/.test(line)) {
      // A continuation line of the last list item.
      const li = lists[lists.length - 1].ul.lastElementChild;
      li.append(" ", ...inline(line.trim(), onLink));
      continue;
    }
    closeLists();
    para.push(line.trim());
  }
  if (code) root.append(h("pre", null, code.join("\n")));
  flushPara();
  return root;
}
