// What a page reads aloud: its text as blocks in reading order — {kind: "heading" | "para", text, el}.
// Wikipedia has its own rule (the article only: no citation marks, pronunciations, infobox, tables,
// navboxes or captions, and nothing from References / See also on); other sites get the main text
// (article / main / the element with the most paragraph text), without menus, sidebars, footers, captions,
// boxes of other stories or sign-ups, a short line asking to sign up, lines that are all one link, and headings
// left with nothing under them.
// A paragraph can be a <div> a site marks as one (Reuters); a figure's own text is read (the Guardian's timeline).
// A plain script (content script, and the tests' pages): sets globalThis.PageText.
(() => {
  // a Wikipedia section heading (h2: a section's own, not a sub-heading) that ends the article (English and Chinese)
  const STOP = /^(references|notes|explanatory notes|notes and references|citations|sources|bibliography|see also|external links|further reading|works cited|footnotes|参考文献|參考文獻|参考资料|參考資料|参见|參見|相关条目|相關條目|外部链接|外部連結|注释|註釋|注释与参考文献|脚注|延伸阅读|延伸閱讀)$/i;
  // dropped inside a block: citation marks, pronunciations and "listen", edit links
  const DROP = 'sup.reference, sup.mw-ref, .mw-ref, .noprint, .IPA, .rt-commentedText, .ext-phonos, .mw-editsection, ' +
    '.mw-cite-backlink, .sortkey, style, script, audio, video, button';
  // skipped whole: boxes, tables, pictures, notes about the article
  const SKIP = 'table, figure, .infobox, .navbox, .navbox-styles, .thumb, .hatnote, .shortdescription, .reflist, .references, ' +
    '.mw-references-wrap, .sidebar, .toc, #toc, .metadata, .ambox, .mbox-small, .side-box, .gallery, .catlinks, ' +
    '.mwe-math-element, .noprint, [role="note"], [hidden], [aria-hidden="true"], style, script, link, meta';
  const HEADING = /^H[1-6]$/;

  function clean(text) {
    return text
      .replace(/\[(?:\d+|[a-z]|[^\]]*?\bneeded\b[^\]]*|note \d+|nb \d+|\d+\s*[:–-]\s*\d+)\]/g, '')  // [12] [a] [citation needed]; an editor's [he] stays
      .replace(/\(\s*(?:listen|ⓘ)\s*\)/gi, '').replace(/ⓘ/g, '')
      .replace(/\b(?:UK|US|British|American|RP|GA|Received Pronunciation|General American)\s*:\s*(?=[;,)]|$)/g, '')  // a label whose pronunciation was dropped
      .replace(/\(\s*[;,:/]*\s*\)/g, '').replace(/（\s*[；，、：]*\s*）/g, '')  // brackets left empty by what was dropped
      .replace(/\(\s*(?:[;,]\s*)+/g, '(')                  // "(; ; 1801" -> "(1801"
      .replace(/(?:\s*[;,])+\s*\)/g, ')')                    // "1874; , )" -> "1874)"
      .replace(/\s+/g, ' ')
      .replace(/\s+([,.;:!?)，。；：！？）])/g, '$1').replace(/\(\s+/g, '(')
      .trim();
  }

  // drawn on the page: not display:none (itself or above), not visibility:hidden. A page that isn't
  // laid out (no checkVisibility) counts everything as shown.
  const shown = el => !el.checkVisibility || el.checkVisibility({ visibilityProperty: true, checkVisibilityCSS: true });  // the second: Chrome < 121

  // visibleOnly: leave out the parts of el you can't see (a hidden <span> inside a paragraph)
  function textOf(el, visibleOnly = false) {
    if (!visibleOnly) {
      const c = el.cloneNode(true);
      c.querySelectorAll(DROP).forEach(n => n.remove());
      return clean(c.textContent || '');
    }
    let text = '';
    const walk = node => {
      for (const k of node.childNodes) {
        if (k.nodeType === Node.TEXT_NODE) text += k.data;
        else if (k.nodeType === Node.ELEMENT_NODE && !k.matches(DROP) && shown(k)) {
          // a part laid out on a line of its own (a date above a title in one heading) keeps a space from its neighbours
          const st = getComputedStyle(k);  // not a floated drop cap ("T" + "he"): floats compute as block
          const own = !/^inline|^contents$/.test(st.display) && st.float === 'none';
          if (own) text += ' ';
          walk(k);
          if (own) text += ' ';
        }
      }
    };
    walk(el);
    return clean(text);
  }

  const block = (kind, el, text = textOf(el)) => (text ? [{ kind, text, el }] : []);

  function wikipedia(doc) {
    const root = doc.querySelector('#mw-content-text .mw-parser-output');
    if (!root) return null;
    const out = [], title = doc.querySelector('#firstHeading');
    if (title) out.push(...block('heading', title));
    let stopped = false;
    const walk = node => {
      for (const el of node.children) {
        if (stopped) return;
        if (el.matches(SKIP)) continue;
        const h = el.matches('.mw-heading') ? el.querySelector('h1, h2, h3, h4, h5, h6') : HEADING.test(el.tagName) ? el : null;
        if (h) {
          const text = textOf(h);
          if (h.tagName === 'H2' && STOP.test(text)) { stopped = true; return; }
          out.push(...block('heading', h, text));
        } else if (el.matches('p, blockquote')) out.push(...block('para', el));
        else if (el.matches('ul, ol, dl')) for (const li of el.querySelectorAll(':scope > li, :scope > dd, :scope > dt')) out.push(...block('para', li));
        else if (el.matches('section, div')) walk(el);
      }
    };
    walk(root);
    return out;
  }

  // not read: around the article, a picture's caption (a figure's own text is: a timeline can be one), hidden parts
  const AWAY = 'nav, aside, footer, header, form, figcaption, table, menu, dialog, [role="navigation"], [role="banner"], ' +
    '[role="contentinfo"], [role="complementary"], [role="dialog"], [role="alertdialog"], [aria-modal="true"], ' +
    '[aria-hidden="true"], [hidden], .sr-only, .visually-hidden';
  // a paragraph is a <p>, or a block a site marks as one (Reuters: <div data-testid="paragraph-3">)
  const PARA = 'p, [data-testid^="paragraph-"], [role="paragraph"]';
  const BLOCKS = `h1, h2, h3, h4, ${PARA}, li, blockquote, dd, pre`;
  // boxes beside the article that sites put inside it: other stories, sign-ups, the writers' biographies
  const EXTRA = /author[-_]?bio|newsletter|most[-_]?(?:popular|viewed|read)|read[-_]?next|recirc|related[-_]?(?:stories|articles|content|links)|outbrain|taboola/i;
  // only a box holding a small share of the main text: a wrapper named "…newsletter…" around the whole story is not one
  const extra = (el, root) => {
    for (let e = el; e && e !== root; e = e.parentElement) {
      if (['class', 'id', 'data-testid', 'data-component', 'data-gu-name'].some(name => EXTRA.test(e.getAttribute(name) || ''))
          && e.textContent.length < root.textContent.length * 0.4) return true;
    }
    return false;
  };
  // a picture's figure (a picture and no heading): its text is a caption or a credit, however it is marked up;
  // a figure with headings (a timeline) is read
  const caption = el => {
    const f = el.closest('figure');
    return !!f && !f.querySelector('h1, h2, h3, h4, h5, h6') && !!f.querySelector('img, picture, video, iframe, svg, canvas');
  };
  // a block that is (almost) all one link — a list of other stories ("Most viewed", "Read Next"); never the title
  function linkOnly(el, text) {
    if (el.tagName === 'H1') return false;
    if (el.closest('a')) return true;
    let linked = 0;
    for (const a of el.querySelectorAll('a')) linked += textOf(a, true).length;
    return text.length > 0 && linked / text.length >= 0.8;
  }
  // a sign-up line inside the story ("… newsletter. Sign up here."): short, with a link that only says to sign up
  const SIGN_UP = /^(?:sign[ -]?up|subscribe|register)(?: here| now| today| for free)?[.!]?$/i;
  const signUp = (el, text) => text.length < 220 && [...el.querySelectorAll('a')].some(a => SIGN_UP.test(textOf(a, true)));
  // a heading with nothing read under it before the next heading of its level or above ("Most viewed" once its links are gone)
  function dropEmptyHeadings(out) {
    const level = b => Number(b.el.tagName[1]) || 9;
    return out.filter((b, i) => {
      if (b.kind !== 'heading') return true;
      for (let j = i + 1; j < out.length; j++) {
        if (out[j].kind === 'para') return true;
        if (level(out[j]) <= level(b)) return false;
      }
      return false;
    });
  }

  function mainOf(doc) {
    const fixed = doc.querySelector('article, [role="main"], main');
    if (fixed && fixed.textContent.trim().length > 200) return fixed;
    let best = doc.body, most = 0;  // the element whose own paragraphs hold the most text
    const score = new Map();
    for (const p of doc.querySelectorAll(PARA)) {
      const parent = p.parentElement;
      if (parent) score.set(parent, (score.get(parent) || 0) + p.textContent.trim().length);
    }
    for (const [el, n] of score) if (n > most) { most = n; best = el; }
    return best;
  }

  // other sites: only what you can see (Wikipedia's own rule above reads collapsed mobile sections too)
  function generic(doc) {
    const root = mainOf(doc), out = [];
    for (const el of root.querySelectorAll(BLOCKS)) {
      const away = el.closest(AWAY);
      // the story's own title may sit in the story's header (Reuters); a site's header is outside the main text
      const title = el.tagName === 'H1' && away && away.matches('header:not([role="banner"])') && root !== doc.body && root.contains(away);
      if (away && !root.closest(AWAY) && !title) continue;
      if (el.querySelector(BLOCKS)) continue;   // the innermost block only: no text twice
      if (!shown(el)) continue;                 // display:none, visibility:hidden — here or above
      if (extra(el, root)) continue;            // other stories, sign-ups, biographies
      if (caption(el)) continue;                // a picture's caption or credit
      const kind = HEADING.test(el.tagName) ? 'heading' : 'para';
      const text = textOf(el, true);
      if (kind === 'para' && text.length < 25 && !/[.!?。！？]$/.test(text)) continue;  // buttons, captions, "Share"
      if (linkOnly(el, text) || (kind === 'para' && signUp(el, text))) continue;
      out.push(...block(kind, el, text));
    }
    return dropEmptyHeadings(out);
  }

  const isWikipedia = loc => /(^|\.)wikipedia\.org$/.test(loc.hostname);

  globalThis.PageText = {
    clean,
    // the blocks of this page; from: start at the block holding that element (▶ on a paragraph)
    parts(doc, loc, from) {
      const all = (isWikipedia(loc) && wikipedia(doc)) || generic(doc);
      if (!from) return all;
      const i = all.findIndex(b => b.el === from || b.el.contains(from));
      return i < 0 ? [] : all.slice(i);  // no longer on the page: nothing, never the top
    },
    isWikipedia,
    BLOCKS,  // what counts as a block on other sites (reader.js finds the one under the pointer with it)
  };
})();
