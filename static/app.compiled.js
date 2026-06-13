function _extends() { _extends = Object.assign ? Object.assign.bind() : function (target) { for (var i = 1; i < arguments.length; i++) { var source = arguments[i]; for (var key in source) { if (Object.prototype.hasOwnProperty.call(source, key)) { target[key] = source[key]; } } } return target; }; return _extends.apply(this, arguments); }
const {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  useContext,
  createContext,
  useDeferredValue
} = React;

// ═══════════════════════════════════════════════
// SVG Icons (inline, no external deps needed)
// ═══════════════════════════════════════════════
const Icons = {
  dashboard: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "3",
    y: "3",
    width: "7",
    height: "7",
    rx: "1"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "14",
    y: "3",
    width: "7",
    height: "7",
    rx: "1"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "3",
    y: "14",
    width: "7",
    height: "7",
    rx: "1"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "14",
    y: "14",
    width: "7",
    height: "7",
    rx: "1"
  })),
  messages: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"
  })),
  nodes: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M12 2v4m0 12v4M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"
  })),
  topology: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "6",
    cy: "6",
    r: "3"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "18",
    cy: "6",
    r: "3"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "6",
    cy: "18",
    r: "3"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "18",
    cy: "18",
    r: "3"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8.5",
    y1: "7.5",
    x2: "15.5",
    y2: "16.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8.5",
    y1: "16.5",
    x2: "15.5",
    y2: "7.5"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "6",
    y1: "9",
    x2: "6",
    y2: "15"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "18",
    y1: "9",
    x2: "18",
    y2: "15"
  })),
  map: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "10",
    r: "3"
  })),
  power: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M13 2L3 14h9l-1 8 10-12h-9l1-8z"
  })),
  alerts: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M13.73 21a2 2 0 01-3.46 0"
  })),
  send: /*#__PURE__*/React.createElement("svg", {
    width: "18",
    height: "18",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("line", {
    x1: "22",
    y1: "2",
    x2: "11",
    y2: "13"
  }), /*#__PURE__*/React.createElement("polygon", {
    points: "22 2 15 22 11 13 2 9 22 2"
  })),
  check: /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "20 6 9 17 4 12"
  })),
  ai: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M12 2a4 4 0 014 4v1h1a3 3 0 010 6h-1v1a4 4 0 01-8 0v-1H7a3 3 0 010-6h1V6a4 4 0 014-4z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "9",
    cy: "10",
    r: "1",
    fill: "currentColor"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "15",
    cy: "10",
    r: "1",
    fill: "currentColor"
  })),
  system: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "2",
    y: "3",
    width: "20",
    height: "14",
    rx: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8 21h8M12 17v4"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M6 8h.01M10 8h4"
  })),
  settings: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"
  })),
  navigate: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("polygon", {
    points: "3 11 22 2 13 21 11 13 3 11"
  })),
  scheduler: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "3",
    y: "4",
    width: "18",
    height: "18",
    rx: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "16",
    y1: "2",
    x2: "16",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "2",
    x2: "8",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "3",
    y1: "10",
    x2: "21",
    y2: "10"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "16",
    r: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "14",
    x2: "12",
    y2: "12"
  })),
  calendar: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "3",
    y: "4",
    width: "18",
    height: "18",
    rx: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "16",
    y1: "2",
    x2: "16",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "2",
    x2: "8",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "3",
    y1: "10",
    x2: "21",
    y2: "10"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"
  })),
  calculator: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "4",
    y: "2",
    width: "16",
    height: "20",
    rx: "2"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "6",
    x2: "16",
    y2: "6"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "10",
    x2: "10",
    y2: "10"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "14",
    y1: "10",
    x2: "16",
    y2: "10"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "14",
    x2: "10",
    y2: "14"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "14",
    y1: "14",
    x2: "16",
    y2: "14"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "8",
    y1: "18",
    x2: "10",
    y2: "18"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "14",
    y1: "18",
    x2: "16",
    y2: "18"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "10",
    x2: "12",
    y2: "10"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "14",
    x2: "12",
    y2: "14"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "18",
    x2: "12",
    y2: "18"
  })),
  wifi: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M5 12.55a11 11 0 0114.08 0"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M1.42 9a16 16 0 0121.16 0"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8.53 16.11a6 6 0 016.95 0"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "20",
    r: "1",
    fill: "currentColor"
  })),
  mountain: /*#__PURE__*/React.createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M3 20l6.5-11 3.5 6 2-3 6 8H3z"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M9.5 9l1.4-2.5L13 10"
  }))
};

// ═══════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════
// Escape a string for safe interpolation into an HTML context.
// Prevents XSS when node names, aliases, or device IDs come from untrusted sources.
function escHtml(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function timeAgo(ts) {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// Resolve a settings value into an Intl.DateTimeFormat timeZone option.
// "auto" / unset / invalid → undefined (browser picks local tz).
// IANA tz names like "America/New_York" handle DST automatically because
// the Intl tzdata is what V8/JavaScriptCore use under the hood.
function resolveTz(tz) {
  if (!tz || tz === 'auto') return undefined;
  return tz;
}

// ═══════════════════════════════════════════════
// Markdown rendering for AI (Ray) replies
// ═══════════════════════════════════════════════
// Renders a safe subset of Markdown as real React nodes — never raw HTML, so
// model output can never inject markup (no dangerouslySetInnerHTML). Supports
// headings, **bold**, *italics*, `inline code`, fenced code blocks, tables,
// ordered/unordered (nested) lists, blockquotes, horizontal rules and links.
// Tolerant of partial input so a reply renders cleanly mid-stream (e.g. an
// unclosed code fence or a half-built table).

// Inline span parser: code / bold / italic / strikethrough / links.
// Single-pass scanner (no regex) so identifiers like node_id aren't mangled
// and so it degrades gracefully on unterminated markers.
function mdInline(text, kp) {
  const out = [];
  let buf = '';
  let i = 0;
  const flush = () => {
    if (buf) {
      out.push(buf);
      buf = '';
    }
  };
  while (i < text.length) {
    const c = text[i];
    if (c === '`') {
      // `inline code`
      const end = text.indexOf('`', i + 1);
      if (end > i) {
        flush();
        out.push( /*#__PURE__*/React.createElement("code", {
          key: kp + 'c' + i,
          style: {
            fontFamily: 'var(--font-mono)',
            fontSize: '0.86em',
            background: 'var(--accent-green-dim)',
            color: 'var(--accent-green)',
            padding: '1px 5px',
            borderRadius: 4,
            wordBreak: 'break-word'
          }
        }, text.slice(i + 1, end)));
        i = end + 1;
        continue;
      }
    }
    if (c === '*' && text[i + 1] === '*') {
      // **bold**
      const end = text.indexOf('**', i + 2);
      if (end > i + 1) {
        flush();
        out.push( /*#__PURE__*/React.createElement("strong", {
          key: kp + 'b' + i
        }, mdInline(text.slice(i + 2, end), kp + 'b' + i)));
        i = end + 2;
        continue;
      }
    }
    if (c === '*') {
      // *italic*
      const end = text.indexOf('*', i + 1);
      if (end > i + 1) {
        flush();
        out.push( /*#__PURE__*/React.createElement("em", {
          key: kp + 'i' + i
        }, mdInline(text.slice(i + 1, end), kp + 'i' + i)));
        i = end + 1;
        continue;
      }
    }
    if (c === '~' && text[i + 1] === '~') {
      // ~~strikethrough~~
      const end = text.indexOf('~~', i + 2);
      if (end > i + 1) {
        flush();
        out.push( /*#__PURE__*/React.createElement("span", {
          key: kp + 's' + i,
          style: {
            textDecoration: 'line-through',
            opacity: 0.7
          }
        }, mdInline(text.slice(i + 2, end), kp + 's' + i)));
        i = end + 2;
        continue;
      }
    }
    if (c === '[') {
      // [label](url)
      const close = text.indexOf(']', i + 1);
      if (close > i && text[close + 1] === '(') {
        const paren = text.indexOf(')', close + 2);
        if (paren > close) {
          flush();
          out.push( /*#__PURE__*/React.createElement("a", {
            key: kp + 'l' + i,
            href: text.slice(close + 2, paren).trim(),
            target: "_blank",
            rel: "noopener noreferrer",
            style: {
              color: 'var(--accent-green)',
              textDecoration: 'underline'
            }
          }, text.slice(i + 1, close)));
          i = paren + 1;
          continue;
        }
      }
    }
    buf += c;
    i++;
  }
  flush();
  return out;
}

// Split a table row into trimmed cells, tolerating optional leading/trailing pipes.
function mdSplitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map(c => c.trim());
}

// True when a line is a GFM table separator row, e.g. `|---|:--:|---:|`.
function mdIsTableSep(s) {
  if (!s || s.indexOf('-') === -1 || s.indexOf('|') === -1) return false;
  return mdSplitRow(s).every(c => /^:?-{1,}:?$/.test(c));
}

// True when a line begins a non-paragraph block (used to terminate paragraphs).
function mdIsBlockStart(line) {
  return /^\s*```/.test(line) || /^\s*#{1,6}\s/.test(line) || /^\s*([-*_])\1{2,}\s*$/.test(line) || /^\s*>\s?/.test(line) || /^\s*([-*+]|\d+[.)])\s+/.test(line);
}
const MD_HEAD = {
  1: 19,
  2: 17,
  3: 15.5,
  4: 14.5,
  5: 13.5,
  6: 13
};

// Block-level Markdown renderer. Returns a fragment of React block nodes.
function MarkdownMessage({
  text
}) {
  const lines = (text || '').split('\n');
  const blocks = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];

    // ── fenced code block ──
    const fence = line.match(/^\s*```(.*)$/);
    if (fence) {
      i++;
      const code = [];
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // consume closing fence (if present)
      blocks.push( /*#__PURE__*/React.createElement("pre", {
        key: k++,
        style: {
          margin: '8px 0',
          padding: '10px 12px',
          background: 'rgba(0,0,0,0.28)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          overflowX: 'auto',
          fontFamily: 'var(--font-mono)',
          fontSize: 12.5,
          lineHeight: 1.5,
          color: 'var(--text-primary)',
          whiteSpace: 'pre'
        }
      }, /*#__PURE__*/React.createElement("code", null, code.join('\n'))));
      continue;
    }

    // ── blank line ──
    if (/^\s*$/.test(line)) {
      i++;
      continue;
    }

    // ── heading ──
    const h = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      blocks.push( /*#__PURE__*/React.createElement("div", {
        key: k++,
        style: {
          fontSize: MD_HEAD[lvl],
          fontWeight: 700,
          lineHeight: 1.3,
          margin: (blocks.length ? 12 : 2) + 'px 0 5px',
          color: 'var(--text-primary)'
        }
      }, mdInline(h[2].trim(), 'h' + k)));
      i++;
      continue;
    }

    // ── horizontal rule ──
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push( /*#__PURE__*/React.createElement("hr", {
        key: k++,
        style: {
          border: 0,
          borderTop: '1px solid var(--border)',
          margin: '12px 0'
        }
      }));
      i++;
      continue;
    }

    // ── table ──
    if (line.indexOf('|') !== -1 && i + 1 < lines.length && mdIsTableSep(lines[i + 1])) {
      const header = mdSplitRow(line);
      const aligns = mdSplitRow(lines[i + 1]).map(c => {
        const l = c.startsWith(':'),
          r = c.endsWith(':');
        return l && r ? 'center' : r ? 'right' : 'left';
      });
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].indexOf('|') !== -1 && !/^\s*$/.test(lines[i]) && !/^\s*```/.test(lines[i])) {
        rows.push(mdSplitRow(lines[i]));
        i++;
      }
      blocks.push( /*#__PURE__*/React.createElement("div", {
        key: k++,
        style: {
          overflowX: 'auto',
          margin: '8px 0'
        }
      }, /*#__PURE__*/React.createElement("table", {
        style: {
          borderCollapse: 'collapse',
          width: '100%',
          fontSize: 13
        }
      }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, header.map((c, ci) => /*#__PURE__*/React.createElement("th", {
        key: ci,
        style: {
          border: '1px solid var(--border)',
          padding: '6px 10px',
          textAlign: aligns[ci] || 'left',
          background: 'var(--accent-green-dim)',
          fontWeight: 700,
          whiteSpace: 'nowrap'
        }
      }, mdInline(c, 'th' + k + '_' + ci))))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, ri) => /*#__PURE__*/React.createElement("tr", {
        key: ri
      }, header.map((_, ci) => /*#__PURE__*/React.createElement("td", {
        key: ci,
        style: {
          border: '1px solid var(--border)',
          padding: '6px 10px',
          textAlign: aligns[ci] || 'left',
          verticalAlign: 'top'
        }
      }, mdInline(r[ci] || '', 't' + k + '_' + ri + '_' + ci)))))))));
      continue;
    }

    // ── blockquote ──
    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      blocks.push( /*#__PURE__*/React.createElement("blockquote", {
        key: k++,
        style: {
          margin: '8px 0',
          padding: '4px 12px',
          borderLeft: '3px solid var(--accent-green)',
          color: 'var(--text-muted)',
          fontStyle: 'italic'
        }
      }, quote.map((q, qi) => /*#__PURE__*/React.createElement("div", {
        key: qi
      }, mdInline(q, 'q' + k + '_' + qi)))));
      continue;
    }

    // ── list (ordered/unordered, indent-based nesting) ──
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      // Gather consecutive items of the same kind; a marker switch (− ↔ 1.) starts a new list.
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && /^\s*\d+[.)]\s+/.test(lines[i]) === ordered) {
        const m = lines[i].match(/^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$/);
        const indent = Math.floor(m[1].replace(/\t/g, '  ').length / 2);
        items.push({
          indent,
          text: m[2]
        });
        i++;
      }
      const Tag = ordered ? 'ol' : 'ul';
      blocks.push( /*#__PURE__*/React.createElement(Tag, {
        key: k++,
        style: {
          margin: '6px 0',
          paddingLeft: 22,
          display: 'flex',
          flexDirection: 'column',
          gap: 3
        }
      }, items.map((it, ii) => /*#__PURE__*/React.createElement("li", {
        key: ii,
        style: {
          marginLeft: it.indent * 18,
          lineHeight: 1.55
        }
      }, mdInline(it.text, 'li' + k + '_' + ii)))));
      continue;
    }

    // ── paragraph (soft line breaks preserved) ──
    const para = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !mdIsBlockStart(lines[i]) && !(lines[i].indexOf('|') !== -1 && i + 1 < lines.length && mdIsTableSep(lines[i + 1]))) {
      para.push(lines[i]);
      i++;
    }
    blocks.push( /*#__PURE__*/React.createElement("div", {
      key: k++,
      style: {
        margin: (blocks.length ? '6px' : '0') + ' 0 0',
        lineHeight: 1.65
      }
    }, para.map((p, pi) => /*#__PURE__*/React.createElement(React.Fragment, {
      key: pi
    }, pi > 0 && /*#__PURE__*/React.createElement("br", null), mdInline(p, 'p' + k + '_' + pi)))));
  }
  return /*#__PURE__*/React.createElement(React.Fragment, null, blocks);
}
function fmtTime(ts, tz) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: resolveTz(tz)
  });
}
function fmtTimestamp(ts, dateFormat, tz) {
  if (!ts) return '—';
  return dateFormat === 'absolute' ? fmtTime(ts, tz) : timeAgo(ts);
}

// Curated list of IANA timezones that show up in the Settings selector.
// All IANA names follow DST rules automatically — no per-zone DST logic
// needed in the UI.
const TIMEZONE_OPTIONS = [['auto', 'Auto (browser)'], ['UTC', 'UTC'], ['Pacific/Honolulu', 'Hawaii (HST, no DST)'], ['America/Anchorage', 'Alaska (AKST/AKDT)'], ['America/Los_Angeles', 'Pacific (PST/PDT)'], ['America/Phoenix', 'Arizona (MST, no DST)'], ['America/Denver', 'Mountain (MST/MDT)'], ['America/Chicago', 'Central (CST/CDT)'], ['America/New_York', 'Eastern (EST/EDT)'], ['America/Halifax', 'Atlantic (AST/ADT)'], ['America/St_Johns', 'Newfoundland (NST/NDT)'], ['America/Sao_Paulo', 'São Paulo (BRT)'], ['Europe/London', 'London (GMT/BST)'], ['Europe/Paris', 'Paris / Berlin / Madrid (CET/CEST)'], ['Europe/Helsinki', 'Helsinki / Athens (EET/EEST)'], ['Europe/Moscow', 'Moscow (MSK)'], ['Africa/Johannesburg', 'Johannesburg (SAST)'], ['Asia/Dubai', 'Dubai (GST)'], ['Asia/Kolkata', 'India (IST)'], ['Asia/Bangkok', 'Bangkok (ICT)'], ['Asia/Singapore', 'Singapore (SGT)'], ['Asia/Shanghai', 'China (CST)'], ['Asia/Tokyo', 'Tokyo (JST)'], ['Asia/Seoul', 'Seoul (KST)'], ['Australia/Perth', 'Perth (AWST)'], ['Australia/Adelaide', 'Adelaide (ACST/ACDT)'], ['Australia/Sydney', 'Sydney (AEST/AEDT)'], ['Pacific/Auckland', 'Auckland (NZST/NZDT)']];
function snrColor(snr) {
  if (snr == null) return "var(--text-muted)";
  if (snr >= 10) return "var(--accent-green)";
  if (snr >= 5) return "var(--accent-blue)";
  if (snr >= 0) return "var(--accent-amber)";
  return "var(--accent-red)";
}
function batColor(level) {
  if (level == null) return "var(--text-muted)";
  if (level > 60) return "var(--accent-green)";
  if (level > 30) return "var(--accent-amber)";
  return "var(--accent-red)";
}
function signalQuality(snr, rssi) {
  if (snr == null && rssi == null) return {
    label: "Unknown",
    cls: ""
  };
  const s = snr || 0;
  if (s >= 10) return {
    label: "Excellent",
    cls: "tag-green"
  };
  if (s >= 5) return {
    label: "Good",
    cls: "tag-blue"
  };
  if (s >= 0) return {
    label: "Fair",
    cls: "tag-amber"
  };
  return {
    label: "Poor",
    cls: "tag-red"
  };
}

// Node health score: 0–100 composite of battery, signal, recency, channel load, uptime
function nodeHealthScore(n) {
  const now = Math.floor(Date.now() / 1000);
  let score = 0;

  // Battery (0–30 pts)
  if (n.battery_level != null) {
    score += Math.round(n.battery_level * 0.30);
  } else {
    score += 15; // unknown — assume mid
  }

  // SNR (0–25 pts)
  const snr = n.snr;
  if (snr != null) {
    if (snr >= 10) score += 25;else if (snr >= 5) score += 18;else if (snr >= 0) score += 10;else score += 2;
  } else {
    score += 12; // unknown — assume mid
  }

  // Last heard recency (0–25 pts)
  if (n.last_heard) {
    const age = now - n.last_heard;
    if (age < 300) score += 25; // <5 min
    else if (age < 900) score += 20; // <15 min
    else if (age < 1800) score += 12; // <30 min
    else if (age < 3600) score += 5; // <1 hr
  }

  // Channel utilisation (0–10 pts) — lower is healthier
  if (n.channel_util != null) {
    if (n.channel_util < 10) score += 10;else if (n.channel_util < 20) score += 7;else if (n.channel_util < 40) score += 4;
  } else {
    score += 5;
  }

  // Uptime stability (0–10 pts)
  if (n.uptime != null) {
    if (n.uptime > 86400) score += 10; // >1 day
    else if (n.uptime > 43200) score += 7; // >12 hr
    else if (n.uptime > 3600) score += 4; // >1 hr
    else score += 1;
  } else {
    score += 5;
  }
  return Math.min(100, Math.max(0, score));
}
function healthColor(score) {
  if (score >= 80) return 'var(--accent-green)';
  if (score >= 60) return 'var(--accent-blue)';
  if (score >= 40) return 'var(--accent-amber)';
  return 'var(--accent-red)';
}
function HealthBadge({
  node,
  size = 'normal'
}) {
  const score = nodeHealthScore(node);
  const color = healthColor(score);
  const r = size === 'large' ? 22 : 14;
  const stroke = size === 'large' ? 4 : 3;
  const circ = 2 * Math.PI * r;
  const dash = score / 100 * circ;
  const fontSize = size === 'large' ? 14 : 10;
  return /*#__PURE__*/React.createElement("span", {
    title: `Health: ${score}/100`,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      cursor: 'default'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: r * 2 + stroke * 2,
    height: r * 2 + stroke * 2,
    viewBox: `0 0 ${r * 2 + stroke * 2} ${r * 2 + stroke * 2}`,
    style: {
      transform: 'rotate(-90deg)'
    }
  }, /*#__PURE__*/React.createElement("circle", {
    cx: r + stroke,
    cy: r + stroke,
    r: r,
    fill: "none",
    stroke: "var(--bg-input)",
    strokeWidth: stroke
  }), /*#__PURE__*/React.createElement("circle", {
    cx: r + stroke,
    cy: r + stroke,
    r: r,
    fill: "none",
    stroke: color,
    strokeWidth: stroke,
    strokeDasharray: `${dash} ${circ}`,
    strokeLinecap: "round",
    style: {
      transition: 'stroke-dasharray 0.5s ease'
    }
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize,
      fontFamily: 'var(--font-mono)',
      color,
      fontWeight: 600
    }
  }, score));
}

// ═══════════════════════════════════════════════
// API helpers
// ═══════════════════════════════════════════════
const DEFAULT_API_TIMEOUT_MS = 8000;
function apiTimeoutFor(url, method = 'GET', explicitMs) {
  if (Number.isFinite(explicitMs) && explicitMs > 0) return explicitMs;
  const path = String(url || '');
  if (path === '/api/wifi/connect') return 45000;
  if (path === '/api/hotspot/start') return 30000;
  if (path.startsWith('/api/device/config/')) return 15000;
  if (path === '/api/device/owner') return 15000;
  if (path === '/api/factory-reset') return 20000;
  if (path === '/api/factory-defaults/capture') return 20000;
  return DEFAULT_API_TIMEOUT_MS;
}
async function apiRequest(url, {
  method = 'GET',
  data,
  timeoutMs,
  headers = {}
} = {}) {
  const controller = new AbortController();
  const requestTimeoutMs = apiTimeoutFor(url, method, timeoutMs);
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
  try {
    var _body;
    const init = {
      method,
      headers: {
        ...headers
      },
      signal: controller.signal
    };
    if (data !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(data);
    }
    const r = await fetch(url, init);
    let body = null;
    try {
      body = await r.json();
    } catch {}
    if (!r.ok) throw new Error(((_body = body) === null || _body === void 0 ? void 0 : _body.error) || `Request failed: ${r.status}`);
    return body;
  } catch (err) {
    if (err && err.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(requestTimeoutMs / 1000)}s`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}
const api = {
  get: (url, opts) => apiRequest(url, {
    ...opts,
    method: 'GET'
  }),
  post: (url, data, opts) => apiRequest(url, {
    ...opts,
    method: 'POST',
    data
  }),
  put: (url, data, opts) => apiRequest(url, {
    ...opts,
    method: 'PUT',
    data
  }),
  patch: (url, data, opts) => apiRequest(url, {
    ...opts,
    method: 'PATCH',
    data
  }),
  del: (url, opts) => apiRequest(url, {
    ...opts,
    method: 'DELETE'
  })
};

// ═══════════════════════════════════════════════
// Settings Context
// ═══════════════════════════════════════════════
const SettingsContext = createContext({
  units: 'metric',
  date_format: 'relative',
  timezone: 'auto',
  show_offline_nodes: 'true',
  node_online_window_h: '2',
  dashboard_refresh_s: '5',
  map_default_zoom: '13',
  battery_alert_pct: '20',
  node_offline_min: '30',
  serial_port: '/dev/ttyACM0'
});
function useSettings() {
  return useContext(SettingsContext);
}
function validateSettings(s) {
  const errors = {};
  const webPort = parseInt(s.web_port);
  if (isNaN(webPort) || webPort < 1024 || webPort > 65535) errors.web_port = 'Must be 1024–65535';
  const refresh = parseInt(s.dashboard_refresh_s);
  if (isNaN(refresh) || refresh < 1 || refresh > 60) errors.dashboard_refresh_s = 'Must be 1–60 seconds';
  const battery = parseInt(s.battery_alert_pct);
  if (isNaN(battery) || battery < 5 || battery > 50) errors.battery_alert_pct = 'Must be 5–50%';
  const offlineMin = parseInt(s.node_offline_min);
  if (isNaN(offlineMin) || offlineMin < 5 || offlineMin > 1440) errors.node_offline_min = 'Must be 5–1440 minutes';
  const zoom = parseInt(s.map_default_zoom);
  if (isNaN(zoom) || zoom < 1 || zoom > 19) errors.map_default_zoom = 'Must be 1–19';
  const onlineWin = parseInt(s.node_online_window_h);
  if (isNaN(onlineWin) || onlineWin < 1 || onlineWin > 24) errors.node_online_window_h = 'Must be 1–24 hours';
  return errors;
}

// Helper: is a node currently online?
// Own node is "online" when the device is connected (it has no last_heard).
// Other nodes are online if heard within the node_online_window_h setting.
function nodeOnline(n, myNodeId, deviceConnected, onlineWindowH) {
  if (myNodeId && n.node_id === myNodeId) return !!deviceConnected;
  const windowSec = (parseFloat(onlineWindowH) || 2) * 3600;
  return !!(n.last_heard && Date.now() / 1000 - n.last_heard < windowSec);
}

// ═══════════════════════════════════════════════
// Dashboard Page
// ═══════════════════════════════════════════════
const DEFAULT_DASH_WIDGETS = [{
  id: 'stat_total_nodes',
  label: 'Total Nodes',
  enabled: true
}, {
  id: 'stat_total_messages',
  label: 'Messages',
  enabled: true
}, {
  id: 'stat_atlas_status',
  label: 'Atlas + GPS Status',
  enabled: true
}, {
  id: 'stat_atlas_battery',
  label: 'Atlas Battery',
  enabled: true
}, {
  id: 'stat_active_alerts',
  label: 'Alert Count',
  enabled: true
}, {
  id: 'stat_channel_util',
  label: 'Channel Utilization',
  enabled: true
}, {
  id: 'stat_avg_snr',
  label: 'Average SNR',
  enabled: true
}, {
  id: 'stat_mesh_avg_battery',
  label: 'Mesh Avg Battery',
  enabled: true
}, {
  id: 'network_health',
  label: 'Network Health',
  enabled: true
}, {
  id: 'phone_trackers',
  label: 'Phone Trackers',
  enabled: true
}, {
  id: 'battery_fleet',
  label: 'Battery Fleet Status',
  enabled: true
}, {
  id: 'signal_health',
  label: 'Signal Distribution',
  enabled: true
}, {
  id: 'node_roles',
  label: 'Node Roles',
  enabled: true
}, {
  id: 'online_nodes',
  label: 'Online Nodes',
  enabled: true
}, {
  id: 'recent_messages',
  label: 'Recent Messages',
  enabled: true
}, {
  id: 'active_alerts',
  label: 'Active Alerts',
  enabled: true
}];
function useDashWidgets() {
  const migrateWidgets = stored => {
    if (!Array.isArray(stored)) return null;
    const seen = new Set();
    const migrated = [];
    const add = widget => {
      if (!widget || !widget.id || seen.has(widget.id)) return;
      seen.add(widget.id);
      migrated.push(widget);
    };
    for (const widget of stored) {
      if (!widget || !widget.id) continue;
      if (widget.id === 'stat_overview') {
        const enabled = widget.enabled !== false;
        for (const def of DEFAULT_DASH_WIDGETS.filter(w => w.id.startsWith('stat_'))) {
          add({
            ...def,
            enabled
          });
        }
        continue;
      }
      if (DEFAULT_DASH_WIDGETS.some(w => w.id === widget.id)) add(widget);
    }
    for (const def of DEFAULT_DASH_WIDGETS) {
      if (!seen.has(def.id)) migrated.push(def);
    }
    return migrated;
  };
  const [widgets, setWidgets] = React.useState(() => {
    try {
      const stored = JSON.parse(localStorage.getItem('dashWidgets') || 'null');
      const migrated = migrateWidgets(stored);
      return migrated || DEFAULT_DASH_WIDGETS;
    } catch {
      return DEFAULT_DASH_WIDGETS;
    }
  });
  const save = w => {
    setWidgets(w);
    localStorage.setItem('dashWidgets', JSON.stringify(w));
  };
  const toggle = id => save(widgets.map(w => w.id === id ? {
    ...w,
    enabled: !w.enabled
  } : w));
  const moveUp = id => {
    const i = widgets.findIndex(w => w.id === id);
    if (i <= 0) return;
    const next = [...widgets];
    [next[i - 1], next[i]] = [next[i], next[i - 1]];
    save(next);
  };
  const moveDown = id => {
    const i = widgets.findIndex(w => w.id === id);
    if (i >= widgets.length - 1) return;
    const next = [...widgets];
    [next[i], next[i + 1]] = [next[i + 1], next[i]];
    save(next);
  };
  const reset = () => save(DEFAULT_DASH_WIDGETS);
  return {
    widgets,
    toggle,
    moveUp,
    moveDown,
    reset
  };
}
function DashboardPage({
  stats,
  nodes,
  messages,
  alerts,
  setPage,
  device,
  gpsStatus,
  trackerDevices = []
}) {
  const {
    node_online_window_h,
    date_format,
    timezone
  } = useSettings();
  const {
    widgets,
    toggle,
    moveUp,
    moveDown,
    reset
  } = useDashWidgets();
  const [customizing, setCustomizing] = React.useState(false);
  const enabledWidgets = widgets.filter(w => w.enabled);
  const statWidgets = enabledWidgets.filter(w => w.id.startsWith('stat_'));
  const contentWidgets = enabledWidgets.filter(w => !w.id.startsWith('stat_'));
  const recentAlerts = alerts.filter(a => !a.acknowledged).slice(0, 5);
  const recentMsgs = messages.slice(0, 5);
  const myNodeId = device && device.my_node_id ? device.my_node_id : null;
  const onlineNodes = nodes.filter(n => nodeOnline(n, myNodeId, device && device.connected, node_online_window_h));

  // Fleet-wide derived metrics
  const nodesWithSnr = nodes.filter(n => n.snr != null);
  const nodesWithRssi = nodes.filter(n => n.rssi != null);
  const nodesWithBat = nodes.filter(n => n.battery_level != null);
  const avgSnr = nodesWithSnr.length ? nodesWithSnr.reduce((s, n) => s + n.snr, 0) / nodesWithSnr.length : null;
  const avgRssi = nodesWithRssi.length ? Math.round(nodesWithRssi.reduce((s, n) => s + n.rssi, 0) / nodesWithRssi.length) : null;
  const avgBat = nodesWithBat.length ? Math.round(nodesWithBat.reduce((s, n) => s + n.battery_level, 0) / nodesWithBat.length) : null;
  const avgChannelUtil = nodes.length ? (nodes.reduce((s, n) => s + (n.channel_util || 0), 0) / nodes.length).toFixed(1) : '0.0';
  const avgAirUtil = nodes.length ? (nodes.reduce((s, n) => s + (n.air_util_tx || 0), 0) / nodes.length).toFixed(1) : '0.0';
  const pctOnline = nodes.length ? Math.round(onlineNodes.length / nodes.length * 100) : 0;

  // Battery buckets
  const batCritical = nodesWithBat.filter(n => n.battery_level < 20).length;
  const batLow = nodesWithBat.filter(n => n.battery_level >= 20 && n.battery_level < 50).length;
  const batGood = nodesWithBat.filter(n => n.battery_level >= 50).length;
  const batUnknown = nodes.length - nodesWithBat.length;
  const lowestBat = [...nodesWithBat].sort((a, b) => a.battery_level - b.battery_level).slice(0, 3);
  const atlasConnected = !!(device && device.connected);
  const atlasLabel = device && (device.long_name || device.short_name) || 'Atlas Control';
  const atlasBatteryPct = device && device.battery_pct != null ? Number(device.battery_pct) : null;
  const atlasBatteryColor = atlasBatteryPct == null ? 'var(--text-muted)' : batColor(atlasBatteryPct);
  const atlasBatterySub = atlasBatteryPct == null ? 'No host battery telemetry' : [device.battery_voltage != null ? `${Number(device.battery_voltage).toFixed(2)}V` : null, device.battery_current_ma != null ? `${(Math.abs(Number(device.battery_current_ma)) / 1000).toFixed(2)}A` : null, device.battery_power_w != null ? `${Math.abs(Number(device.battery_power_w)).toFixed(1)}W` : null, device.battery_status || null].filter(Boolean).join(' · ');
  const gpsFix = gpsStatus && gpsStatus.fix ? gpsStatus.fix : null;
  const gpsDeadReckoning = !!(gpsFix && gpsFix.fix_qual === 6);
  const gpsConnected = !!(gpsStatus && gpsStatus.connected);
  const gpsColor = !gpsConnected ? 'var(--accent-red)' : gpsDeadReckoning ? 'var(--accent-amber)' : gpsFix ? 'var(--accent-green)' : 'var(--accent-red)';
  const gpsLabel = !gpsConnected ? 'Offline' : gpsDeadReckoning ? 'Dead Reckoning' : gpsFix ? gpsFix.fix_label || 'Fix' : 'No Fix';
  const gpsSub = !gpsConnected ? gpsStatus && gpsStatus.port || '/dev/gps' : [gpsFix && gpsFix.sats_in_view != null ? `${gpsFix.sats_in_view} sats` : null, gpsStatus && gpsStatus.port || '/dev/gps'].filter(Boolean).join(' · ');
  const atlasStatusColor = atlasConnected ? gpsConnected ? gpsDeadReckoning ? 'var(--accent-amber)' : 'var(--accent-green)' : 'var(--accent-amber)' : 'var(--accent-red)';
  const atlasStatusValue = !atlasConnected ? `${atlasLabel} Offline` : !gpsConnected ? `${atlasLabel} · GPS Offline` : gpsDeadReckoning ? `${atlasLabel} · GPS DR` : gpsFix ? `${atlasLabel} · GPS Fix` : `${atlasLabel} · GPS No Fix`;
  const atlasStatusSub = [device && device.port ? device.port : null, device && device.my_node_id ? device.my_node_id : null, gpsSub].filter(Boolean).join(' · ');

  // Signal buckets
  const sigBuckets = {
    Excellent: 0,
    Good: 0,
    Fair: 0,
    Poor: 0,
    Unknown: 0
  };
  nodes.forEach(n => {
    const q = signalQuality(n.snr, n.rssi);
    sigBuckets[q.label] = (sigBuckets[q.label] || 0) + 1;
  });

  // Role breakdown
  const roles = {};
  nodes.forEach(n => {
    const r = n.role || 'UNKNOWN';
    roles[r] = (roles[r] || 0) + 1;
  });
  const roleEntries = Object.entries(roles).sort((a, b) => b[1] - a[1]);
  const roleColor = r => r === 'ROUTER' ? 'var(--accent-blue)' : r === 'CLIENT' ? 'var(--accent-green)' : r === 'SENSOR' ? 'var(--accent-amber)' : r === 'REPEATER' ? '#a78bfa' : 'var(--text-muted)';
  const renderStatWidget = id => {
    switch (id) {
      case 'stat_total_nodes':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Total Nodes"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: 'var(--accent-blue)'
          }
        }, stats.total_nodes || 0), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, onlineNodes.length, " online \xB7 ", pctOnline, "%"));
      case 'stat_total_messages':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Messages"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: 'var(--accent-green)'
          }
        }, stats.total_messages || 0), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, "across all channels"));
      case 'stat_atlas_status':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, atlasLabel, " + GPS"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: atlasStatusColor,
            fontSize: 18,
            lineHeight: 1.2
          }
        }, atlasStatusValue), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub",
          style: {
            fontSize: 10.5,
            lineHeight: 1.35
          }
        }, atlasStatusSub || 'Heltec V4 and GPS status unavailable'));
      case 'stat_atlas_battery':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Atlas Battery"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: atlasBatteryColor
          }
        }, atlasBatteryPct != null ? `${atlasBatteryPct}%` : '—'), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, atlasBatterySub));
      case 'stat_active_alerts':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Active Alerts"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: stats.unacked_alerts > 0 ? 'var(--accent-red)' : 'var(--accent-green)'
          }
        }, stats.unacked_alerts || 0), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, "unacknowledged"));
      case 'stat_channel_util':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Avg Channel Util"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: 'var(--accent-amber)'
          }
        }, avgChannelUtil, "%"), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, avgAirUtil, "% air TX util"));
      case 'stat_avg_snr':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Avg SNR"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: avgSnr == null ? 'var(--text-muted)' : avgSnr > 5 ? 'var(--accent-green)' : avgSnr > 0 ? 'var(--accent-amber)' : 'var(--accent-red)'
          }
        }, avgSnr != null ? avgSnr.toFixed(1) : '—'), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, "dB \xB7 ", nodesWithSnr.length, " reporting"));
      case 'stat_mesh_avg_battery':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "stat-card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "stat-label"
        }, "Mesh Avg Battery"), /*#__PURE__*/React.createElement("div", {
          className: "stat-value",
          style: {
            color: avgBat != null ? batColor(avgBat) : 'var(--text-muted)'
          }
        }, avgBat != null ? avgBat + '%' : '—'), /*#__PURE__*/React.createElement("div", {
          className: "stat-sub"
        }, nodesWithBat.length, " reporting"));
      default:
        return null;
    }
  };
  const renderWidget = id => {
    switch (id) {
      case 'network_health':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Network Health")), /*#__PURE__*/React.createElement("div", {
          style: {
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill,minmax(160px,1fr))',
            gap: 12
          }
        }, [{
          label: 'Nodes Online',
          val: `${onlineNodes.length} / ${nodes.length}`,
          sub: `${pctOnline}% of fleet`,
          color: pctOnline >= 80 ? 'var(--accent-green)' : pctOnline >= 50 ? 'var(--accent-amber)' : 'var(--accent-red)'
        }, {
          label: 'Avg SNR',
          val: avgSnr != null ? avgSnr.toFixed(1) + ' dB' : '—',
          sub: 'signal-to-noise',
          color: avgSnr == null ? 'var(--text-muted)' : avgSnr > 5 ? 'var(--accent-green)' : avgSnr > 0 ? 'var(--accent-amber)' : 'var(--accent-red)'
        }, {
          label: 'Avg RSSI',
          val: avgRssi != null ? avgRssi + ' dBm' : '—',
          sub: 'signal strength',
          color: avgRssi == null ? 'var(--text-muted)' : avgRssi > -80 ? 'var(--accent-green)' : avgRssi > -100 ? 'var(--accent-amber)' : 'var(--accent-red)'
        }, {
          label: 'Channel Load',
          val: avgChannelUtil + '%',
          sub: avgAirUtil + '% air TX',
          color: parseFloat(avgChannelUtil) < 30 ? 'var(--accent-green)' : parseFloat(avgChannelUtil) < 60 ? 'var(--accent-amber)' : 'var(--accent-red)'
        }].map(({
          label,
          val,
          sub,
          color
        }) => /*#__PURE__*/React.createElement("div", {
          key: label,
          style: {
            background: 'var(--bg-tertiary)',
            borderRadius: 8,
            padding: '12px 14px'
          }
        }, /*#__PURE__*/React.createElement("div", {
          style: {
            fontSize: 11,
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            marginBottom: 4
          }
        }, label), /*#__PURE__*/React.createElement("div", {
          style: {
            fontSize: 22,
            fontFamily: 'var(--font-mono)',
            fontWeight: 700,
            color
          }
        }, val), /*#__PURE__*/React.createElement("div", {
          style: {
            fontSize: 11,
            color: 'var(--text-muted)',
            marginTop: 3
          }
        }, sub)))));
      case 'phone_trackers':
        {
          const now = Math.floor(Date.now() / 1000);
          const active = trackerDevices.filter(d => d.latitude && d.longitude && now - (d.last_seen || 0) < 300);
          const stale = trackerDevices.filter(d => now - (d.last_seen || 0) >= 300);
          const trackerUrl = window.location.origin + '/tracker';
          return /*#__PURE__*/React.createElement("div", {
            key: id,
            className: "card"
          }, /*#__PURE__*/React.createElement("div", {
            className: "card-header"
          }, /*#__PURE__*/React.createElement("div", {
            className: "card-title"
          }, "\uD83D\uDCF1 Phone Trackers"), /*#__PURE__*/React.createElement("button", {
            className: "btn btn-sm",
            onClick: () => setPage('map')
          }, "View on Map")), trackerDevices.length === 0 ? /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
            style: {
              color: 'var(--text-muted)',
              fontSize: 13,
              marginBottom: 12
            }
          }, "No phones connected yet."), /*#__PURE__*/React.createElement("div", {
            style: {
              fontSize: 12,
              color: 'var(--text-secondary)',
              marginBottom: 6
            }
          }, "Share this link with teammates:"), /*#__PURE__*/React.createElement("div", {
            style: {
              background: 'var(--bg-tertiary)',
              borderRadius: 6,
              padding: '8px 12px',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              color: 'var(--accent-blue)',
              wordBreak: 'break-all'
            }
          }, trackerUrl)) : /*#__PURE__*/React.createElement("div", {
            style: {
              display: 'flex',
              flexDirection: 'column',
              gap: 0
            }
          }, active.map(d => {
            var _d$latitude, _d$longitude;
            return /*#__PURE__*/React.createElement("div", {
              key: d.id,
              style: {
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '9px 0',
                borderBottom: '1px solid var(--border)'
              }
            }, /*#__PURE__*/React.createElement("div", {
              style: {
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: d.color || 'var(--accent-blue)',
                flexShrink: 0,
                boxShadow: `0 0 6px ${d.color || '#3b82f6'}`
              }
            }), /*#__PURE__*/React.createElement("div", {
              style: {
                flex: 1,
                minWidth: 0
              }
            }, /*#__PURE__*/React.createElement("div", {
              style: {
                fontWeight: 600,
                fontSize: 13,
                color: 'var(--text-primary)'
              }
            }, d.name), /*#__PURE__*/React.createElement("div", {
              style: {
                fontSize: 11,
                color: 'var(--text-muted)'
              }
            }, (_d$latitude = d.latitude) === null || _d$latitude === void 0 ? void 0 : _d$latitude.toFixed(5), ", ", (_d$longitude = d.longitude) === null || _d$longitude === void 0 ? void 0 : _d$longitude.toFixed(5))), /*#__PURE__*/React.createElement("div", {
              style: {
                textAlign: 'right',
                flexShrink: 0
              }
            }, d.battery != null && /*#__PURE__*/React.createElement("div", {
              style: {
                fontSize: 12,
                color: batColor(Math.round(d.battery * 100)),
                fontFamily: 'var(--font-mono)'
              }
            }, Math.round(d.battery * 100), "%"), /*#__PURE__*/React.createElement("div", {
              style: {
                fontSize: 11,
                color: 'var(--accent-green)'
              }
            }, "live")));
          }), stale.map(d => /*#__PURE__*/React.createElement("div", {
            key: d.id,
            style: {
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '9px 0',
              borderBottom: '1px solid var(--border)',
              opacity: 0.5
            }
          }, /*#__PURE__*/React.createElement("div", {
            style: {
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: 'var(--text-muted)',
              flexShrink: 0
            }
          }), /*#__PURE__*/React.createElement("div", {
            style: {
              flex: 1
            }
          }, /*#__PURE__*/React.createElement("div", {
            style: {
              fontWeight: 600,
              fontSize: 13,
              color: 'var(--text-muted)'
            }
          }, d.name)), /*#__PURE__*/React.createElement("div", {
            style: {
              fontSize: 11,
              color: 'var(--text-muted)'
            }
          }, "offline"))), /*#__PURE__*/React.createElement("div", {
            style: {
              paddingTop: 10,
              fontSize: 11,
              color: 'var(--text-muted)'
            }
          }, "Tracker URL: ", /*#__PURE__*/React.createElement("span", {
            style: {
              color: 'var(--accent-blue)',
              fontFamily: 'var(--font-mono)'
            }
          }, trackerUrl))));
        }
      case 'battery_fleet':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Battery Fleet Status")), /*#__PURE__*/React.createElement("div", {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: 9
          }
        }, [{
          label: 'Critical  (<20%)',
          count: batCritical,
          color: 'var(--accent-red)'
        }, {
          label: 'Low  (20–49%)',
          count: batLow,
          color: 'var(--accent-amber)'
        }, {
          label: 'Good  (≥50%)',
          count: batGood,
          color: 'var(--accent-green)'
        }, {
          label: 'Unknown',
          count: batUnknown,
          color: 'var(--text-muted)'
        }].map(({
          label,
          count,
          color
        }) => /*#__PURE__*/React.createElement("div", {
          key: label,
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 10
          }
        }, /*#__PURE__*/React.createElement("div", {
          style: {
            width: 130,
            fontSize: 12,
            color: 'var(--text-secondary)',
            flexShrink: 0
          }
        }, label), /*#__PURE__*/React.createElement("div", {
          style: {
            flex: 1,
            background: 'var(--bg-tertiary)',
            borderRadius: 4,
            height: 14,
            overflow: 'hidden'
          }
        }, /*#__PURE__*/React.createElement("div", {
          style: {
            height: '100%',
            width: nodes.length ? `${count / nodes.length * 100}%` : '0%',
            background: color,
            borderRadius: 4,
            transition: 'width .3s'
          }
        })), /*#__PURE__*/React.createElement("div", {
          style: {
            width: 28,
            fontSize: 12,
            fontFamily: 'var(--font-mono)',
            color,
            textAlign: 'right'
          }
        }, count))), lowestBat.length > 0 && /*#__PURE__*/React.createElement("div", {
          style: {
            marginTop: 4,
            paddingTop: 8,
            borderTop: '1px solid var(--border)',
            fontSize: 11,
            color: 'var(--text-muted)'
          }
        }, "Lowest: ", lowestBat.map(n => `${n.alias || n.long_name} ${n.battery_level}%`).join(' · '))));
      case 'signal_health':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Signal Distribution")), /*#__PURE__*/React.createElement("div", {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: 9
          }
        }, [{
          label: 'Excellent',
          color: 'var(--accent-green)'
        }, {
          label: 'Good',
          color: 'var(--accent-blue)'
        }, {
          label: 'Fair',
          color: 'var(--accent-amber)'
        }, {
          label: 'Poor',
          color: 'var(--accent-red)'
        }, {
          label: 'Unknown',
          color: 'var(--text-muted)'
        }].map(({
          label,
          color
        }) => {
          const count = sigBuckets[label] || 0;
          return /*#__PURE__*/React.createElement("div", {
            key: label,
            style: {
              display: 'flex',
              alignItems: 'center',
              gap: 10
            }
          }, /*#__PURE__*/React.createElement("div", {
            style: {
              width: 80,
              fontSize: 12,
              color: 'var(--text-secondary)',
              flexShrink: 0
            }
          }, label), /*#__PURE__*/React.createElement("div", {
            style: {
              flex: 1,
              background: 'var(--bg-tertiary)',
              borderRadius: 4,
              height: 14,
              overflow: 'hidden'
            }
          }, /*#__PURE__*/React.createElement("div", {
            style: {
              height: '100%',
              width: nodes.length ? `${count / nodes.length * 100}%` : '0%',
              background: color,
              borderRadius: 4,
              transition: 'width .3s'
            }
          })), /*#__PURE__*/React.createElement("div", {
            style: {
              width: 28,
              fontSize: 12,
              fontFamily: 'var(--font-mono)',
              color,
              textAlign: 'right'
            }
          }, count));
        })));
      case 'node_roles':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Node Roles")), roleEntries.length === 0 ? /*#__PURE__*/React.createElement("div", {
          style: {
            color: 'var(--text-muted)',
            fontSize: 13
          }
        }, "No nodes") : /*#__PURE__*/React.createElement("div", {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: 9
          }
        }, roleEntries.map(([role, count]) => /*#__PURE__*/React.createElement("div", {
          key: role,
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 10
          }
        }, /*#__PURE__*/React.createElement("div", {
          style: {
            width: 90,
            fontSize: 12,
            color: 'var(--text-secondary)',
            flexShrink: 0
          }
        }, role), /*#__PURE__*/React.createElement("div", {
          style: {
            flex: 1,
            background: 'var(--bg-tertiary)',
            borderRadius: 4,
            height: 14,
            overflow: 'hidden'
          }
        }, /*#__PURE__*/React.createElement("div", {
          style: {
            height: '100%',
            width: nodes.length ? `${count / nodes.length * 100}%` : '0%',
            background: roleColor(role),
            borderRadius: 4,
            transition: 'width .3s'
          }
        })), /*#__PURE__*/React.createElement("div", {
          style: {
            width: 28,
            fontSize: 12,
            fontFamily: 'var(--font-mono)',
            color: roleColor(role),
            textAlign: 'right'
          }
        }, count)))));
      case 'online_nodes':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Online Nodes"), /*#__PURE__*/React.createElement("button", {
          className: "btn btn-sm",
          onClick: () => setPage('mesh')
        }, "View All")), /*#__PURE__*/React.createElement("div", {
          className: "table-wrap"
        }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Name"), /*#__PURE__*/React.createElement("th", null, "Signal"), /*#__PURE__*/React.createElement("th", null, "Battery"), /*#__PURE__*/React.createElement("th", null, "Last Heard"))), /*#__PURE__*/React.createElement("tbody", null, onlineNodes.slice(0, 8).map(n => {
          const sq = signalQuality(n.snr, n.rssi);
          return /*#__PURE__*/React.createElement("tr", {
            key: n.node_id
          }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
            style: {
              color: 'var(--text-primary)',
              fontWeight: 500
            }
          }, n.alias || n.long_name), /*#__PURE__*/React.createElement("br", null), /*#__PURE__*/React.createElement("span", {
            className: "mono",
            style: {
              fontSize: 11,
              color: 'var(--text-muted)'
            }
          }, n.node_id)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
            className: `tag ${sq.cls}`
          }, sq.label)), /*#__PURE__*/React.createElement("td", null, n.battery_level != null ? /*#__PURE__*/React.createElement("span", {
            style: {
              color: batColor(n.battery_level),
              fontFamily: 'var(--font-mono)'
            }
          }, n.battery_level, "%") : '—'), /*#__PURE__*/React.createElement("td", {
            className: "mono",
            style: {
              fontSize: 12
            }
          }, fmtTimestamp(n.last_heard, date_format, timezone)));
        })))));
      case 'recent_messages':
        return /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Recent Messages"), /*#__PURE__*/React.createElement("button", {
          className: "btn btn-sm",
          onClick: () => setPage('comms')
        }, "View All")), /*#__PURE__*/React.createElement("div", {
          className: "msg-list",
          style: {
            maxHeight: 340
          }
        }, recentMsgs.map((m, i) => /*#__PURE__*/React.createElement("div", {
          className: "msg-item",
          key: i
        }, /*#__PURE__*/React.createElement("div", {
          className: "msg-header"
        }, /*#__PURE__*/React.createElement("span", {
          className: "msg-from"
        }, m.from_id), /*#__PURE__*/React.createElement("span", {
          style: {
            color: 'var(--text-muted)',
            fontSize: 11
          }
        }, "\u2192 ", m.to_id === '^all' ? 'Broadcast' : m.to_id), /*#__PURE__*/React.createElement("span", {
          className: "msg-time"
        }, fmtTime(m.rx_time))), /*#__PURE__*/React.createElement("div", {
          className: "msg-text"
        }, m.text)))));
      case 'active_alerts':
        return recentAlerts.length > 0 ? /*#__PURE__*/React.createElement("div", {
          key: id,
          className: "card"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-header"
        }, /*#__PURE__*/React.createElement("div", {
          className: "card-title"
        }, "Active Alerts"), /*#__PURE__*/React.createElement("button", {
          className: "btn btn-sm",
          onClick: () => setPage('comms')
        }, "View All")), /*#__PURE__*/React.createElement("div", {
          style: {
            display: 'flex',
            flexDirection: 'column',
            gap: 8
          }
        }, recentAlerts.map(a => /*#__PURE__*/React.createElement(AlertItem, {
          key: a.id,
          alert: a,
          onAck: () => api.post(`/api/alerts/${a.id}/ack`)
        })))) : null;
      default:
        return null;
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'flex-end',
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setCustomizing(c => !c),
    style: {
      background: customizing ? 'var(--accent-color)' : '',
      color: customizing ? '#ffffff' : ''
    }
  }, customizing ? '✕ Done' : '⊞ Customize')), /*#__PURE__*/React.createElement("div", {
    style: {
      overflow: 'hidden',
      maxHeight: customizing ? '600px' : '0px',
      opacity: customizing ? 1 : 0,
      marginBottom: customizing ? 16 : 0,
      transition: 'max-height 300ms ease, opacity 200ms ease, margin-bottom 300ms ease',
      pointerEvents: customizing ? 'auto' : 'none'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, "Dashboard Widgets"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: reset,
    style: {
      fontSize: 11
    }
  }, "Reset to Default")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      maxHeight: '420px',
      overflowY: 'auto',
      paddingRight: 4
    }
  }, (() => {
    const statItems = widgets.filter(w => w.id.startsWith('stat_'));
    const otherItems = widgets.filter(w => !w.id.startsWith('stat_'));
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("details", {
      open: true,
      style: {
        background: 'var(--bg-tertiary)',
        borderRadius: 8,
        padding: '8px 10px'
      }
    }, /*#__PURE__*/React.createElement("summary", {
      style: {
        cursor: 'pointer',
        listStyle: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 13,
        fontWeight: 600,
        color: 'var(--text-primary)'
      }
    }, "Stats Overview"), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)'
      }
    }, statItems.filter(w => w.enabled).length, " shown")), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        marginTop: 10
      }
    }, statItems.map((w, idx) => /*#__PURE__*/React.createElement("div", {
      key: w.id,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '7px 10px',
        background: 'rgba(255,255,255,0.03)',
        borderRadius: 6
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: w.enabled,
      onChange: () => toggle(w.id),
      style: {
        accentColor: 'var(--accent-color)',
        width: 15,
        height: 15,
        cursor: 'pointer'
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        fontSize: 13,
        color: w.enabled ? 'var(--text-primary)' : 'var(--text-muted)'
      }
    }, w.label), /*#__PURE__*/React.createElement("button", {
      onClick: () => moveUp(w.id),
      disabled: widgets.findIndex(x => x.id === w.id) === 0,
      style: {
        background: 'none',
        border: 'none',
        color: 'var(--text-muted)',
        cursor: 'pointer',
        padding: '0 4px',
        fontSize: 14,
        opacity: widgets.findIndex(x => x.id === w.id) === 0 ? 0.25 : 1
      }
    }, "\u2191"), /*#__PURE__*/React.createElement("button", {
      onClick: () => moveDown(w.id),
      disabled: widgets.findIndex(x => x.id === w.id) === widgets.length - 1,
      style: {
        background: 'none',
        border: 'none',
        color: 'var(--text-muted)',
        cursor: 'pointer',
        padding: '0 4px',
        fontSize: 14,
        opacity: widgets.findIndex(x => x.id === w.id) === widgets.length - 1 ? 0.25 : 1
      }
    }, "\u2193"))))), otherItems.map((w, idx) => /*#__PURE__*/React.createElement("div", {
      key: w.id,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '7px 10px',
        background: 'var(--bg-tertiary)',
        borderRadius: 6
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: w.enabled,
      onChange: () => toggle(w.id),
      style: {
        accentColor: 'var(--accent-color)',
        width: 15,
        height: 15,
        cursor: 'pointer'
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        flex: 1,
        fontSize: 13,
        color: w.enabled ? 'var(--text-primary)' : 'var(--text-muted)'
      }
    }, w.label), /*#__PURE__*/React.createElement("button", {
      onClick: () => moveUp(w.id),
      disabled: widgets.findIndex(x => x.id === w.id) === 0,
      style: {
        background: 'none',
        border: 'none',
        color: 'var(--text-muted)',
        cursor: 'pointer',
        padding: '0 4px',
        fontSize: 14,
        opacity: widgets.findIndex(x => x.id === w.id) === 0 ? 0.25 : 1
      }
    }, "\u2191"), /*#__PURE__*/React.createElement("button", {
      onClick: () => moveDown(w.id),
      disabled: widgets.findIndex(x => x.id === w.id) === widgets.length - 1,
      style: {
        background: 'none',
        border: 'none',
        color: 'var(--text-muted)',
        cursor: 'pointer',
        padding: '0 4px',
        fontSize: 14,
        opacity: widgets.findIndex(x => x.id === w.id) === widgets.length - 1 ? 0.25 : 1
      }
    }, "\u2193"))));
  })()))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 16
    }
  }, statWidgets.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "dash-grid"
  }, statWidgets.map(w => renderStatWidget(w.id))), enabledWidgets.length > 0 ? contentWidgets.map(w => renderWidget(w.id)) : /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 20
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Dashboard Hidden")), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13,
      marginBottom: 14
    }
  }, "All dashboard widgets are currently disabled on this device, so the dashboard has nothing to render."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: () => {
      reset();
      setCustomizing(false);
    }
  }, "Reset Dashboard"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setCustomizing(true)
  }, "Customize Widgets")))));
}

// ═══════════════════════════════════════════════
// Rename Modal
// ═══════════════════════════════════════════════
function RenameModal({
  node,
  currentAlias,
  onSave,
  onClose
}) {
  const [value, setValue] = useState(currentAlias || '');
  const [error, setError] = useState('');
  const nodeUrl = `/api/nodes/${encodeURIComponent(node.node_id)}/alias`;
  const save = async () => {
    if (!value.trim()) {
      setError('Please enter a name.');
      return;
    }
    try {
      const result = await api.put(nodeUrl, {
        alias: value.trim()
      });
      if (result && result.error) {
        setError(result.error);
        return;
      }
      onSave(node.node_id, value.trim());
      onClose();
    } catch (e) {
      setError('Failed to save. Please try again.');
    }
  };
  const clear = async () => {
    try {
      await api.put(nodeUrl, {
        alias: ''
      });
      onSave(node.node_id, null);
      onClose();
    } catch (e) {
      setError('Failed to clear. Please try again.');
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "rename-overlay",
    onClick: e => e.target === e.currentTarget && onClose()
  }, /*#__PURE__*/React.createElement("div", {
    className: "rename-modal"
  }, /*#__PURE__*/React.createElement("h3", null, "Rename Node"), /*#__PURE__*/React.createElement("p", null, "Set a custom display name for ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-cyan)',
      fontFamily: 'var(--font-mono)'
    }
  }, node.long_name || node.node_id)), /*#__PURE__*/React.createElement("input", {
    value: value,
    onChange: e => {
      setValue(e.target.value);
      setError('');
    },
    placeholder: node.long_name || node.short_name || node.node_id,
    onKeyDown: e => {
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') onClose();
    },
    autoFocus: true
  }), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      margin: '6px 0'
    }
  }, error), /*#__PURE__*/React.createElement("div", {
    className: "rename-modal-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onClose
  }, "Cancel"), currentAlias && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      color: 'var(--accent-red)',
      borderColor: 'var(--accent-red)'
    },
    onClick: clear
  }, "Clear"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: save
  }, "Save"))));
}

// ═══════════════════════════════════════════════
// Channel Modal (create / edit)
// ═══════════════════════════════════════════════
const CH_COLORS = ['var(--accent-green)', 'var(--accent-blue)', 'var(--accent-amber)', 'var(--accent-purple)', 'var(--accent-cyan)', 'var(--accent-red)', '#22c55e', '#60a5fa'];
function ChannelModal({
  existing,
  takenNums,
  onSave,
  onClose
}) {
  var _find;
  const editMode = existing != null;
  const firstAvailable = editMode ? null : (_find = [1, 2, 3, 4, 5, 6, 7].find(n => !takenNums.includes(n))) !== null && _find !== void 0 ? _find : null;
  const [mode, setMode] = useState('create');
  const [num, setNum] = useState(editMode ? existing.channel_num : firstAvailable);
  const [name, setName] = useState(editMode ? existing.name : '');
  const [psk, setPsk] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [shareData, setShareData] = useState(null);
  const [copyState, setCopyState] = useState('');
  const isPrimary = !!(existing !== null && existing !== void 0 && existing.is_primary) || num === 0;
  const workflow = editMode ? 'edit' : mode;
  const isJoinMode = workflow === 'join';
  const title = editMode ? 'Edit Channel' : isJoinMode ? 'Join Channel' : 'Create Channel';
  const save = async () => {
    if (num === null) {
      setError('Please select a channel slot.');
      return;
    }
    if (!isJoinMode && !name.trim()) {
      setError('Please enter a channel name.');
      return;
    }
    if (isJoinMode && !psk.trim()) {
      setError('Paste an invite link, or enter the channel name and key manually.');
      return;
    }
    if (isJoinMode && psk.trim() && !psk.trim().includes('/#') && !psk.trim().includes('/?add=true#') && !name.trim()) {
      setError('Enter the channel name when joining with a manual key.');
      return;
    }
    setSaving(true);
    try {
      const result = editMode ? await api.put(`/api/channels/${num}`, {
        name: name.trim(),
        psk: psk.trim() || undefined
      }) : await api.post('/api/channels', {
        channel_num: num,
        name: name.trim(),
        psk: psk.trim() || undefined
      });
      if (result && result.error) {
        setError(result.error);
        return;
      }
      onSave(result === null || result === void 0 ? void 0 : result.channels);
      if (!editMode && !isJoinMode) {
        try {
          const share = await api.get(`/api/channels/${num}/share`);
          setShareData(share);
        } catch (_) {
          setShareData({
            share_url: '',
            qr_base64: null,
            unavailable: true
          });
        }
        return;
      }
      onClose();
    } catch (e) {
      setError((e === null || e === void 0 ? void 0 : e.message) || 'Failed to save channel. Please try again.');
    } finally {
      setSaving(false);
    }
  };
  const copyShare = async () => {
    if (!(shareData !== null && shareData !== void 0 && shareData.share_url)) return;
    try {
      await navigator.clipboard.writeText(shareData.share_url);
      setCopyState('Copied');
      setTimeout(() => setCopyState(''), 1200);
    } catch (_) {
      setCopyState('Copy failed');
      setTimeout(() => setCopyState(''), 1600);
    }
  };
  if (shareData) {
    return /*#__PURE__*/React.createElement("div", {
      className: "rename-overlay",
      onClick: e => e.target === e.currentTarget && onClose()
    }, /*#__PURE__*/React.createElement("div", {
      className: "rename-modal",
      style: {
        width: 400
      }
    }, /*#__PURE__*/React.createElement("h3", null, "Channel Ready"), /*#__PURE__*/React.createElement("p", {
      style: {
        marginBottom: 12
      }
    }, "Atlas created ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-primary)',
        fontWeight: 700
      }
    }, name.trim()), " on channel ", num, ". Share this invite with the other nodes you want to add."), /*#__PURE__*/React.createElement("div", {
      className: "channel-help-card"
    }, (shareData === null || shareData === void 0 ? void 0 : shareData.qr_base64) && /*#__PURE__*/React.createElement("div", {
      style: {
        textAlign: 'center',
        marginBottom: 12
      }
    }, /*#__PURE__*/React.createElement("img", {
      src: `data:image/png;base64,${shareData.qr_base64}`,
      alt: "Meshtastic channel invite QR",
      style: {
        width: '100%',
        maxWidth: 240,
        borderRadius: 8,
        background: 'white',
        padding: 8
      }
    })), !(shareData !== null && shareData !== void 0 && shareData.unavailable) ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        marginBottom: 6,
        textTransform: 'uppercase',
        letterSpacing: '0.05em'
      }
    }, "Invite link"), /*#__PURE__*/React.createElement("textarea", {
      readOnly: true,
      value: (shareData === null || shareData === void 0 ? void 0 : shareData.share_url) || '',
      style: {
        width: '100%',
        minHeight: 84,
        resize: 'vertical',
        fontSize: 12,
        fontFamily: 'var(--font-mono)'
      }
    })) : /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 12,
        color: 'var(--accent-amber)',
        lineHeight: 1.5
      }
    }, "The channel was created, but Atlas could not load the invite QR or link right now. Open the channel and use ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-primary)'
      }
    }, "Invite Nodes"), " to try again."), /*#__PURE__*/React.createElement("div", {
      className: "channel-helper-steps"
    }, /*#__PURE__*/React.createElement("div", null, "1. Open the Meshtastic app on the other node."), /*#__PURE__*/React.createElement("div", null, "2. Scan this QR code or paste the invite link."), /*#__PURE__*/React.createElement("div", null, "3. The node joins ", /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-primary)'
      }
    }, name.trim()), " with the correct key."))), /*#__PURE__*/React.createElement("div", {
      className: "rename-modal-actions"
    }, /*#__PURE__*/React.createElement("button", {
      className: "btn btn-sm",
      onClick: onClose
    }, "Done"), /*#__PURE__*/React.createElement("button", {
      className: "btn btn-sm btn-primary",
      onClick: copyShare,
      disabled: !(shareData !== null && shareData !== void 0 && shareData.share_url)
    }, copyState || 'Copy Invite Link'))));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "rename-overlay",
    onClick: e => e.target === e.currentTarget && onClose()
  }, /*#__PURE__*/React.createElement("div", {
    className: "rename-modal",
    style: {
      width: 400
    }
  }, /*#__PURE__*/React.createElement("h3", null, title), /*#__PURE__*/React.createElement("p", {
    style: {
      marginBottom: 12
    }
  }, editMode ? `Update channel ${num}.` : isJoinMode ? 'Join with either an invite link or the channel name and key.' : 'Create a new shared channel, then invite the other nodes from the next step.'), !editMode && /*#__PURE__*/React.createElement("div", {
    className: "channel-mode-row"
  }, /*#__PURE__*/React.createElement("button", {
    className: `channel-mode-btn ${mode === 'create' ? 'active' : ''}`,
    onClick: () => {
      setMode('create');
      setError('');
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "channel-mode-title"
  }, "Create new"), /*#__PURE__*/React.createElement("div", {
    className: "channel-mode-copy"
  }, "Make a fresh channel and invite other nodes after saving.")), /*#__PURE__*/React.createElement("button", {
    className: `channel-mode-btn ${mode === 'join' ? 'active' : ''}`,
    onClick: () => {
      setMode('join');
      setError('');
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "channel-mode-title"
  }, "Join existing"), /*#__PURE__*/React.createElement("div", {
    className: "channel-mode-copy"
  }, "Paste an invite link, or type the channel name and key if you only have those."))), !editMode && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6,
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, "Channel slot"), /*#__PURE__*/React.createElement("div", {
    className: "channel-num-selector"
  }, [1, 2, 3, 4, 5, 6, 7].map(n => /*#__PURE__*/React.createElement("button", {
    key: n,
    className: `channel-num-btn ${num === n ? 'selected' : ''}`,
    disabled: takenNums.includes(n),
    onClick: () => setNum(n),
    title: takenNums.includes(n) ? 'Already configured' : `Channel ${n}`
  }, takenNums.includes(n) ? '✓' : n)))), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6,
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, isJoinMode ? 'Channel name' : 'Channel name'), /*#__PURE__*/React.createElement("input", {
    value: name,
    onChange: e => {
      setName(e.target.value);
      setError('');
    },
    placeholder: isJoinMode ? 'Needed for manual key entry. Optional if you paste an invite link.' : 'e.g. Team Alpha, Ops, Base Camp...',
    onKeyDown: e => {
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') onClose();
    },
    autoFocus: true
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      margin: '10px 0 6px',
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, isJoinMode ? 'Invite link or channel key' : 'Channel key or invite link'), /*#__PURE__*/React.createElement("input", {
    value: psk,
    onChange: e => {
      setPsk(e.target.value);
      setError('');
    },
    placeholder: editMode ? 'Leave blank to keep current key, or paste a Meshtastic invite link' : isJoinMode ? 'Paste the invite link, or enter default / random / simple0 / 0x... / base64' : 'Optional: leave blank for a secure random key, or paste an invite link',
    onKeyDown: e => {
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') onClose();
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "channel-help-card",
    style: {
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      lineHeight: 1.5
    }
  }, isPrimary ? 'This is the primary channel. Changing its key changes the radio’s main mesh channel.' : isJoinMode ? 'If you have an invite link, paste it and Atlas will import the name and key. If the Meshtastic app does not give you a link, enter the same channel name and key used on the other node.' : 'Leave the key blank and Atlas will generate a secure random key for this new channel. After saving, you will get a QR code and invite link to add other nodes.')), (existing === null || existing === void 0 ? void 0 : existing.psk_state) && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, "Current key state: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-secondary)'
    }
  }, existing.psk_state)), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      marginBottom: 8
    }
  }, error), /*#__PURE__*/React.createElement("div", {
    className: "rename-modal-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onClose
  }, "Cancel"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: save
  }, saving ? 'Saving…' : editMode ? 'Save Changes' : isJoinMode ? 'Join Channel' : 'Create Channel'))));
}
function ChannelShareModal({
  channel,
  onClose
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [share, setShare] = useState(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get(`/api/channels/${channel.channel_num}/share`);
        if (!cancelled) setShare(data);
      } catch (e) {
        if (!cancelled) setError((e === null || e === void 0 ? void 0 : e.message) || 'Unable to load channel share data.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [channel.channel_num]);
  const copyShare = async () => {
    if (!(share !== null && share !== void 0 && share.share_url)) return;
    try {
      await navigator.clipboard.writeText(share.share_url);
    } catch (_) {}
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "rename-overlay",
    onClick: e => e.target === e.currentTarget && onClose()
  }, /*#__PURE__*/React.createElement("div", {
    className: "rename-modal",
    style: {
      width: 380
    }
  }, /*#__PURE__*/React.createElement("h3", null, "Invite Nodes"), /*#__PURE__*/React.createElement("p", {
    style: {
      marginBottom: 12
    }
  }, "Add another node to ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)',
      fontWeight: 600
    }
  }, channel.name), " by sharing this invite."), loading ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)',
      padding: '12px 0'
    }
  }, "Loading share data\u2026") : error ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--accent-red)',
      padding: '12px 0'
    }
  }, error) : /*#__PURE__*/React.createElement(React.Fragment, null, (share === null || share === void 0 ? void 0 : share.qr_base64) && /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'center',
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: `data:image/png;base64,${share.qr_base64}`,
    alt: "Meshtastic channel share QR",
    style: {
      width: '100%',
      maxWidth: 260,
      borderRadius: 8,
      background: 'white',
      padding: 8
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 6
    }
  }, "Scan in the Meshtastic app to join this channel.")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6,
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, "Invite link"), /*#__PURE__*/React.createElement("textarea", {
    readOnly: true,
    value: (share === null || share === void 0 ? void 0 : share.share_url) || '',
    style: {
      width: '100%',
      minHeight: 84,
      resize: 'vertical',
      fontSize: 12,
      fontFamily: 'var(--font-mono)'
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "channel-helper-steps"
  }, /*#__PURE__*/React.createElement("div", null, "1. Open Meshtastic on the node you want to add."), /*#__PURE__*/React.createElement("div", null, "2. Scan this QR code or paste the invite link."), /*#__PURE__*/React.createElement("div", null, "3. That node joins ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, channel.name), "."))), /*#__PURE__*/React.createElement("div", {
    className: "rename-modal-actions",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onClose
  }, "Close"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: copyShare,
    disabled: !(share !== null && share !== void 0 && share.share_url)
  }, "Copy Invite Link"))));
}

// ═══════════════════════════════════════════════
// Messages Page
// ═══════════════════════════════════════════════
function MessagesPage({
  messages,
  nodes,
  device,
  refresh
}) {
  var _dmDiagnostics$prefer;
  const [selection, setSelection] = useState({
    type: 'channel',
    num: 0
  });
  const [text, setText] = useState('');
  const [channels, setChannels] = useState([]);
  const [dmDiagnostics, setDmDiagnostics] = useState(null);
  const [contactsOpen, setContactsOpen] = useState(() => !isAtlasMobileClient());
  const [renameTarget, setRenameTarget] = useState(null); // node for contact rename
  const [channelModal, setChannelModal] = useState(null); // null | 'new' | channel obj
  const [channelShare, setChannelShare] = useState(null); // null | channel obj
  const [aliases, setAliases] = useState({});
  const [sendError, setSendError] = useState(null);
  const [sendStatus, setSendStatus] = useState(null);
  const [sending, setSending] = useState(false);
  const [dmLastRead, setDmLastRead] = useState(() => {
    try {
      const stored = localStorage.getItem('dmLastRead');
      const parsed = stored ? JSON.parse(stored) : {};
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  });
  const [channelLastRead, setChannelLastRead] = useState(() => {
    try {
      const stored = localStorage.getItem('channelLastRead');
      const parsed = stored ? JSON.parse(stored) : {};
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  });
  const chatEndRef = useRef(null);
  const myNodeId = device && device.my_node_id ? String(device.my_node_id) : null;
  const loadChannels = useCallback(async (nextChannels = null) => {
    if (Array.isArray(nextChannels)) {
      setChannels(nextChannels);
      return nextChannels;
    }
    const loaded = await api.get('/api/channels');
    setChannels(loaded);
    return loaded;
  }, []);
  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  // Merge aliases from nodes into the aliases state.  Use a merge (not replace)
  // so a locally-set alias survives until the node_update socket event arrives.
  useEffect(() => {
    setAliases(prev => {
      const next = {
        ...prev
      };
      let changed = false;
      nodes.forEach(n => {
        if (n.alias && next[n.node_id] !== n.alias) {
          next[n.node_id] = n.alias;
          changed = true;
        } else if (!n.alias && n.node_id in next && n.alias === null) {
          // alias explicitly cleared in DB (null, not just absent from partial update)
          delete next[n.node_id];
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [nodes]);
  const resolveName = useCallback(id => {
    if (!id) return '?';
    if (aliases[id]) return aliases[id];
    const n = nodes.find(x => x.node_id === id);
    return n ? n.long_name || n.short_name || id : id;
  }, [nodes, aliases]);
  const channelName = useCallback(num => {
    const ch = channels.find(c => c.channel_num === num);
    return ch ? ch.name : `Channel ${num}`;
  }, [channels]);
  const directNodes = useMemo(() => nodes.filter(n => n.node_id && n.node_id !== 'local_gps' && (!myNodeId || n.node_id !== myNodeId)), [nodes, myNodeId]);
  useEffect(() => {
    try {
      localStorage.setItem('dmLastRead', JSON.stringify(dmLastRead));
    } catch {}
  }, [dmLastRead]);
  useEffect(() => {
    try {
      localStorage.setItem('channelLastRead', JSON.stringify(channelLastRead));
    } catch {}
  }, [channelLastRead]);
  const dmUnreadCounts = useMemo(() => {
    const counts = {};
    for (const m of messages) {
      if (!m.is_direct || !m.from_id || m.from_id === myNodeId) continue;
      const nodeId = m.from_id;
      const lastReadTs = Number(dmLastRead[nodeId] || 0);
      const msgTs = Number(m.rx_time || 0);
      if (msgTs > lastReadTs) counts[nodeId] = (counts[nodeId] || 0) + 1;
    }
    return counts;
  }, [messages, dmLastRead, myNodeId]);
  const channelUnreadCounts = useMemo(() => {
    const counts = {};
    for (const m of messages) {
      if (m.is_direct || !m.from_id || m.from_id === myNodeId) continue;
      const ch = m.channel || 0;
      const lastReadTs = Number(channelLastRead[ch] || 0);
      const msgTs = Number(m.rx_time || 0);
      if (msgTs > lastReadTs) counts[ch] = (counts[ch] || 0) + 1;
    }
    return counts;
  }, [messages, channelLastRead, myNodeId]);

  // Filter messages for current selection
  const conversation = useMemo(() => {
    if (selection.type === 'node') {
      return [...messages.filter(m => {
        if (!m.is_direct) return false;
        return m.from_id === selection.nodeId || m.to_id === selection.nodeId;
      })].reverse();
    }
    return [...messages.filter(m => (m.channel || 0) === selection.num && m.to_id === '^all')].reverse();
  }, [messages, selection]);
  useEffect(() => {
    if (chatEndRef.current) chatEndRef.current.scrollIntoView({
      behavior: 'smooth'
    });
  }, [conversation.length, selection]);
  useEffect(() => {
    if (!sendStatus) return undefined;
    const timer = setTimeout(() => setSendStatus(null), 4000);
    return () => clearTimeout(timer);
  }, [sendStatus]);
  useEffect(() => {
    if (selection.type !== 'node' || !selection.nodeId) return;
    const latestIncomingTs = conversation.reduce((latest, m) => {
      if (m.is_direct && m.from_id === selection.nodeId) return Math.max(latest, Number(m.rx_time || 0));
      return latest;
    }, 0);
    if (!latestIncomingTs) return;
    setDmLastRead(prev => {
      if (Number(prev[selection.nodeId] || 0) >= latestIncomingTs) return prev;
      return {
        ...prev,
        [selection.nodeId]: latestIncomingTs
      };
    });
  }, [selection, conversation]);
  useEffect(() => {
    if (selection.type !== 'channel') return;
    const ch = selection.num;
    const latestIncomingTs = conversation.reduce((latest, m) => {
      if (!m.is_direct && (m.channel || 0) === ch && m.from_id !== myNodeId) return Math.max(latest, Number(m.rx_time || 0));
      return latest;
    }, 0);
    if (!latestIncomingTs) return;
    setChannelLastRead(prev => {
      if (Number(prev[ch] || 0) >= latestIncomingTs) return prev;
      return {
        ...prev,
        [ch]: latestIncomingTs
      };
    });
  }, [selection, conversation]);
  useEffect(() => {
    let cancelled = false;
    if (selection.type !== 'node' || !selection.nodeId) {
      setDmDiagnostics(null);
      return;
    }
    api.get(`/api/nodes/${encodeURIComponent(selection.nodeId)}/dm-diagnostics`).then(data => {
      if (!cancelled) setDmDiagnostics(data && !data.error ? data : null);
    }).catch(() => {
      if (!cancelled) setDmDiagnostics(null);
    });
    return () => {
      cancelled = true;
    };
  }, [selection, messages, nodes]);
  const send = async () => {
    if (!text.trim()) return;
    setSending(true);
    setSendError(null);
    setSendStatus(null);
    const payload = selection.type === 'node' ? {
      text: text.trim(),
      destination: selection.nodeId,
      channel: 0
    } : {
      text: text.trim(),
      destination: null,
      channel: selection.num
    };
    try {
      const res = await api.post('/api/messages/send', payload);
      setText('');
      const targetLabel = selection.type === 'node' ? selNodeObj ? resolveName(selNodeObj.node_id) : selection.nodeId : channelName(selection.num);
      setSendStatus({
        kind: 'success',
        text: selection.type === 'node' ? `Message sent to ${targetLabel}.` : `Message sent on ${targetLabel}.`
      });
      refresh();
    } catch (err) {
      const message = (err === null || err === void 0 ? void 0 : err.message) || 'Message send failed';
      setSendError(message);
      setSendStatus({
        kind: 'error',
        text: `Send failed: ${message}`
      });
    } finally {
      setSending(false);
    }
  };
  const deleteChannel = async num => {
    if (num === 0) return;
    await fetch(`/api/channels/${num}`, {
      method: 'DELETE'
    });
    loadChannels();
    if (selection.type === 'channel' && selection.num === num) {
      setSelection({
        type: 'channel',
        num: 0
      });
    }
  };
  const handleAliasUpdate = (nodeId, alias) => {
    setAliases(prev => {
      const next = {
        ...prev
      };
      if (alias) next[nodeId] = alias;else delete next[nodeId];
      return next;
    });
  };
  const selChannelObj = channels.find(c => c.channel_num === selection.num) || null;
  const selNodeObj = selection.type === 'node' ? directNodes.find(n => n.node_id === selection.nodeId) || null : null;
  const PencilIcon = () => /*#__PURE__*/React.createElement("svg", {
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "page messages-page-wrap"
  }, renameTarget && /*#__PURE__*/React.createElement(RenameModal, {
    node: renameTarget,
    currentAlias: aliases[renameTarget.node_id],
    onSave: handleAliasUpdate,
    onClose: () => setRenameTarget(null)
  }), channelModal !== null && /*#__PURE__*/React.createElement(ChannelModal, {
    existing: channelModal === 'new' ? null : channelModal,
    takenNums: channelModal === 'new' ? channels.map(c => c.channel_num) : [],
    onSave: loadChannels,
    onClose: () => setChannelModal(null)
  }), channelShare && /*#__PURE__*/React.createElement(ChannelShareModal, {
    channel: channelShare,
    onClose: () => setChannelShare(null)
  }), /*#__PURE__*/React.createElement("div", {
    className: "messages-layout"
  }, /*#__PURE__*/React.createElement("div", {
    className: "contacts-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: `contacts-panel-toggle ${contactsOpen ? 'open' : ''}`,
    onClick: () => setContactsOpen(o => !o)
  }, /*#__PURE__*/React.createElement("span", {
    className: "contacts-panel-toggle-label"
  }, "Channels & Messages"), /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5"
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "6 9 12 15 18 9"
  }))), /*#__PURE__*/React.createElement("div", {
    className: `contacts-panel-body ${contactsOpen ? '' : 'closed'}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "contacts-section-label"
  }, /*#__PURE__*/React.createElement("span", null, "Channels"), /*#__PURE__*/React.createElement("button", {
    className: "contacts-add-btn",
    title: "Create or join a channel",
    onClick: () => setChannelModal('new')
  }, "+")), /*#__PURE__*/React.createElement("div", {
    style: {
      margin: '0 12px 10px',
      padding: '10px 12px',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 14,
      background: 'linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01))'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-primary)',
      fontWeight: 600,
      marginBottom: 4
    }
  }, "Need another shared channel?"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      lineHeight: 1.45
    }
  }, "Use ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, "+"), " to create a new channel for your group or paste an invite link to join one from another node.")), channels.map(ch => {
    const color = CH_COLORS[ch.channel_num] || 'var(--text-muted)';
    const isSel = selection.type === 'channel' && selection.num === ch.channel_num;
    const msgCount = channelUnreadCounts[ch.channel_num] || 0;
    return /*#__PURE__*/React.createElement("div", {
      key: ch.channel_num,
      className: `contact-item ${isSel ? 'active' : ''}`,
      onClick: () => setSelection({
        type: 'channel',
        num: ch.channel_num
      })
    }, /*#__PURE__*/React.createElement("div", {
      className: "channel-icon",
      style: {
        background: color + '22',
        color
      }
    }, ch.channel_num), /*#__PURE__*/React.createElement("span", {
      className: "contact-name"
    }, ch.name), /*#__PURE__*/React.createElement("button", {
      className: "contact-rename-btn",
      title: "Edit",
      onClick: e => {
        e.stopPropagation();
        setChannelModal(ch);
      }
    }, /*#__PURE__*/React.createElement(PencilIcon, null)), /*#__PURE__*/React.createElement("button", {
      className: "contact-rename-btn",
      title: "Invite nodes",
      style: {
        marginLeft: 0
      },
      onClick: e => {
        e.stopPropagation();
        setChannelShare(ch);
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "12",
      height: "12",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11 4"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 0 0 7.07 7.07L13 19"
    }))), ch.channel_num !== 0 && /*#__PURE__*/React.createElement("button", {
      className: "contact-rename-btn",
      title: "Remove",
      style: {
        marginLeft: 0
      },
      onClick: e => {
        e.stopPropagation();
        deleteChannel(ch.channel_num);
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "12",
      height: "12",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2"
    }, /*#__PURE__*/React.createElement("polyline", {
      points: "3 6 5 6 21 6"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M19 6l-1 14H6L5 6"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M10 11v6m4-6v6"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M9 6V4h6v2"
    }))), msgCount > 0 && /*#__PURE__*/React.createElement("span", {
      className: "contact-unread",
      style: {
        background: color,
        color: 'var(--bg-primary)'
      }
    }, msgCount > 99 ? '99+' : msgCount));
  }), /*#__PURE__*/React.createElement("div", {
    className: "contacts-section-label",
    style: {
      marginTop: 18
    }
  }, /*#__PURE__*/React.createElement("span", null, "Direct Messages")), directNodes.length === 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '8px 12px',
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "No nodes available yet."), directNodes.map(node => {
    const isSel = selection.type === 'node' && selection.nodeId === node.node_id;
    const unreadCount = dmUnreadCounts[node.node_id] || 0;
    const pkReady = !!node.dm_ready;
    return /*#__PURE__*/React.createElement("div", {
      key: node.node_id,
      className: `contact-item ${isSel ? 'active' : ''}`,
      onClick: () => setSelection({
        type: 'node',
        nodeId: node.node_id
      })
    }, /*#__PURE__*/React.createElement("div", {
      className: "channel-icon",
      style: {
        background: 'var(--accent-blue-dim)',
        color: 'var(--accent-blue)'
      }
    }, "@"), /*#__PURE__*/React.createElement("div", {
      style: {
        minWidth: 0,
        flex: 1
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "contact-name"
    }, resolveName(node.node_id)), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 10,
        color: pkReady ? 'var(--accent-green)' : 'var(--text-muted)'
      }
    }, pkReady ? 'Secure direct ready' : 'Shared-channel direct')), /*#__PURE__*/React.createElement("button", {
      className: "contact-rename-btn",
      title: "Rename",
      onClick: e => {
        e.stopPropagation();
        setRenameTarget(node);
      }
    }, /*#__PURE__*/React.createElement(PencilIcon, null)), unreadCount > 0 && /*#__PURE__*/React.createElement("span", {
      className: "contact-unread",
      style: {
        background: 'var(--accent-blue)',
        color: 'var(--bg-primary)'
      }
    }, unreadCount > 99 ? '99+' : unreadCount));
  }))), /*#__PURE__*/React.createElement("div", {
    className: "chat-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "chat-header"
  }, selection.type === 'node' ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "channel-icon",
    style: {
      background: 'var(--accent-blue-dim)',
      color: 'var(--accent-blue)',
      width: 28,
      height: 28
    }
  }, "@"), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "chat-header-name"
  }, selNodeObj ? resolveName(selNodeObj.node_id) : selection.nodeId), /*#__PURE__*/React.createElement("div", {
    className: "chat-header-sub"
  }, selNodeObj && selNodeObj.dm_ready ? 'Direct message · secure direct ready' : 'Direct message · shared-channel direct')), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      marginLeft: 'auto'
    },
    onClick: () => selNodeObj && setRenameTarget(selNodeObj)
  }, "Rename")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "channel-icon",
    style: {
      background: (CH_COLORS[selection.num] || 'var(--text-muted)') + '22',
      color: CH_COLORS[selection.num] || 'var(--text-muted)',
      width: 28,
      height: 28
    }
  }, selection.num), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "chat-header-name"
  }, channelName(selection.num)), /*#__PURE__*/React.createElement("div", {
    className: "chat-header-sub"
  }, "Channel ", selection.num, " \xB7 broadcast")), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      marginLeft: 'auto'
    },
    onClick: () => selChannelObj && setChannelModal(selChannelObj)
  }, "Edit"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => selChannelObj && setChannelShare(selChannelObj)
  }, "Invite Nodes"))), selection.type === 'node' && dmDiagnostics && /*#__PURE__*/React.createElement("div", {
    style: {
      margin: '10px 14px 0',
      padding: '10px 12px',
      border: '1px solid var(--border)',
      borderRadius: 12,
      background: 'var(--bg-tertiary)',
      display: 'grid',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      gap: 12,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, "DM Diagnostics"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: dmDiagnostics.secure_direct_ready ? 'var(--accent-green)' : 'var(--accent-amber)'
    }
  }, dmDiagnostics.secure_direct_ready ? 'PKI direct available' : 'Fallback direct only')), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      display: 'grid',
      gap: 4
    }
  }, /*#__PURE__*/React.createElement("div", null, "Peer key in Atlas: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, dmDiagnostics.public_key_present ? 'yes' : 'no')), /*#__PURE__*/React.createElement("div", null, "Peer advertises PKI: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, dmDiagnostics.node_has_pkc ? 'yes' : 'no')), /*#__PURE__*/React.createElement("div", null, "Preferred fallback channel: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, (_dmDiagnostics$prefer = dmDiagnostics.preferred_channel) !== null && _dmDiagnostics$prefer !== void 0 ? _dmDiagnostics$prefer : 0)), /*#__PURE__*/React.createElement("div", null, "Usable local channels: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, (dmDiagnostics.configured_channels || []).length ? dmDiagnostics.configured_channels.join(', ') : 'none')), dmDiagnostics.last_broadcast_channel != null && /*#__PURE__*/React.createElement("div", null, "Last broadcast seen from peer: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, "channel ", dmDiagnostics.last_broadcast_channel), dmDiagnostics.last_broadcast_rx_time ? ` at ${fmtTime(dmDiagnostics.last_broadcast_rx_time)}` : ''), dmDiagnostics.recent_send && /*#__PURE__*/React.createElement("div", null, "Last Atlas send: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, dmDiagnostics.recent_send.status), dmDiagnostics.recent_send.transport ? ` via ${dmDiagnostics.recent_send.transport}` : '', dmDiagnostics.recent_send.channel != null ? ` on channel ${dmDiagnostics.recent_send.channel}` : '', dmDiagnostics.recent_send.updated_at ? ` at ${fmtTime(dmDiagnostics.recent_send.updated_at)}` : ''), dmDiagnostics.recent_send && dmDiagnostics.recent_send.error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)'
    }
  }, "Last error: ", dmDiagnostics.recent_send.error))), /*#__PURE__*/React.createElement("div", {
    className: "chat-messages"
  }, conversation.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "empty-state"
  }, selection.type === 'node' ? `No direct messages yet with ${selNodeObj ? resolveName(selNodeObj.node_id) : selection.nodeId}` : `No messages yet in ${channelName(selection.num)}`), conversation.map((m, i) => {
    const isMine = myNodeId && m.from_id === myNodeId;
    const metaLabel = selection.type === 'node' ? m.raw_json && m.raw_json.includes('"transport": "pki"') ? 'PKC DM' : 'Direct' : null;
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: `chat-bubble-row ${isMine ? 'mine' : 'theirs'}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "chat-bubble-stack"
    }, !isMine && /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--accent-cyan)',
        fontFamily: 'var(--font-mono)',
        marginBottom: 3,
        marginLeft: 2
      }
    }, resolveName(m.from_id)), /*#__PURE__*/React.createElement("div", {
      className: "chat-bubble"
    }, m.text), /*#__PURE__*/React.createElement("div", {
      className: "chat-bubble-meta"
    }, /*#__PURE__*/React.createElement("span", null, fmtTime(m.rx_time)), metaLabel && /*#__PURE__*/React.createElement("span", null, metaLabel), m.rx_snr != null && /*#__PURE__*/React.createElement("span", null, "SNR ", m.rx_snr.toFixed(1)), m.hop_limit != null && m.hop_start != null && /*#__PURE__*/React.createElement("span", null, m.hop_start - m.hop_limit, "/", m.hop_start, " hops"))));
  }), /*#__PURE__*/React.createElement("div", {
    ref: chatEndRef
  })), /*#__PURE__*/React.createElement("div", {
    className: "chat-input-area"
  }, /*#__PURE__*/React.createElement("input", {
    value: text,
    onChange: e => setText(e.target.value),
    placeholder: selection.type === 'node' ? `Direct message ${selNodeObj ? resolveName(selNodeObj.node_id) : selection.nodeId}...` : `Message ${channelName(selection.num)}...`,
    onKeyDown: e => e.key === 'Enter' && send()
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: send,
    disabled: sending
  }, Icons.send)), sendStatus && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '0 14px 8px',
      fontSize: 12,
      color: sendStatus.kind === 'success' ? 'var(--accent-green)' : 'var(--accent-red)'
    }
  }, sendStatus.text), sendError && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '0 14px 12px',
      fontSize: 12,
      color: 'var(--accent-red)'
    }
  }, sendError))));
}

// ═══════════════════════════════════════════════
// Nodes Page
// ═══════════════════════════════════════════════
function haversineKm(lat1, lon1, lat2, lon2) {
  if (lat1 == null || lon1 == null || lat2 == null || lon2 == null) return null;
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Minimum distance (km) from point to a GeoJSON LineString geometry.
// Uses flat-earth projection — accurate for short distances (<1 km).
function distToRouteKm(pLat, pLon, geometry) {
  if (!geometry || !geometry.coordinates || geometry.coordinates.length < 2) return null;
  const R = 6371;
  const cosLat = Math.cos(pLat * Math.PI / 180);
  let minD2 = Infinity;
  const coords = geometry.coordinates;
  for (let i = 0; i < coords.length - 1; i++) {
    const [aLon, aLat] = coords[i];
    const [bLon, bLat] = coords[i + 1];
    // Flat-earth vectors in km, with point A as origin
    const ax = (pLon - aLon) * Math.PI / 180 * cosLat * R;
    const ay = (pLat - aLat) * Math.PI / 180 * R;
    const bx = (bLon - aLon) * Math.PI / 180 * cosLat * R;
    const by = (bLat - aLat) * Math.PI / 180 * R;
    const segLen2 = bx * bx + by * by;
    let d2;
    if (segLen2 === 0) {
      d2 = ax * ax + ay * ay;
    } else {
      const t = Math.max(0, Math.min(1, (ax * bx + ay * by) / segLen2));
      const px = ax - t * bx;
      const py = ay - t * by;
      d2 = px * px + py * py;
    }
    if (d2 < minD2) minD2 = d2;
  }
  return minD2 === Infinity ? null : Math.sqrt(minD2);
}
function fmtDist(km, units) {
  if (km == null) return '—';
  if (units === 'imperial') {
    const miles = km * 0.621371;
    if (miles < 0.1) return `${Math.round(miles * 5280)}ft`;
    return `${miles.toFixed(2)}mi`;
  }
  if (km < 1) return `${Math.round(km * 1000)}m`;
  return `${km.toFixed(2)}km`;
}
function NodesPage({
  nodes,
  device
}) {
  const {
    units,
    date_format,
    show_offline_nodes,
    node_online_window_h,
    timezone
  } = useSettings();
  const [selectedId, setSelectedId] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const canvasRef = useRef(null);
  const myNodeId = device && device.my_node_id ? device.my_node_id : null;
  const myNode = myNodeId ? nodes.find(n => n.node_id === myNodeId) : null;
  // Always derive selected from live nodes so name/alias updates reflect immediately
  const selected = selectedId ? nodes.find(n => n.node_id === selectedId) || null : null;
  const distTo = n => myNode && n.node_id !== myNodeId ? haversineKm(myNode.latitude, myNode.longitude, n.latitude, n.longitude) : null;
  const visibleNodes = show_offline_nodes === 'false' ? nodes.filter(n => nodeOnline(n, myNodeId, device && device.connected, node_online_window_h)) : nodes;
  useEffect(() => {
    if (selected) {
      api.get(`/api/telemetry?node_id=${selected.node_id}&hours=24`).then(setTelemetry);
    }
  }, [selected]);
  useEffect(() => {
    if (!selected || !telemetry.length || !canvasRef.current) return;
    drawChart(canvasRef.current, telemetry);
  }, [telemetry, selected]);
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, !selected ? /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "All Nodes (", visibleNodes.length, ")")), /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Node"), /*#__PURE__*/React.createElement("th", {
    className: "nodes-col-hw"
  }, "Hardware"), /*#__PURE__*/React.createElement("th", null, "Role"), /*#__PURE__*/React.createElement("th", null, "Signal"), /*#__PURE__*/React.createElement("th", null, "Battery"), /*#__PURE__*/React.createElement("th", {
    className: "nodes-col-chutil"
  }, "Ch Util"), /*#__PURE__*/React.createElement("th", {
    className: "nodes-col-dist"
  }, "Distance"), /*#__PURE__*/React.createElement("th", null, "Last Heard"), /*#__PURE__*/React.createElement("th", null, "Health"))), /*#__PURE__*/React.createElement("tbody", null, visibleNodes.map(n => {
    const sq = signalQuality(n.snr, n.rssi);
    const online = nodeOnline(n, myNodeId, device && device.connected, node_online_window_h);
    return /*#__PURE__*/React.createElement("tr", {
      key: n.node_id,
      onClick: () => setSelectedId(n.node_id),
      style: {
        cursor: 'pointer'
      }
    }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        background: online ? 'var(--accent-green)' : 'var(--accent-red)',
        marginRight: 8
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--text-primary)',
        fontWeight: 500
      }
    }, n.alias || n.long_name), /*#__PURE__*/React.createElement("br", null), /*#__PURE__*/React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11,
        color: 'var(--text-muted)'
      }
    }, n.node_id)), /*#__PURE__*/React.createElement("td", {
      className: "nodes-col-hw mono",
      style: {
        fontSize: 12
      }
    }, n.hw_model), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
      className: "tag tag-purple"
    }, n.role || '—')), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
      className: `tag ${sq.cls}`
    }, n.snr != null ? `${n.snr.toFixed(1)} dB` : '—')), /*#__PURE__*/React.createElement("td", null, n.battery_level != null ? /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
      style: {
        color: batColor(n.battery_level),
        fontFamily: 'var(--font-mono)',
        fontSize: 13
      }
    }, n.battery_level, "%"), /*#__PURE__*/React.createElement("div", {
      className: "battery-bar"
    }, /*#__PURE__*/React.createElement("div", {
      className: "battery-fill",
      style: {
        width: `${n.battery_level}%`,
        background: batColor(n.battery_level)
      }
    }))) : '—'), /*#__PURE__*/React.createElement("td", {
      className: "nodes-col-chutil mono",
      style: {
        fontSize: 12
      }
    }, n.channel_util != null ? `${n.channel_util.toFixed(1)}%` : '—'), /*#__PURE__*/React.createElement("td", {
      className: "nodes-col-dist mono",
      style: {
        fontSize: 12
      }
    }, fmtDist(distTo(n), units)), /*#__PURE__*/React.createElement("td", {
      className: "mono",
      style: {
        fontSize: 12
      }
    }, fmtTimestamp(n.last_heard, date_format, timezone)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(HealthBadge, {
      node: n
    })));
  }))))) : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => setSelectedId(null),
    style: {
      marginBottom: 16
    }
  }, "\u2190 Back to Nodes"), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", {
    style: {
      fontSize: 20,
      fontWeight: 700,
      marginBottom: 4
    }
  }, selected.alias || selected.long_name), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, selected.node_id, " \xB7 ", selected.hw_model, " \xB7 ", selected.role)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(HealthBadge, {
    node: selected,
    size: "large"
  }), /*#__PURE__*/React.createElement("span", {
    className: `tag ${nodeOnline(selected, myNodeId, device && device.connected, node_online_window_h) ? 'tag-green' : 'tag-red'}`
  }, nodeOnline(selected, myNodeId, device && device.connected, node_online_window_h) ? 'ONLINE' : 'OFFLINE'))), /*#__PURE__*/React.createElement("div", {
    className: "node-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Battery"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value",
    style: {
      color: batColor(selected.battery_level)
    }
  }, selected.battery_level != null ? `${selected.battery_level}%` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Voltage"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, selected.voltage != null ? `${selected.voltage}V` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "SNR"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value",
    style: {
      color: snrColor(selected.snr)
    }
  }, selected.snr != null ? `${selected.snr.toFixed(1)} dB` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "RSSI"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, selected.rssi != null ? `${selected.rssi} dBm` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Channel Util"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, selected.channel_util != null ? `${selected.channel_util.toFixed(1)}%` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Air Util TX"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, selected.air_util_tx != null ? `${selected.air_util_tx.toFixed(1)}%` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Uptime"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, selected.uptime ? `${Math.floor(selected.uptime / 3600)}h` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Position"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value",
    style: {
      fontSize: 14
    }
  }, selected.latitude && selected.longitude ? `${selected.latitude.toFixed(4)}, ${selected.longitude.toFixed(4)}` : '—')), /*#__PURE__*/React.createElement("div", {
    className: "node-metric"
  }, /*#__PURE__*/React.createElement("div", {
    className: "node-metric-label"
  }, "Distance"), /*#__PURE__*/React.createElement("div", {
    className: "node-metric-value"
  }, fmtDist(distTo(selected), units))))), telemetry.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title",
    style: {
      marginBottom: 12
    }
  }, "Telemetry (24h)"), /*#__PURE__*/React.createElement("div", {
    className: "chart-container"
  }, /*#__PURE__*/React.createElement("canvas", {
    ref: canvasRef
  })))));
}

// ═══════════════════════════════════════════════
// Topology Page
// ═══════════════════════════════════════════════
function TopologyPage({
  nodes
}) {
  const canvasRef = useRef(null);
  const [topo, setTopo] = useState({
    nodes: [],
    links: []
  });
  const [visible, setVisible] = useState(false); // off by default — load on demand
  const [loading, setLoading] = useState(false);
  const loadTopology = () => {
    setLoading(true);
    api.get('/api/topology').then(d => {
      setTopo(d);
      setVisible(true);
      setLoading(false);
    });
  };
  const refresh = () => {
    setLoading(true);
    api.get('/api/topology').then(d => {
      setTopo(d);
      setLoading(false);
    });
  };

  // Merge fresh names from the polled `nodes` prop into topo nodes so name
  // changes are reflected without a manual Refresh.
  const mergedTopo = useMemo(() => {
    if (!nodes || !nodes.length) return topo;
    const nameMap = {};
    nodes.forEach(n => {
      nameMap[n.node_id] = {
        long_name: n.alias || n.long_name,
        short_name: n.short_name
      };
    });
    return {
      ...topo,
      nodes: topo.nodes.map(n => ({
        ...n,
        ...(nameMap[n.node_id] || {})
      }))
    };
  }, [topo, nodes]);
  useEffect(() => {
    if (!visible || !canvasRef.current || !mergedTopo.nodes.length) return;
    const raf = requestAnimationFrame(() => {
      if (canvasRef.current) drawTopology(canvasRef.current, mergedTopo);
    });
    return () => cancelAnimationFrame(raf);
  }, [mergedTopo, visible]);
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Mesh Topology"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center'
    }
  }, visible ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: refresh,
    disabled: loading
  }, loading ? 'Loading…' : 'Refresh'), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setVisible(false)
  }, "Hide")) : /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: loadTopology,
    disabled: loading
  }, loading ? 'Loading…' : 'Load Topology'))), !visible && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '40px 0',
      textAlign: 'center',
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, "Topology rendering is off. Click ", /*#__PURE__*/React.createElement("b", null, "Load Topology"), " to fetch and display the current mesh graph."), visible && /*#__PURE__*/React.createElement("div", {
    className: "topo-container"
  }, /*#__PURE__*/React.createElement("canvas", {
    ref: canvasRef
  })), visible && mergedTopo.links.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title",
    style: {
      marginBottom: 8
    }
  }, "Links"), /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "From"), /*#__PURE__*/React.createElement("th", null, "To"), /*#__PURE__*/React.createElement("th", null, "SNR"), /*#__PURE__*/React.createElement("th", null, "RSSI"))), /*#__PURE__*/React.createElement("tbody", null, mergedTopo.links.map((l, i) => {
    const fromNode = mergedTopo.nodes.find(n => n.node_id === l.from_id);
    const toNode = mergedTopo.nodes.find(n => n.node_id === l.to_id);
    return /*#__PURE__*/React.createElement("tr", {
      key: i
    }, /*#__PURE__*/React.createElement("td", {
      className: "mono",
      style: {
        fontSize: 12
      }
    }, fromNode ? fromNode.alias || fromNode.long_name : l.from_id), /*#__PURE__*/React.createElement("td", {
      className: "mono",
      style: {
        fontSize: 12
      }
    }, toNode ? toNode.alias || toNode.long_name : l.to_id), /*#__PURE__*/React.createElement("td", {
      style: {
        color: snrColor(l.snr)
      }
    }, l.snr != null ? `${l.snr.toFixed(1)} dB` : '—'), /*#__PURE__*/React.createElement("td", {
      className: "mono"
    }, l.rssi != null ? `${l.rssi} dBm` : '—'));
  })))))));
}

// ════════════════════════════════════════════════════════════════════════════
// MAP ENGINE — MapLibre GL + Protomaps PMTiles
// ════════════════════════════════════════════════════════════════════════════

// Register pmtiles:// protocol handler once for the page lifetime.
let _pmtilesProtocolReady = false;
function ensurePMTilesProtocol() {
  if (_pmtilesProtocolReady) return;
  const proto = new pmtiles.Protocol();
  // Prefer the newer async protocol handler when available, but fall back to
  // the legacy adapter for older MapLibre builds.
  const handler = typeof proto.tilev4 === 'function' ? proto.tilev4.bind(proto) : proto.tile.bind(proto);
  maplibregl.addProtocol('pmtiles', handler);
  _pmtilesProtocolReady = true;
}

// Build the MapLibre GL style: Protomaps light basemap + amplified trail/NPS layers.
function atlasMapStyle() {
  const theme = protomapsThemes.namedTheme('light');
  const base = protomapsThemes.layers('protomaps', theme);
  const basemapUrl = `pmtiles://${window.location.origin}/tiles/map.pmtiles`;
  const topoUrl = `pmtiles://${window.location.origin}/tiles/topo.pmtiles`;
  return {
    version: 8,
    glyphs: '/fonts/{fontstack}/{range}.pbf',
    sources: {
      protomaps: {
        type: 'vector',
        url: basemapUrl,
        minzoom: 0,
        maxzoom: 15,
        bounds: [-125.0, 24.0, -66.0, 50.0],
        attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a> (ODbL) · ' + '© <a href="https://protomaps.com" target="_blank">Protomaps</a> · ' + '<a href="https://maplibre.org" target="_blank">MapLibre</a>'
      },
      topo: {
        type: 'raster',
        url: topoUrl,
        tileSize: 256,
        minzoom: 8,
        maxzoom: 13,
        attribution: '<a href="https://www.usgs.gov/programs/national-geospatial-program/national-map" target="_blank">USGS National Map</a>'
      }
    },
    layers: [...base,
    // National park / nature reserve fill
    {
      id: 'atlas-nps-fill',
      type: 'fill',
      source: 'protomaps',
      'source-layer': 'landuse',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['national_park', 'nature_reserve', 'protected_area']]],
      paint: {
        'fill-color': '#1c3a28',
        'fill-opacity': 0.4
      }
    },
    // NPS boundary
    {
      id: 'atlas-nps-border',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'landuse',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['national_park', 'nature_reserve', 'protected_area']]],
      paint: {
        'line-color': '#22c55e',
        'line-width': 1.5,
        'line-opacity': 0.75,
        'line-dasharray': [4, 3]
      }
    },
    // Hiking trail dark casing for contrast
    {
      id: 'atlas-trail-casing',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'track', 'pedestrian', 'footway']]],
      minzoom: 11,
      paint: {
        'line-color': '#000',
        'line-width': ['interpolate', ['linear'], ['zoom'], 11, 2.5, 18, 7],
        'line-opacity': 0.5
      }
    },
    // Hiking trail amber line
    {
      id: 'atlas-trail',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'track', 'pedestrian', 'footway']]],
      minzoom: 11,
      paint: {
        'line-color': ['match', ['get', 'pmap:kind'], 'track', '#d97706', 'pedestrian', '#94a3b8', '#f59e0b'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1, 18, 4],
        'line-dasharray': [3, 2],
        'line-opacity': 0.9
      }
    },
    // Topo raster overlay — rendered above basemap fills, below vector labels
    {
      id: 'atlas-topo',
      type: 'raster',
      source: 'topo',
      minzoom: 8,
      // No layer maxzoom: the source maxzoom (17) tells MapLibre the highest
      // resolution tiles available and handles overzooming at zoom >17.
      // A layer maxzoom:17 would hide the overlay at zoom ≥17.
      layout: {
        visibility: 'none'
      },
      paint: {
        'raster-opacity': 0.55
      }
    },
    // ── AllTrails-style hiking overlay (hidden by default; hiking mode enables it) ──
    // Placed AFTER atlas-topo so trail lines render on top of the topo raster.
    // Dark casing for contrast on all backgrounds
    {
      id: 'atlas-hike-casing',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'track', 'footway', 'pedestrian', 'bridleway']]],
      minzoom: 8,
      layout: {
        visibility: 'none'
      },
      paint: {
        'line-color': '#082010',
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 3, 13, 7, 18, 12],
        'line-opacity': 0.75
      }
    },
    // Solid line for footpaths/footways/pedestrian ways
    {
      id: 'atlas-hike-line',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'footway', 'pedestrian', 'bridleway']]],
      minzoom: 8,
      layout: {
        visibility: 'none'
      },
      paint: {
        'line-color': ['match', ['get', 'pmap:kind'], 'footway', '#4ade80', 'pedestrian', '#86efac', '#16a34a'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.5, 13, 4, 18, 7],
        'line-opacity': 1.0
      }
    },
    // Dashed line for unpaved tracks (dirt roads / forest roads)
    {
      id: 'atlas-hike-track',
      type: 'line',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['==', ['get', 'pmap:kind'], 'track'],
      minzoom: 8,
      layout: {
        visibility: 'none'
      },
      paint: {
        'line-color': '#22c55e',
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 1.5, 13, 4, 18, 7],
        'line-opacity': 1.0,
        'line-dasharray': [5, 2]
      }
    },
    // City / town labels so the street basemap has geographic context.
    {
      id: 'atlas-place-label',
      type: 'symbol',
      source: 'protomaps',
      'source-layer': 'places',
      filter: ['any', ['has', 'name'], ['has', 'pmap:name']],
      minzoom: 4,
      layout: {
        'text-field': ['coalesce', ['get', 'name'], ['get', 'pmap:name']],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 8, 13, 12, 16],
        'text-letter-spacing': 0.02,
        'text-max-width': 10,
        'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
        'text-radial-offset': 0.6
      },
      paint: {
        'text-color': '#334155',
        'text-halo-color': 'rgba(255,255,255,0.92)',
        'text-halo-width': 1.4
      }
    },
    // Named lakes, bays, and rivers improve orientation on the road map.
    {
      id: 'atlas-water-label',
      type: 'symbol',
      source: 'protomaps',
      'source-layer': 'water',
      filter: ['any', ['has', 'name'], ['has', 'pmap:name']],
      minzoom: 6,
      layout: {
        'symbol-placement': 'point',
        'text-field': ['coalesce', ['get', 'name'], ['get', 'pmap:name']],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 6, 11, 10, 13, 14, 15],
        'text-letter-spacing': 0.03,
        'text-max-width': 12
      },
      paint: {
        'text-color': '#0f766e',
        'text-halo-color': 'rgba(255,255,255,0.88)',
        'text-halo-width': 1.2
      }
    },
    // Road labels at closer zooms to make the street map more usable for orientation.
    {
      id: 'atlas-road-label',
      type: 'symbol',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['any', ['has', 'name'], ['has', 'pmap:name']],
      minzoom: 10,
      layout: {
        'symbol-placement': 'line',
        'text-field': ['coalesce', ['get', 'name'], ['get', 'pmap:name']],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 10, 10, 16, 13],
        'text-max-angle': 30,
        'symbol-spacing': 300
      },
      paint: {
        'text-color': '#475569',
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 1.5
      }
    },
    // Trail name labels — show earlier in hiking mode
    {
      id: 'atlas-hike-label',
      type: 'symbol',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'track', 'footway']]],
      minzoom: 12,
      layout: {
        visibility: 'none',
        'symbol-placement': 'line',
        'text-field': ['coalesce', ['get', 'pgf:name'], ['get', 'name']],
        'text-size': 12,
        'text-font': ['Noto Sans Regular'],
        'text-offset': [0, 1],
        'text-max-angle': 30
      },
      paint: {
        'text-color': '#86efac',
        'text-halo-color': '#0a1a0f',
        'text-halo-width': 2
      }
    },
    // Trail name labels at high zoom
    {
      id: 'atlas-trail-label',
      type: 'symbol',
      source: 'protomaps',
      'source-layer': 'roads',
      filter: ['in', ['get', 'pmap:kind'], ['literal', ['path', 'track']]],
      minzoom: 14,
      layout: {
        'symbol-placement': 'line',
        'text-field': ['coalesce', ['get', 'pgf:name'], ['get', 'name']],
        'text-size': 11,
        'text-font': ['Noto Sans Regular'],
        'text-offset': [0, 1.2],
        'text-max-angle': 30
      },
      paint: {
        'text-color': '#fbbf24',
        'text-halo-color': '#000',
        'text-halo-width': 1.5
      }
    }]
  };
}

// Create a configured MapLibre GL map on the given DOM container.
function showMapUnavailable(container, error) {
  const detail = (error === null || error === void 0 ? void 0 : error.message) || (error === null || error === void 0 ? void 0 : error.statusMessage) || String(error || 'Unknown WebGL error');
  container.innerHTML = `
    <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:24px;background:linear-gradient(180deg,#0b1220,#111827);color:#e5e7eb;">
      <div style="max-width:560px;border:1px solid rgba(148,163,184,0.25);background:rgba(17,24,39,0.92);border-radius:14px;padding:20px 22px;box-shadow:0 12px 36px rgba(0,0,0,0.35)">
        <div style="font-size:18px;font-weight:700;margin-bottom:8px">Map Unavailable</div>
        <div style="font-size:14px;line-height:1.55;color:#cbd5e1;margin-bottom:12px">
          This browser could not create a WebGL context, so Atlas cannot render the map on this device.
        </div>
        <div style="font-size:13px;line-height:1.55;color:#94a3b8;margin-bottom:12px">
          Try enabling hardware acceleration or WebGL in the browser, or open Atlas in a different browser/device.
        </div>
        <div class="mono" style="font-size:11px;line-height:1.45;color:#fca5a5;word-break:break-word">${escHtml(detail)}</div>
      </div>
    </div>`;
}
function makeAtlasMap(container, opts = {}) {
  ensurePMTilesProtocol();
  try {
    var _opts$center, _opts$zoom;
    return new maplibregl.Map({
      container,
      style: atlasMapStyle(),
      center: (_opts$center = opts.center) !== null && _opts$center !== void 0 ? _opts$center : [-98.35, 39.5],
      zoom: (_opts$zoom = opts.zoom) !== null && _opts$zoom !== void 0 ? _opts$zoom : 4,
      maxZoom: 19,
      attributionControl: true,
      dragRotate: false,
      touchZoomRotate: true
    });
  } catch (err) {
    console.error('[atlas-map] init failed:', err);
    showMapUnavailable(container, err);
    return null;
  }
}

// ════════════════════════════════════════════════════════════════════════════
// Map Page — MapLibre GL
// ════════════════════════════════════════════════════════════════════════════
function MapPage({
  nodes,
  trackerDevices = [],
  onRemoveTracker,
  device
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef(new Map()); // nodeId  → {marker, popup, el, color}
  const phoneRef = useRef(new Map()); // devId   → marker
  const fetchedRef = useRef(new Set());
  const fitDoneRef = useRef(false);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [positions, setPositions] = useState({});
  const [showTrails, setShowTrails] = useState(false);
  const [showPhones, setShowPhones] = useState(true);
  const [showTopo, setShowTopo] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState(null);
  const [showNoGps, setShowNoGps] = useState(true);

  // Init map
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const m = makeAtlasMap(containerRef.current);
    if (!m) return;
    m.on('error', e => console.error('[atlas-map] error:', e.error));
    // Force resize — fire at 0 ms AND 200 ms in case layout isn't settled yet
    m.resize();
    const resizeTimer = setTimeout(() => m.resize(), 200);
    m.on('load', () => {
      m.addSource('node-trails', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: []
        }
      });
      m.addLayer({
        id: 'node-trails',
        type: 'line',
        source: 'node-trails',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 3,
          'line-opacity': 0.9
        }
      });
      setMapLoaded(true);
    });
    mapRef.current = m;
    return () => {
      clearTimeout(resizeTimer);
      m.remove();
      mapRef.current = null;
    };
  }, []);

  // Fetch position history once per node (all nodes, not just those with a current fix)
  useEffect(() => {
    nodes.forEach(n => {
      if (fetchedRef.current.has(n.node_id)) return;
      fetchedRef.current.add(n.node_id);
      api.get(`/api/positions?node_id=${n.node_id}`).then(pts => setPositions(prev => ({
        ...prev,
        [n.node_id]: pts
      }))).catch(() => {});
    });
  }, [nodes]);

  // Append live position fixes to trails in real time via the shared socket.
  // Without this, trail polylines are static (loaded once via fetchedRef and never refreshed).
  useEffect(() => {
    const socket = window._atlasSocket;
    if (!socket) return;
    const onPos = data => {
      if (!data || !data.node_id || !data.latitude || !data.longitude) return;
      setPositions(prev => ({
        ...prev,
        [data.node_id]: [data, ...(prev[data.node_id] || [])].slice(0, 500)
      }));
    };
    socket.on('position_update', onPos);
    return () => socket.off('position_update', onPos);
  }, []);

  // Node markers — incremental updates
  useEffect(() => {
    var _device$my_node_id;
    const m = mapRef.current;
    if (!m || !mapLoaded) return;
    const myNodeId = (_device$my_node_id = device === null || device === void 0 ? void 0 : device.my_node_id) !== null && _device$my_node_id !== void 0 ? _device$my_node_id : null;
    const validNodes = nodes.filter(n => n.latitude && n.longitude);
    const activeIds = new Set(validNodes.map(n => n.node_id));
    for (const [id, {
      marker
    }] of markersRef.current) {
      if (!activeIds.has(id)) {
        marker.remove();
        markersRef.current.delete(id);
      }
    }

    // Spread coincident markers using a fixed geographic offset so the markers
    // sit at stable real-world positions and never drift when zooming.
    // Nodes are grouped by coordinate rounded to 4dp (~11 m) and sorted by
    // node_id so the angle assignment is deterministic across re-renders.
    const GEO_SPREAD_M = 120; // radius in metres — ~25 px at zoom 15, ~12 px at zoom 13
    const coordKey = n => `${n.latitude.toFixed(4)},${n.longitude.toFixed(4)}`;
    const groups = {};
    validNodes.forEach(n => {
      const k = coordKey(n);
      (groups[k] = groups[k] || []).push(n);
    });
    // nodeLngLat: node_id → [displayLon, displayLat] (geographic, not pixel)
    const nodeLngLat = {};
    Object.values(groups).forEach(group => {
      if (group.length < 2) return;
      const sorted = [...group].sort((a, b) => a.node_id < b.node_id ? -1 : 1);
      sorted.forEach((n, i) => {
        const angle = 2 * Math.PI * i / sorted.length - Math.PI / 2;
        const dy_deg = GEO_SPREAD_M * Math.sin(angle) / 110540; // latitude degrees
        const dx_deg = GEO_SPREAD_M * Math.cos(angle) / (
        // longitude degrees
        111320 * Math.cos(n.latitude * Math.PI / 180) || 1);
        nodeLngLat[n.node_id] = [n.longitude + dx_deg, n.latitude + dy_deg];
      });
    });
    const lnglats = [];
    validNodes.forEach(n => {
      const online = nodeOnline(n, myNodeId, !!(device !== null && device !== void 0 && device.connected));
      const isOwn = !!(myNodeId && n.node_id === myNodeId);
      const color = isOwn ? '#f59e0b' : online ? '#00e5a0' : '#ef4444';
      const displayLngLat = nodeLngLat[n.node_id] || [n.longitude, n.latitude];
      const popupExtra = isOwn ? `Lat: ${n.latitude.toFixed(6)}<br>Lon: ${n.longitude.toFixed(6)}${n.altitude != null ? '<br>Alt: ' + n.altitude.toFixed(1) + ' m' : ''}` : `Battery: ${n.battery_level != null ? n.battery_level + '%' : '—'}<br>SNR: ${n.snr != null ? n.snr.toFixed(1) + ' dB' : '—'}`;
      const navBtn = isOwn ? '' : `<button onclick="window._atlasNavTo&&window._atlasNavTo(${n.latitude},${n.longitude},${JSON.stringify(n.alias || n.long_name || n.node_id).replace(/"/g, '&quot;')})" ` + `style="margin-top:10px;padding:6px 0;background:#0ea5e9;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600;width:100%;letter-spacing:0.3px">` + `Navigate Here ›</button>`;
      const displayName = n.alias || n.long_name || n.short_name || n.node_id;
      const onlineColor = online ? '#00e5a0' : '#ef4444';
      const popupHtml = `<div style="font-family:system-ui;font-size:13px;color:#e2e8f0;min-width:170px">` + `<div style="font-weight:700;font-size:14px;margin-bottom:3px;color:${color}">${escHtml(displayName)}</div>` + `<div style="font-family:monospace;font-size:11px;color:#5a6478;margin-bottom:6px">${escHtml(n.node_id)}</div>` + `<div style="font-size:12px;color:#94a3b8">${escHtml(n.hw_model)} · ${escHtml(n.role || 'CLIENT')}</div>` + `<div style="font-size:12px;color:#94a3b8;margin-top:3px">${popupExtra}</div>` + `<div style="font-size:12px;margin-top:4px;color:${onlineColor}">${online ? '● Online' : '● Offline'} · ${timeAgo(n.last_heard)}</div>` + `${navBtn}</div>`;
      const existing = markersRef.current.get(n.node_id);
      if (existing) {
        existing.marker.setLngLat(displayLngLat);
        existing.popup.setHTML(popupHtml);
        if (existing.color !== color) {
          existing.el.style.background = color;
          existing.el.style.boxShadow = `0 0 ${isOwn ? 10 : 8}px ${color}88`;
          existing.color = color;
        }
      } else {
        const el = document.createElement('div');
        if (isOwn) {
          el.style.cssText = `width:22px;height:22px;background:${color};border:2px solid #0a0e17;border-radius:4px;box-shadow:0 0 10px ${color}88;display:flex;align-items:center;justify-content:center;font-size:14px;cursor:pointer;`;
          el.textContent = '⌂';
        } else {
          el.style.cssText = `width:14px;height:14px;background:${color};border:2px solid #0a0e17;border-radius:50%;box-shadow:0 0 8px ${color}88;cursor:pointer;`;
        }
        const popup = new maplibregl.Popup({
          offset: 12,
          closeOnClick: true
        }).setHTML(popupHtml);
        const marker = new maplibregl.Marker({
          element: el
        }).setLngLat(displayLngLat).setPopup(popup).addTo(m);
        markersRef.current.set(n.node_id, {
          marker,
          popup,
          el,
          color
        });
      }
      lnglats.push([n.longitude, n.latitude]); // use true position for fitBounds
    });
    if (lnglats.length > 0 && !fitDoneRef.current) {
      if (lnglats.length === 1) {
        m.flyTo({
          center: lnglats[0],
          zoom: 13
        });
      } else {
        const bounds = lnglats.reduce((b, ll) => b.extend(ll), new maplibregl.LngLatBounds(lnglats[0], lnglats[0]));
        m.fitBounds(bounds, {
          padding: 60,
          maxZoom: 15
        });
      }
      fitDoneRef.current = true;
    }
  }, [nodes, device, mapLoaded]);

  // GPS trails as GeoJSON source
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapLoaded) return;
    const src = m.getSource('node-trails');
    if (!src) return;
    if (!showTrails) {
      src.setData({
        type: 'FeatureCollection',
        features: []
      });
      return;
    }
    const colors = ['#3b82f6', '#a78bfa', '#f59e0b', '#22d3ee', '#ec4899', '#84cc16'];
    const features = nodes.map((n, i) => {
      const pts = positions[n.node_id];
      if (!pts || pts.length < 2) return null;
      return {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [...pts].reverse().map(p => [p.longitude, p.latitude])
        },
        properties: {
          color: colors[i % colors.length]
        }
      };
    }).filter(Boolean);
    src.setData({
      type: 'FeatureCollection',
      features
    });
  }, [nodes, positions, showTrails, mapLoaded]);

  // Phone tracker markers
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    if (!showPhones) {
      for (const mk of phoneRef.current.values()) mk.remove();
      phoneRef.current.clear();
      return;
    }
    const now = Math.floor(Date.now() / 1000);
    const activeIds = new Set(trackerDevices.filter(d => d.latitude && d.longitude).map(d => d.id));
    for (const [id, mk] of phoneRef.current) {
      if (!activeIds.has(id)) {
        mk.remove();
        phoneRef.current.delete(id);
      }
    }
    trackerDevices.forEach(dev => {
      if (!dev.latitude || !dev.longitude) return;
      const age = now - (dev.last_seen || 0);
      const online = age < 300;
      const color = dev.color || '#3b82f6';
      const batStr = dev.battery != null ? `${Math.round(dev.battery * 100)}%` : '—';
      const removeBtn = onRemoveTracker ? `<br><a href="#" data-tracker-remove="${escHtml(dev.id)}" style="font-size:11px;color:#ef4444;">Remove</a>` : '';
      const popupHtml = `<div style="font-family:system-ui;font-size:13px;color:#1a1a1a;min-width:140px"><b>📱 ${escHtml(dev.name)}</b><br><span style="font-size:12px">Battery: ${escHtml(batStr)}<br>Last: ${online ? Math.round(age) + 's ago' : Math.round(age / 60) + 'm ago (offline)'}</span>${removeBtn}</div>`;
      const existing = phoneRef.current.get(dev.id);
      if (existing) {
        existing.setLngLat([dev.longitude, dev.latitude]);
        existing.getPopup().setHTML(popupHtml);
      } else {
        const el = document.createElement('div');
        el.style.cssText = `width:20px;height:20px;background:${color};border:2px solid #0a0e17;border-radius:4px;box-shadow:0 0 10px ${color}88;display:flex;align-items:center;justify-content:center;font-size:11px;cursor:pointer;opacity:${online ? 1 : 0.4};`;
        el.textContent = '📱';
        const mk = new maplibregl.Marker({
          element: el
        }).setLngLat([dev.longitude, dev.latitude]).setPopup(new maplibregl.Popup({
          offset: 12,
          closeOnClick: true
        }).setHTML(popupHtml)).addTo(m);
        phoneRef.current.set(dev.id, mk);
      }
    });
    // Delegate remove-button clicks via data attribute — avoids inline onclick XSS risk
    if (onRemoveTracker) {
      const m = mapRef.current;
      if (m && !m._trackerRemoveListener) {
        m._trackerRemoveListener = e => {
          const a = e.target.closest('a[data-tracker-remove]');
          if (!a) return;
          e.preventDefault();
          onRemoveTracker(a.dataset.trackerRemove);
        };
        m.getContainer().addEventListener('click', m._trackerRemoveListener);
      }
    }
  }, [trackerDevices, showPhones]);

  // Toggle topo overlay visibility
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapLoaded) return;
    try {
      m.setLayoutProperty('atlas-topo', 'visibility', showTopo ? 'visible' : 'none');
    } catch (_) {}
  }, [showTopo, mapLoaded]);
  const Toggle = ({
    label,
    value,
    onChange,
    color
  }) => /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      cursor: 'pointer',
      fontSize: 12,
      color: 'var(--text-secondary)'
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: value,
    onChange: e => onChange(e.target.checked),
    style: {
      accentColor: color || 'var(--accent-green)',
      width: 14,
      height: 14
    }
  }), label);
  async function scanMesh() {
    setScanning(true);
    setScanMsg(null);
    try {
      const res = await api.post('/api/mesh/scan', {});
      setScanMsg(res.ok ? 'Scan sent — nodes will appear as they respond.' : res.error || 'Scan failed');
    } catch (e) {
      setScanMsg('Scan failed: ' + (e.message || 'unknown error'));
    }
    setScanning(false);
    setTimeout(() => setScanMsg(null), 6000);
  }
  const activePhonesCount = trackerDevices.filter(d => d.latitude && d.longitude && Math.floor(Date.now() / 1000) - (d.last_seen || 0) < 300).length;
  const noGpsNodes = nodes.filter(n => !n.latitude || !n.longitude);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      flex: 1,
      minHeight: 0,
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      padding: '10px 20px',
      background: 'var(--bg-card)',
      borderBottom: '1px solid var(--border)',
      flexShrink: 0,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-secondary)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em'
    }
  }, "Node Map"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, nodes.filter(n => n.latitude && n.longitude).length, " nodes with GPS"), noGpsNodes.length > 0 && /*#__PURE__*/React.createElement("button", {
    onClick: () => setShowNoGps(v => !v),
    style: {
      background: 'none',
      border: 'none',
      cursor: 'pointer',
      fontSize: 11,
      color: 'var(--accent-amber)',
      padding: 0,
      fontFamily: 'inherit'
    }
  }, noGpsNodes.length, " no GPS ", showNoGps ? '▲' : '▼'), activePhonesCount > 0 && /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--accent-blue)'
    }
  }, activePhonesCount, " phone", activePhonesCount !== 1 ? 's' : '', " live"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: 16,
      alignItems: 'center',
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement(Toggle, {
    label: "Topo",
    value: showTopo,
    onChange: setShowTopo,
    color: "#a78bfa"
  }), /*#__PURE__*/React.createElement(Toggle, {
    label: "GPS Trails",
    value: showTrails,
    onChange: setShowTrails,
    color: "#3b82f6"
  }), /*#__PURE__*/React.createElement(Toggle, {
    label: "Phones",
    value: showPhones,
    onChange: setShowPhones,
    color: "#3b82f6"
  }), /*#__PURE__*/React.createElement("button", {
    onClick: scanMesh,
    disabled: scanning,
    title: "Broadcast a NodeInfo + Position request to all mesh nodes, including multi-hop",
    style: {
      padding: '4px 12px',
      borderRadius: 'var(--radius)',
      cursor: 'pointer',
      border: '1px solid var(--accent-green)',
      background: 'var(--accent-green-dim)',
      color: 'var(--accent-green)',
      fontFamily: 'inherit',
      fontSize: 12,
      fontWeight: 600,
      opacity: scanning ? 0.6 : 1
    }
  }, scanning ? 'Scanning…' : 'Scan Mesh'))), scanMsg && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '6px 20px',
      background: 'var(--bg-input)',
      borderBottom: '1px solid var(--border)',
      fontSize: 12,
      color: 'var(--text-muted)',
      flexShrink: 0
    }
  }, scanMsg), showNoGps && noGpsNodes.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-card)',
      borderBottom: '1px solid var(--border)',
      flexShrink: 0,
      maxHeight: 140,
      overflowY: 'auto',
      padding: '8px 20px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      fontWeight: 700,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      letterSpacing: '0.08em',
      marginBottom: 6
    }
  }, "Heard nodes without GPS position"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 6
    }
  }, noGpsNodes.map(n => /*#__PURE__*/React.createElement("div", {
    key: n.node_id,
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '4px 10px',
      fontSize: 11,
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)',
      fontWeight: 500
    }
  }, n.alias || n.long_name || n.short_name || n.node_id), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      color: 'var(--text-muted)',
      fontSize: 10
    }
  }, n.hw_model), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 10
    }
  }, timeAgo(n.last_heard)), /*#__PURE__*/React.createElement("button", {
    title: "Request this node's position via the mesh",
    onClick: () => api.post(`/api/nodes/${encodeURIComponent(n.node_id)}/request-nodeinfo`, {}).catch(() => {}),
    style: {
      background: 'none',
      border: '1px solid var(--border)',
      borderRadius: 3,
      cursor: 'pointer',
      fontSize: 10,
      color: 'var(--text-muted)',
      padding: '1px 6px',
      fontFamily: 'inherit'
    }
  }, "ping"))))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      position: 'relative'
    }
  }, /*#__PURE__*/React.createElement("div", {
    ref: containerRef,
    style: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0
    }
  })));
}

// ═══════════════════════════════════════════════
// Power Analytics Page
// ═══════════════════════════════════════════════
function PowerPage({
  nodes
}) {
  const [selectedNode, setSelectedNode] = useState(null);
  const [telemetry, setTelemetry] = useState([]);
  const [hours, setHours] = useState(24);
  const batCanvasRef = useRef(null);
  const voltCanvasRef = useRef(null);
  const utilCanvasRef = useRef(null);
  useEffect(() => {
    const nid = selectedNode || nodes[0] && nodes[0].node_id;
    if (nid) {
      api.get(`/api/telemetry?node_id=${nid}&hours=${hours}`).then(setTelemetry);
    }
  }, [selectedNode, nodes, hours]);
  useEffect(() => {
    if (!telemetry.length) return;
    if (batCanvasRef.current) drawSingleChart(batCanvasRef.current, telemetry, 'battery_level', 'Battery %', 'var(--accent-green)', 0, 100);
    if (voltCanvasRef.current) drawSingleChart(voltCanvasRef.current, telemetry, 'voltage', 'Voltage (V)', 'var(--accent-amber)', 2.5, 4.5);
    if (utilCanvasRef.current) drawSingleChart(utilCanvasRef.current, telemetry, 'channel_util', 'Ch Util %', 'var(--accent-cyan)', 0, 50);
  }, [telemetry]);
  const nodesSorted = useMemo(() => [...nodes].sort((a, b) => (a.battery_level || 999) - (b.battery_level || 999)), [nodes]);
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Battery Overview")), /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Node"), /*#__PURE__*/React.createElement("th", null, "Battery"), /*#__PURE__*/React.createElement("th", null, "Voltage"), /*#__PURE__*/React.createElement("th", null, "Uptime"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, nodesSorted.map(n => /*#__PURE__*/React.createElement("tr", {
    key: n.node_id
  }, /*#__PURE__*/React.createElement("td", {
    style: {
      fontWeight: 500,
      color: 'var(--text-primary)'
    }
  }, n.alias || n.long_name), /*#__PURE__*/React.createElement("td", null, n.battery_level != null ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 80
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "battery-bar",
    style: {
      height: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "battery-fill",
    style: {
      width: `${n.battery_level}%`,
      background: batColor(n.battery_level)
    }
  }))), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 13,
      color: batColor(n.battery_level)
    }
  }, n.battery_level, "%")) : '—'), /*#__PURE__*/React.createElement("td", {
    className: "mono"
  }, n.voltage ? `${n.voltage}V` : '—'), /*#__PURE__*/React.createElement("td", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, n.uptime ? `${Math.floor(n.uptime / 3600)}h ${Math.floor(n.uptime % 3600 / 60)}m` : '—'), /*#__PURE__*/React.createElement("td", null, n.battery_level != null && n.battery_level < 20 ? /*#__PURE__*/React.createElement("span", {
    className: "tag tag-red"
  }, "LOW") : n.battery_level != null ? /*#__PURE__*/React.createElement("span", {
    className: "tag tag-green"
  }, "OK") : '—'))))))), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Power Trends"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("select", {
    value: selectedNode || '',
    onChange: e => setSelectedNode(e.target.value),
    style: {
      padding: '5px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 12
    }
  }, nodes.map(n => /*#__PURE__*/React.createElement("option", {
    key: n.node_id,
    value: n.node_id
  }, n.alias || n.long_name))), /*#__PURE__*/React.createElement("div", {
    className: "tab-bar",
    style: {
      margin: 0
    }
  }, [6, 12, 24, 48].map(h => /*#__PURE__*/React.createElement("button", {
    key: h,
    className: `tab-btn ${hours === h ? 'active' : ''}`,
    onClick: () => setHours(h)
  }, h, "h"))))), telemetry.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "power-charts-grid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 8,
      textTransform: 'uppercase',
      letterSpacing: '0.04em'
    }
  }, "Battery Level"), /*#__PURE__*/React.createElement("div", {
    className: "chart-container",
    style: {
      height: 200
    }
  }, /*#__PURE__*/React.createElement("canvas", {
    ref: batCanvasRef
  }))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 8,
      textTransform: 'uppercase',
      letterSpacing: '0.04em'
    }
  }, "Voltage"), /*#__PURE__*/React.createElement("div", {
    className: "chart-container",
    style: {
      height: 200
    }
  }, /*#__PURE__*/React.createElement("canvas", {
    ref: voltCanvasRef
  }))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 8,
      textTransform: 'uppercase',
      letterSpacing: '0.04em'
    }
  }, "Channel Utilization"), /*#__PURE__*/React.createElement("div", {
    className: "chart-container",
    style: {
      height: 200
    }
  }, /*#__PURE__*/React.createElement("canvas", {
    ref: utilCanvasRef
  })))) : /*#__PURE__*/React.createElement("div", {
    className: "empty-state"
  }, "Select a node to view power trends")));
}

// ═══════════════════════════════════════════════
// Alerts Page
// ═══════════════════════════════════════════════
function AlertItem({
  alert,
  onAck
}) {
  const icons = {
    critical: '⛔',
    warning: '⚠️',
    info: 'ℹ️'
  };
  return /*#__PURE__*/React.createElement("div", {
    className: `alert-item alert-${alert.severity}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "alert-icon"
  }, icons[alert.severity] || 'ℹ️'), /*#__PURE__*/React.createElement("div", {
    className: "alert-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "alert-title"
  }, alert.title), /*#__PURE__*/React.createElement("div", {
    className: "alert-msg"
  }, alert.message), /*#__PURE__*/React.createElement("div", {
    className: "alert-time"
  }, fmtTime(alert.timestamp))), !alert.acknowledged && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onAck,
    title: "Acknowledge"
  }, Icons.check, " Ack"));
}
function AlertsPage({
  alerts,
  refresh,
  setAlerts
}) {
  const [filter, setFilter] = useState('all');
  const filtered = alerts.filter(a => {
    if (filter === 'unacked') return !a.acknowledged;
    if (filter === 'critical') return a.severity === 'critical';
    return true;
  });
  const ack = async id => {
    await api.post(`/api/alerts/${id}/ack`);
    if (setAlerts) {
      setAlerts(prev => prev.map(a => a.id === id ? {
        ...a,
        acknowledged: 1
      } : a));
    }
    refresh();
  };
  const ackAll = async () => {
    await api.post('/api/alerts/ack-all');
    if (setAlerts) {
      setAlerts(prev => prev.map(a => ({
        ...a,
        acknowledged: 1
      })));
    }
    refresh();
  };
  const unackedCount = alerts.filter(a => !a.acknowledged).length;
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Alerts (", unackedCount, " active)"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center'
    }
  }, unackedCount > 0 && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: ackAll,
    title: "Acknowledge all active alerts"
  }, Icons.check, " Ack All"), /*#__PURE__*/React.createElement("div", {
    className: "tab-bar",
    style: {
      margin: 0
    }
  }, ['all', 'unacked', 'critical'].map(f => /*#__PURE__*/React.createElement("button", {
    key: f,
    className: `tab-btn ${filter === f ? 'active' : ''}`,
    onClick: () => setFilter(f)
  }, f === 'all' ? 'All' : f === 'unacked' ? 'Active' : 'Critical'))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, filtered.map(a => /*#__PURE__*/React.createElement(AlertItem, {
    key: a.id,
    alert: a,
    onAck: () => ack(a.id)
  })), filtered.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "empty-state"
  }, "No alerts"))));
}

// ═══════════════════════════════════════════════
// Canvas drawing helpers (no chart library needed)
// ═══════════════════════════════════════════════
function drawChart(canvas, telemetry) {
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(2, 2);
  const w = rect.width,
    h = rect.height;
  const pad = {
    top: 20,
    right: 20,
    bottom: 30,
    left: 50
  };
  const cw = w - pad.left - pad.right;
  const ch = h - pad.top - pad.bottom;
  ctx.clearRect(0, 0, w, h);
  const batData = telemetry.filter(t => t.battery_level != null);
  if (!batData.length) return;
  const minT = batData[0].timestamp;
  const maxT = batData[batData.length - 1].timestamp;
  const tRange = maxT - minT || 1;

  // Draw grid
  ctx.strokeStyle = '#1f2a40';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + ch / 4 * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
  }

  // Battery line
  ctx.strokeStyle = '#00e5a0';
  ctx.lineWidth = 2;
  ctx.beginPath();
  batData.forEach((d, i) => {
    const x = pad.left + (d.timestamp - minT) / tRange * cw;
    const y = pad.top + (1 - d.battery_level / 100) * ch;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill under
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch);
  grad.addColorStop(0, 'rgba(0,229,160,0.2)');
  grad.addColorStop(1, 'rgba(0,229,160,0)');
  ctx.fillStyle = grad;
  ctx.lineTo(pad.left + cw, pad.top + ch);
  ctx.lineTo(pad.left, pad.top + ch);
  ctx.closePath();
  ctx.fill();

  // Y labels
  ctx.fillStyle = '#5a6478';
  ctx.font = '11px monospace';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const val = 100 - i * 25;
    const y = pad.top + ch / 4 * i;
    ctx.fillText(`${val}%`, pad.left - 8, y + 4);
  }

  // X labels
  ctx.textAlign = 'center';
  const steps = Math.min(6, batData.length);
  for (let i = 0; i < steps; i++) {
    const idx = Math.floor(i * (batData.length - 1) / (steps - 1));
    const d = batData[idx];
    const x = pad.left + (d.timestamp - minT) / tRange * cw;
    ctx.fillText(new Date(d.timestamp * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    }), x, h - 6);
  }
}
function drawSingleChart(canvas, data, key, label, color, minVal, maxVal) {
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.scale(2, 2);
  const w = rect.width,
    h = rect.height;
  const pad = {
    top: 10,
    right: 10,
    bottom: 25,
    left: 40
  };
  const cw = w - pad.left - pad.right;
  const ch = h - pad.top - pad.bottom;
  ctx.clearRect(0, 0, w, h);
  const filtered = data.filter(d => d[key] != null);
  if (!filtered.length) return;
  const minT = filtered[0].timestamp;
  const maxT = filtered[filtered.length - 1].timestamp;
  const tRange = maxT - minT || 1;
  const vRange = maxVal - minVal || 1;

  // Grid
  ctx.strokeStyle = '#1f2a40';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + ch / 4 * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
  }

  // Line
  const cssColor = color.startsWith('var(') ? getComputedStyle(document.documentElement).getPropertyValue(color.slice(4, -1)).trim() : color;
  ctx.strokeStyle = cssColor;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  filtered.forEach((d, i) => {
    const x = pad.left + (d.timestamp - minT) / tRange * cw;
    const y = pad.top + (1 - (d[key] - minVal) / vRange) * ch;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch);
  grad.addColorStop(0, cssColor + '33');
  grad.addColorStop(1, cssColor + '00');
  ctx.fillStyle = grad;
  ctx.lineTo(pad.left + (filtered[filtered.length - 1].timestamp - minT) / tRange * cw, pad.top + ch);
  ctx.lineTo(pad.left, pad.top + ch);
  ctx.closePath();
  ctx.fill();

  // Labels
  ctx.fillStyle = '#5a6478';
  ctx.font = '10px monospace';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const val = maxVal - i / 4 * vRange;
    ctx.fillText(val.toFixed(key === 'voltage' ? 1 : 0), pad.left - 5, pad.top + ch / 4 * i + 4);
  }
}
function drawTopology(canvas, topo) {
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(2, 2);
  const w = rect.width,
    h = rect.height;
  ctx.clearRect(0, 0, w, h);
  const {
    nodes,
    links
  } = topo;
  if (!nodes.length) return;

  // Layout nodes in a circle
  const cx = w / 2,
    cy = h / 2;
  const radius = Math.min(w, h) * 0.35;
  const positions = {};
  nodes.forEach((n, i) => {
    const angle = 2 * Math.PI * i / nodes.length - Math.PI / 2;
    positions[n.node_id] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      node: n
    };
  });

  // Draw links
  links.forEach(l => {
    const from = positions[l.from_id];
    const to = positions[l.to_id];
    if (!from || !to) return;
    const snr = l.snr || 0;
    const alpha = Math.max(0.15, Math.min(1, (snr + 5) / 20));
    ctx.strokeStyle = `rgba(59,130,246,${alpha})`;
    ctx.lineWidth = Math.max(0.5, snr > 0 ? 1.5 : 0.8);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();

    // SNR label at midpoint
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2;
    if (l.snr != null) {
      ctx.font = '10px monospace';
      ctx.fillStyle = '#5a6478';
      ctx.textAlign = 'center';
      ctx.fillText(`${l.snr.toFixed(1)}dB`, mx, my - 4);
    }
  });

  // Draw nodes
  nodes.forEach(n => {
    const pos = positions[n.node_id];
    if (!pos) return;
    const online = n.last_heard && Date.now() / 1000 - n.last_heard < 900;
    const color = online ? '#00e5a0' : '#ef4444';

    // Glow
    ctx.shadowColor = color;
    ctx.shadowBlur = 12;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Inner
    ctx.fillStyle = '#0a0e17';
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 3, 0, Math.PI * 2);
    ctx.fill();

    // Label
    ctx.font = '12px system-ui,sans-serif';
    ctx.fillStyle = '#e2e8f0';
    ctx.textAlign = 'center';
    ctx.fillText(n.short_name || n.long_name, pos.x, pos.y - 16);
    ctx.font = '10px monospace';
    ctx.fillStyle = '#5a6478';
    ctx.fillText(n.role || '', pos.x, pos.y + 22);
  });
}

// ═══════════════════════════════════════════════
// AI Page — RAG + Multi-Chat
// ═══════════════════════════════════════════════
function AIPage() {
  var _status$loaded_models, _status$doc_count, _settings$temperature, _settings$top_p, _settings$top_k, _settings$num_predict, _settings$num_ctx, _settings$num_gpu, _settings$num_thread, _settings$num_batch;
  const [status, setStatus] = useState(null);
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('chat');
  const [documents, setDocuments] = useState([]);
  const [settings, setSettings] = useState({});
  const [newDoc, setNewDoc] = useState({
    title: '',
    content: '',
    tags: ''
  });
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [warmingUp, setWarmingUp] = useState(false);
  const [deletingChatId, setDeletingChatId] = useState(null);
  const [creatingChat, setCreatingChat] = useState(false);
  const chatEndRef = useRef(null);
  const aiReady = !!(status !== null && status !== void 0 && status.running && status !== null && status !== void 0 && status.ai_ready);

  // ── Data fetching ──────────────────────────────
  function loadStatus() {
    return api.get('/api/ai/status').then(s => {
      setStatus(s);
      setWarmingUp(!!(s !== null && s !== void 0 && s.warming_up));
      return s;
    });
  }
  function loadChats() {
    return api.get('/api/ai/chats').then(setChats);
  }
  function loadDocs() {
    return api.get('/api/ai/documents').then(setDocuments);
  }
  function loadSettings() {
    return api.get('/api/ai/settings').then(setSettings);
  }
  function loadMessages(chatId) {
    if (!chatId || String(chatId).startsWith('pending-')) {
      setMessages([]);
      return;
    }
    api.get(`/api/ai/chats/${chatId}`).then(c => setMessages(c.messages || []));
  }

  // ── Mount ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([loadStatus(), loadChats(), loadDocs(), loadSettings()]).then(results => {
      if (cancelled) return;
      const chatsResult = results[1];
      const chatList = chatsResult && chatsResult.status === 'fulfilled' && Array.isArray(chatsResult.value) ? chatsResult.value : [];
      if (chatList.length === 0) {
        api.post('/api/ai/chats', {}).then(chat => {
          if (cancelled) return;
          setChats([chat]);
          setActiveChatId(chat.id);
        }).catch(() => {});
      } else {
        setActiveChatId(chatList[0].id);
        loadMessages(chatList[0].id);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (aiReady) return;
    const poll = setInterval(() => {
      loadStatus().catch(() => {});
    }, 2000);
    return () => clearInterval(poll);
  }, [aiReady]);

  // Re-load when active chat changes
  useEffect(() => {
    loadMessages(activeChatId);
  }, [activeChatId]);

  // Scroll to bottom on new messages
  useEffect(() => {
    var _chatEndRef$current;
    (_chatEndRef$current = chatEndRef.current) === null || _chatEndRef$current === void 0 || _chatEndRef$current.scrollIntoView({
      behavior: 'smooth'
    });
  }, [messages, loading]);

  // ── Actions ────────────────────────────────────
  async function createChat() {
    if (creatingChat) return;
    setCreatingChat(true);
    try {
      const chat = await api.post('/api/ai/chats', {});
      setChats(prev => [chat, ...prev]);
      setActiveChatId(chat.id);
      setMessages([]);
    } finally {
      setCreatingChat(false);
    }
  }
  async function deleteChat(id) {
    if (!id || deletingChatId === id) return;
    setDeletingChatId(id);
    const wasActive = activeChatId === id;
    try {
      await api.del(`/api/ai/chats/${id}`);
      const updated = await api.get('/api/ai/chats');
      setChats(updated);
      if (wasActive) {
        const nextChat = updated[0] || null;
        setActiveChatId(nextChat ? nextChat.id : null);
        setMessages([]);
        if (nextChat) loadMessages(nextChat.id);
      }
    } finally {
      setDeletingChatId(null);
    }
  }
  async function sendMessage() {
    const text = input.trim();
    if (!text || loading || !activeChatId || String(activeChatId).startsWith('pending-') || !aiReady) return;
    setInput('');
    setLoading(true);
    const tempUserId = Date.now();
    const tempAssistantId = Date.now() + 1;
    const tempUser = {
      id: tempUserId,
      role: 'user',
      content: text,
      created_at: Date.now() / 1000
    };
    const tempAssistant = {
      id: tempAssistantId,
      role: 'assistant',
      content: '',
      streaming: true,
      created_at: Date.now() / 1000
    };
    setMessages(prev => [...prev, tempUser, tempAssistant]);
    try {
      let response = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        response = await fetch(`/api/ai/chats/${activeChatId}/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            text
          })
        });
        if (response.ok || response.status !== 503 || attempt === 2) break;
        setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
          ...m,
          content: 'Ray is still starting. Retrying…',
          streaming: true
        } : m));
        await loadStatus().catch(() => {});
        await new Promise(resolve => setTimeout(resolve, attempt === 0 ? 2000 : 4000));
      }
      if (!response.ok) {
        const err = await response.json().catch(() => ({
          error: response.statusText
        }));
        setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
          ...m,
          content: `Error: ${err.error || response.statusText}`,
          streaming: false
        } : m));
        setLoading(false);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let streamingContent = '';
      while (true) {
        const {
          done,
          value
        } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {
          stream: true
        });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.replace !== undefined) {
              // Full content replacement after server-side calc-tag processing
              streamingContent = data.replace;
              setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
                ...m,
                content: streamingContent
              } : m));
            } else if (data.token !== undefined) {
              streamingContent += data.token;
              setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
                ...m,
                content: streamingContent
              } : m));
            } else if (data.error) {
              setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
                ...m,
                content: `Error: ${data.error}`,
                streaming: false
              } : m));
            }
          } catch (e) {}
        }
      }
      setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
        ...m,
        streaming: false
      } : m));
      loadMessages(activeChatId);
      loadChats();
    } catch (e) {
      setMessages(prev => prev.map(m => m.id === tempAssistantId ? {
        ...m,
        content: `Connection error: ${e.message}`,
        streaming: false
      } : m));
    }
    setLoading(false);
  }
  async function addDocument() {
    const {
      title,
      content,
      tags
    } = newDoc;
    if (!title.trim() || !content.trim()) return;
    await api.post('/api/ai/documents', {
      title: title.trim(),
      content: content.trim(),
      tags: tags.trim()
    });
    setNewDoc({
      title: '',
      content: '',
      tags: ''
    });
    loadDocs();
  }
  async function deleteDocument(id) {
    await api.del(`/api/ai/documents/${id}`);
    loadDocs();
  }
  async function saveSettings() {
    setSavingSettings(true);
    setSaveError(null);
    try {
      const result = await api.put('/api/ai/settings', settings);
      // Sync state with what the server actually persisted
      if (result !== null && result !== void 0 && result.settings) setSettings(result.settings);
      setSettingsSaved(true);
      setTimeout(() => setSettingsSaved(false), 2000);
      if (result !== null && result !== void 0 && result.warming_up) {
        setWarmingUp(true);
        loadStatus();
        const targetModel = settings.model;
        const poll = setInterval(() => {
          loadStatus().then(s => {
            const loaded = (s === null || s === void 0 ? void 0 : s.loaded_models) || [];
            if (loaded.some(m => m === targetModel || m.startsWith(targetModel + ':'))) {
              setWarmingUp(false);
              clearInterval(poll);
            }
          });
        }, 3000);
        setTimeout(() => {
          setWarmingUp(false);
          clearInterval(poll);
        }, 120000);
      }
    } catch (err) {
      setSaveError(err.message || 'Save failed');
    } finally {
      setSavingSettings(false);
    }
  }

  // ── Helpers ────────────────────────────────────
  const {
    timezone: tzSetting
  } = useSettings();
  function fmtChatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const tz = resolveTz(tzSetting);
    return d.toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
      timeZone: tz
    }) + ' ' + d.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: tz
    });
  }

  // ── Render ─────────────────────────────────────
  const tabBtnStyle = active => ({
    padding: '6px 14px',
    borderRadius: 'var(--radius)',
    border: 'none',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 500,
    background: active ? 'var(--accent-green-dim)' : 'transparent',
    color: active ? 'var(--accent-green)' : 'var(--text-muted)',
    transition: 'all 200ms'
  });
  const inputStyle = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
    borderRadius: 'var(--radius)',
    padding: '6px 10px',
    fontSize: 13,
    width: '100%'
  };
  const labelStyle = {
    fontSize: 12,
    color: 'var(--text-muted)',
    marginBottom: 4,
    display: 'block'
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "page ai-layout"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ai-sidebar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ai-sidebar-header"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      width: '100%'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    style: {
      flex: 1,
      justifyContent: 'center',
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      opacity: creatingChat ? 0.6 : 1
    },
    onClick: () => {
      createChat();
    },
    disabled: creatingChat
  }, /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5",
    style: {
      pointerEvents: 'none'
    }
  }, /*#__PURE__*/React.createElement("line", {
    x1: "12",
    y1: "5",
    x2: "12",
    y2: "19"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "5",
    y1: "12",
    x2: "19",
    y2: "12"
  })), creatingChat ? 'Creating…' : 'New Chat'))), /*#__PURE__*/React.createElement("div", {
    className: "ai-sidebar-body"
  }, chats.length === 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 12,
      textAlign: 'center',
      marginTop: 20
    }
  }, "No chats yet"), chats.map(chat => /*#__PURE__*/React.createElement("div", {
    key: chat.id,
    className: "ai-chat-item",
    onClick: () => {
      setActiveChatId(chat.id);
      setTab('chat');
    },
    style: {
      background: activeChatId === chat.id ? 'var(--bg-input)' : 'transparent',
      borderLeft: activeChatId === chat.id ? '3px solid var(--accent-green)' : '3px solid transparent'
    },
    onMouseEnter: e => {
      if (activeChatId !== chat.id) e.currentTarget.style.background = 'var(--bg-card)';
    },
    onMouseLeave: e => {
      if (activeChatId !== chat.id) e.currentTarget.style.background = 'transparent';
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: 'var(--text-primary)',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis'
    }
  }, chat.title || 'New Chat'), /*#__PURE__*/React.createElement("div", {
    className: "ai-chat-ts",
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 2
    }
  }, fmtChatTime(chat.updated_at))), /*#__PURE__*/React.createElement("button", {
    type: "button",
    onPointerDown: e => e.stopPropagation(),
    onMouseDown: e => e.stopPropagation(),
    onClick: e => {
      e.stopPropagation();
      e.preventDefault();
      deleteChat(chat.id);
    },
    style: {
      background: 'transparent',
      border: '1px solid transparent',
      cursor: deletingChatId === chat.id ? 'default' : 'pointer',
      color: 'var(--accent-red)',
      padding: '7px',
      borderRadius: 10,
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minWidth: 36,
      minHeight: 36,
      opacity: deletingChatId === chat.id ? 0.4 : 0.9
    },
    title: "Delete chat",
    "aria-label": "Delete chat",
    disabled: deletingChatId === chat.id
  }, /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    style: {
      pointerEvents: 'none'
    }
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "3 6 5 6 21 6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M10 11v6m4-6v6"
  })))))), /*#__PURE__*/React.createElement("div", {
    className: "ai-sidebar-footer"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      width: 7,
      height: 7,
      borderRadius: '50%',
      display: 'inline-block',
      background: status !== null && status !== void 0 && status.running ? 'var(--accent-green)' : 'var(--accent-red)',
      flexShrink: 0
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, status === null ? 'Checking…' : status.running ? `Ollama · ${status.doc_count || 0} docs` : 'Ollama offline')))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      padding: '16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 14,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 17,
      fontWeight: 700,
      color: 'var(--text-primary)'
    }
  }, "Ray"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 1
    }
  }, warmingUp ? `Loading ${settings.model || 'qwen3.5:2b'}…` : aiReady ? `${settings.model || 'qwen3.5:2b'} · RAG ${settings.rag_enabled === 'true' ? 'on' : 'off'} · ${(status === null || status === void 0 ? void 0 : status.doc_count) || 0} docs` : status !== null && status !== void 0 && status.running ? `Starting Ray… ${(status === null || status === void 0 ? void 0 : status.startup_phase) || 'warming_up'}` : 'Ollama not reachable')), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 4
    }
  }, ['chat', 'kb', 'settings'].map(t => /*#__PURE__*/React.createElement("button", {
    key: t,
    style: tabBtnStyle(tab === t),
    onClick: () => setTab(t)
  }, t === 'chat' ? 'Chat' : t === 'kb' ? 'Knowledge Base' : 'Settings')))), tab === 'chat' && /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      minHeight: 0
    }
  }, !activeChatId ? /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: 'var(--text-muted)',
      fontSize: 14
    }
  }, "Select or create a chat") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      padding: 16,
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, messages.length === 0 && !loading && /*#__PURE__*/React.createElement("div", {
    style: {
      textAlign: 'center',
      color: 'var(--text-muted)',
      fontSize: 13,
      marginTop: 32
    }
  }, aiReady ? 'Start the conversation below.' : 'Ray is starting up. Chat will unlock automatically when ready.'), messages.map((m, i) => {
    // Split confidence footer out of assistant message content
    let msgBody = m.content || '';
    let confLabel = null;
    if (m.role === 'assistant') {
      const confMatch = msgBody.match(/\n\n---\nConfidence:\s*(.+)$/s);
      if (confMatch) {
        confLabel = confMatch[1].trim();
        msgBody = msgBody.slice(0, confMatch.index).trimEnd();
      }
    }
    const confTier = confLabel ? confLabel.split('|')[0].trim() : null;
    const confColor = confTier === 'HIGH' ? 'var(--accent-green)' : confTier === 'MEDIUM' ? '#f59e0b' : '#ef4444';
    return /*#__PURE__*/React.createElement("div", {
      key: m.id || i,
      style: {
        display: 'flex',
        flexDirection: 'column',
        alignItems: m.role === 'user' ? 'flex-end' : 'flex-start',
        gap: 3
      }
    }, m.role === 'assistant' ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "card",
      style: {
        maxWidth: '78%',
        padding: '10px 14px',
        background: 'var(--bg-input)',
        border: '1px solid var(--border)',
        borderRadius: '4px 12px 12px 12px',
        fontSize: 14,
        lineHeight: 1.65,
        wordBreak: 'break-word',
        color: 'var(--text-primary)'
      }
    }, /*#__PURE__*/React.createElement(MarkdownMessage, {
      text: msgBody
    }), m.streaming && /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'inline-block',
        width: 2,
        height: '1em',
        background: 'var(--accent-green)',
        marginLeft: 2,
        verticalAlign: 'text-bottom',
        animation: 'blink 1s step-end infinite'
      }
    })), confLabel && /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 5,
        paddingLeft: 4
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: confColor,
        flexShrink: 0
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 10,
        color: 'var(--text-muted)',
        fontFamily: 'monospace'
      }
    }, confLabel)), (m.tokens || m.duration_ms) && /*#__PURE__*/React.createElement("div", {
      className: "mono",
      style: {
        fontSize: 10,
        color: 'var(--text-muted)',
        paddingLeft: 4
      }
    }, m.tokens && m.duration_ms ? `${(m.tokens / (m.duration_ms / 1000)).toFixed(1)} tok/s · ` : '', m.duration_ms ? `${(m.duration_ms / 1000).toFixed(1)}s` : '')) : /*#__PURE__*/React.createElement("div", {
      style: {
        maxWidth: '78%',
        padding: '8px 12px',
        fontSize: 14,
        lineHeight: 1.65,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        color: 'var(--text-primary)'
      }
    }, m.content));
  }), loading && !messages.some(m => m.streaming) && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderRadius: '4px 12px 12px 12px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      color: 'var(--text-muted)',
      fontSize: 14,
      fontStyle: 'italic'
    }
  }, "Thinking\u2026")), /*#__PURE__*/React.createElement("div", {
    ref: chatEndRef
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      borderTop: '1px solid var(--border)',
      padding: '10px 14px',
      display: 'flex',
      gap: 8,
      alignItems: 'center',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("input", {
    value: input,
    onChange: e => setInput(e.target.value),
    onKeyDown: e => e.key === 'Enter' && !e.shiftKey && sendMessage(),
    placeholder: !(status !== null && status !== void 0 && status.running) ? 'Ollama not connected' : !aiReady ? 'Ray is starting up…' : 'Send a message…',
    disabled: !aiReady || loading,
    style: {
      flex: 1,
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      padding: '10px 14px',
      fontSize: 14,
      fontFamily: 'var(--font-sans)',
      outline: 'none'
    },
    onFocus: e => e.target.style.borderColor = 'var(--accent-green)',
    onBlur: e => e.target.style.borderColor = 'var(--border)'
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: sendMessage,
    disabled: !aiReady || loading || !input.trim()
  }, Icons.send)))), tab === 'kb' && /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto',
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 0,
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderBottom: '1px solid var(--border)',
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, "Knowledge Base \u2014 ", documents.length, " document", documents.length !== 1 ? 's' : ''), documents.length === 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 20,
      color: 'var(--text-muted)',
      fontSize: 13,
      textAlign: 'center'
    }
  }, "No documents yet") : /*#__PURE__*/React.createElement("div", {
    style: {
      maxHeight: 420,
      overflowY: 'auto'
    }
  }, documents.map(doc => /*#__PURE__*/React.createElement("div", {
    key: doc.id,
    style: {
      padding: '10px 16px',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      gap: 12,
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 13,
      color: 'var(--text-primary)',
      marginBottom: 4
    }
  }, doc.title), doc.tags && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 4,
      marginBottom: 5
    }
  }, doc.tags.split(',').map(t => t.trim()).filter(Boolean).map(t => /*#__PURE__*/React.createElement("span", {
    key: t,
    className: "tag tag-blue",
    style: {
      fontSize: 10
    }
  }, t))), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      lineHeight: 1.5
    }
  }, doc.content.slice(0, 100), doc.content.length > 100 ? '…' : '')), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'flex-end',
      gap: 6,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: `tag ${doc.is_seed ? 'tag-green' : 'tag-blue'}`,
    style: {
      fontSize: 10
    }
  }, doc.is_seed ? 'Seed' : 'Custom'), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      color: 'var(--accent-red)',
      border: '1px solid var(--accent-red)',
      background: 'transparent',
      padding: '2px 8px'
    },
    onClick: () => deleteDocument(doc.id)
  }, "Delete")))))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-primary)',
      marginBottom: 12
    }
  }, "Add Knowledge"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Title"), /*#__PURE__*/React.createElement("input", {
    style: inputStyle,
    value: newDoc.title,
    onChange: e => setNewDoc(p => ({
      ...p,
      title: e.target.value
    })),
    placeholder: "Document title"
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Tags (comma-separated)"), /*#__PURE__*/React.createElement("input", {
    style: inputStyle,
    value: newDoc.tags,
    onChange: e => setNewDoc(p => ({
      ...p,
      tags: e.target.value
    })),
    placeholder: "e.g. survival, water, medical"
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Content"), /*#__PURE__*/React.createElement("textarea", {
    style: {
      ...inputStyle,
      resize: 'vertical',
      minHeight: 100,
      fontFamily: 'inherit'
    },
    value: newDoc.content,
    onChange: e => setNewDoc(p => ({
      ...p,
      content: e.target.value
    })),
    placeholder: "Document content\u2026",
    rows: 5
  })), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    style: {
      alignSelf: 'flex-start'
    },
    onClick: addDocument,
    disabled: !newDoc.title.trim() || !newDoc.content.trim()
  }, "Add Document")))), tab === 'settings' && /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      overflowY: 'auto'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 20,
      display: 'flex',
      flexDirection: 'column',
      gap: 20
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      marginBottom: 12
    }
  }, "Current Runtime"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "Active Chat Model"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)'
    }
  }, settings.model || '—')), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "Loaded In Ollama"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)'
    }
  }, status !== null && status !== void 0 && (_status$loaded_models = status.loaded_models) !== null && _status$loaded_models !== void 0 && _status$loaded_models.length ? status.loaded_models.join(', ') : 'None loaded')), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "Embedding Model"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)'
    }
  }, settings.embed_model || '—')), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "RAG / Docs"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)'
    }
  }, settings.rag_enabled === 'true' ? 'Enabled' : 'Disabled', " \xB7 ", (_status$doc_count = status === null || status === void 0 ? void 0 : status.doc_count) !== null && _status$doc_count !== void 0 ? _status$doc_count : 0, " docs")))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      marginBottom: 12
    }
  }, "Model"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Chat Model"), status !== null && status !== void 0 && status.models && status.models.length > 0 ? /*#__PURE__*/React.createElement("select", {
    value: settings.model || '',
    onChange: e => setSettings(p => ({
      ...p,
      model: e.target.value
    })),
    style: {
      ...inputStyle,
      fontFamily: 'var(--font-mono)'
    }
  }, settings.model && !status.models.includes(settings.model) && /*#__PURE__*/React.createElement("option", {
    value: settings.model
  }, settings.model, " (not in Ollama)"), status.models.map(m => /*#__PURE__*/React.createElement("option", {
    key: m,
    value: m
  }, m))) : /*#__PURE__*/React.createElement("input", {
    style: inputStyle,
    value: settings.model || '',
    onChange: e => setSettings(p => ({
      ...p,
      model: e.target.value
    })),
    placeholder: "qwen3.5:2b"
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Embed Model"), /*#__PURE__*/React.createElement("input", {
    style: {
      ...inputStyle,
      fontFamily: 'var(--font-mono)'
    },
    value: settings.embed_model || '',
    onChange: e => setSettings(p => ({
      ...p,
      embed_model: e.target.value
    })),
    placeholder: "qwen3-embedding:0.6b"
  })))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      marginBottom: 12
    }
  }, "Behavior"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "System Prompt"), /*#__PURE__*/React.createElement("textarea", {
    style: {
      ...inputStyle,
      resize: 'vertical',
      fontFamily: 'inherit'
    },
    rows: 4,
    value: settings.system_prompt || '',
    onChange: e => setSettings(p => ({
      ...p,
      system_prompt: e.target.value
    }))
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    id: "warmup",
    checked: settings.warmup_on_start === 'true',
    onChange: e => setSettings(p => ({
      ...p,
      warmup_on_start: e.target.checked ? 'true' : 'false'
    }))
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "warmup",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)',
      cursor: 'pointer'
    }
  }, "Warmup model on startup")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Keep-Alive Hours (1\u201324)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "24",
    style: {
      ...inputStyle,
      width: 80
    },
    value: settings.keep_alive_hours || '10',
    onChange: e => setSettings(p => ({
      ...p,
      keep_alive_hours: e.target.value
    }))
  })))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      marginBottom: 12
    }
  }, "Retrieval (RAG)"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    id: "rag_enabled",
    checked: settings.rag_enabled === 'true',
    onChange: e => setSettings(p => ({
      ...p,
      rag_enabled: e.target.checked ? 'true' : 'false'
    }))
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "rag_enabled",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)',
      cursor: 'pointer'
    }
  }, "Enable RAG (inject relevant docs into context)")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Top-K Documents (1\u201310)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "10",
    style: {
      ...inputStyle,
      width: 80
    },
    value: settings.rag_top_k || '3',
    onChange: e => setSettings(p => ({
      ...p,
      rag_top_k: e.target.value
    }))
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    id: "mesh_ctx",
    checked: settings.inject_mesh_context === 'true',
    onChange: e => setSettings(p => ({
      ...p,
      inject_mesh_context: e.target.checked ? 'true' : 'false'
    }))
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "mesh_ctx",
    style: {
      fontSize: 13,
      color: 'var(--text-primary)',
      cursor: 'pointer'
    }
  }, "Inject mesh network context (nodes + recent messages)")))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      marginBottom: 12
    }
  }, "Inference"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Temperature (0\u20132)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "0",
    max: "2",
    step: "0.05",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$temperature = settings.temperature) !== null && _settings$temperature !== void 0 ? _settings$temperature : '0.3',
    onChange: e => setSettings(p => ({
      ...p,
      temperature: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Lower = focused, higher = creative")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Top-P (0\u20131)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "0",
    max: "1",
    step: "0.05",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$top_p = settings.top_p) !== null && _settings$top_p !== void 0 ? _settings$top_p : '0.9',
    onChange: e => setSettings(p => ({
      ...p,
      top_p: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Nucleus sampling cutoff")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Top-K (1\u2013100)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "100",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$top_k = settings.top_k) !== null && _settings$top_k !== void 0 ? _settings$top_k : '40',
    onChange: e => setSettings(p => ({
      ...p,
      top_k: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Token candidates per step")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Max Response Tokens"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "64",
    max: "4096",
    step: "64",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$num_predict = settings.num_predict) !== null && _settings$num_predict !== void 0 ? _settings$num_predict : '512',
    onChange: e => setSettings(p => ({
      ...p,
      num_predict: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Max tokens generated per reply"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Context Window (num_ctx)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "512",
    max: "8192",
    step: "512",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$num_ctx = settings.num_ctx) !== null && _settings$num_ctx !== void 0 ? _settings$num_ctx : '4096',
    onChange: e => setSettings(p => ({
      ...p,
      num_ctx: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Lower = faster responses")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "GPU Layers (num_gpu)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "0",
    max: "99",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$num_gpu = settings.num_gpu) !== null && _settings$num_gpu !== void 0 ? _settings$num_gpu : '99',
    onChange: e => setSettings(p => ({
      ...p,
      num_gpu: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "99 = all layers on GPU"))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "CPU Threads (num_thread)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "32",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$num_thread = settings.num_thread) !== null && _settings$num_thread !== void 0 ? _settings$num_thread : '6',
    onChange: e => setSettings(p => ({
      ...p,
      num_thread: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Inference worker threads")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: labelStyle
  }, "Batch Size (num_batch)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "2048",
    step: "1",
    style: {
      ...inputStyle,
      width: '100%'
    },
    value: (_settings$num_batch = settings.num_batch) !== null && _settings$num_batch !== void 0 ? _settings$num_batch : '512',
    onChange: e => setSettings(p => ({
      ...p,
      num_batch: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 3
    }
  }, "Prompt processing batch size"))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: saveSettings,
    disabled: savingSettings || warmingUp
  }, savingSettings ? 'Saving…' : 'Save Settings'), settingsSaved && !warmingUp && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 13,
      fontWeight: 500
    }
  }, "Saved!"), warmingUp && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent)',
      fontSize: 13,
      fontWeight: 500
    }
  }, "Loading model\u2026"), saveError && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-red, #f87171)',
      fontSize: 13,
      fontWeight: 500
    }
  }, "Error: ", saveError))))));
}

// ═══════════════════════════════════════════════
// System Stats Page
// ═══════════════════════════════════════════════
function SystemPage() {
  var _stats$cpu_pct, _stats$gpu_pct, _stats$ram_pct, _stats$process_count, _stats$disk_read_mbps, _stats$disk_write_mbp;
  const {
    units
  } = useSettings();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const loadingRef = useRef(false);
  const statsRef = useRef(null);
  useEffect(() => {
    statsRef.current = stats;
  }, [stats]);
  const load = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      const s = await api.get('/api/system/stats');
      setStats(s);
      setError(null);
    } catch (e) {
      if (!statsRef.current) setError('Failed to load system stats');
    } finally {
      loadingRef.current = false;
    }
  }, []);
  useEffect(() => {
    load();
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
  }, [load]);
  function fmtUptime(s) {
    if (!s) return '—';
    const d = Math.floor(s / 86400),
      h = Math.floor(s % 86400 / 3600),
      m = Math.floor(s % 3600 / 60);
    return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  function Bar({
    pct,
    color
  }) {
    const c = color || (pct > 85 ? 'var(--accent-red)' : pct > 60 ? 'var(--accent-amber)' : 'var(--accent-green)');
    return /*#__PURE__*/React.createElement("div", {
      style: {
        height: 6,
        background: 'var(--border)',
        borderRadius: 3,
        overflow: 'hidden'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        height: '100%',
        width: `${Math.min(pct, 100)}%`,
        background: c,
        borderRadius: 3,
        transition: 'width 0.5s'
      }
    }));
  }
  function StatCard({
    label,
    value,
    sub,
    pct,
    color
  }) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card",
      style: {
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.08em'
      }
    }, label), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 24,
        fontWeight: 700,
        color: 'var(--text-primary)',
        fontFamily: 'var(--font-mono)'
      }
    }, value), sub && /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)'
      }
    }, sub), pct != null && /*#__PURE__*/React.createElement(Bar, {
      pct: pct,
      color: color
    }));
  }
  function TempBadge({
    label,
    val
  }) {
    if (val == null) return null;
    const displayVal = units === 'imperial' ? (val * 9 / 5 + 32).toFixed(1) : val;
    const unit = units === 'imperial' ? '°F' : '°C';
    const color = val > 80 ? 'var(--accent-red)' : val > 65 ? 'var(--accent-amber)' : 'var(--accent-green)';
    return /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 4,
        padding: '10px 14px',
        background: 'var(--bg-input)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 10,
        color: 'var(--text-muted)',
        textTransform: 'uppercase'
      }
    }, label), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 18,
        fontWeight: 700,
        color,
        fontFamily: 'var(--font-mono)'
      }
    }, displayVal, unit));
  }
  if (error) return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      padding: 16
    }
  }, error));
  if (!stats) return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      padding: 16
    }
  }, "Loading\u2026"));
  const cpuCores = stats.cpu_cores || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "page",
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "sys-stats-grid"
  }, /*#__PURE__*/React.createElement(StatCard, {
    label: "CPU",
    value: `${(_stats$cpu_pct = stats.cpu_pct) !== null && _stats$cpu_pct !== void 0 ? _stats$cpu_pct : '—'}%`,
    sub: `${cpuCores.length} cores`,
    pct: stats.cpu_pct
  }), /*#__PURE__*/React.createElement(StatCard, {
    label: "GPU",
    value: `${(_stats$gpu_pct = stats.gpu_pct) !== null && _stats$gpu_pct !== void 0 ? _stats$gpu_pct : '—'}%`,
    sub: "GR3D",
    pct: stats.gpu_pct,
    color: stats.gpu_pct > 85 ? 'var(--accent-red)' : stats.gpu_pct > 60 ? 'var(--accent-amber)' : 'var(--accent-blue)'
  }), /*#__PURE__*/React.createElement(StatCard, {
    label: "RAM",
    value: `${(_stats$ram_pct = stats.ram_pct) !== null && _stats$ram_pct !== void 0 ? _stats$ram_pct : '—'}%`,
    sub: stats.ram_used_mb != null ? `${stats.ram_used_mb} / ${stats.ram_total_mb} MB` : '',
    pct: stats.ram_pct
  }), /*#__PURE__*/React.createElement(StatCard, {
    label: "Uptime",
    value: fmtUptime(stats.uptime_s),
    sub: `${(_stats$process_count = stats.process_count) !== null && _stats$process_count !== void 0 ? _stats$process_count : '—'} processes`
  })), /*#__PURE__*/React.createElement("div", {
    className: "sys-two-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 10,
      color: 'var(--text-primary)'
    }
  }, "CPU Cores"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, cpuCores.map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      width: 20
    }
  }, "C", i), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(Bar, {
    pct: c.pct
  })), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--text-primary)',
      width: 32,
      textAlign: 'right'
    }
  }, c.pct, "%"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      width: 52,
      textAlign: 'right'
    }
  }, c.freq_mhz, "MHz"))))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 10,
      color: 'var(--text-primary)'
    }
  }, "Memory"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "RAM"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, stats.ram_used_mb, " / ", stats.ram_total_mb, " MB")), /*#__PURE__*/React.createElement(Bar, {
    pct: stats.ram_pct
  })), stats.swap_total_mb > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "Swap"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, stats.swap_used_mb, " / ", stats.swap_total_mb, " MB")), /*#__PURE__*/React.createElement(Bar, {
    pct: stats.swap_pct,
    color: "var(--accent-amber)"
  })))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, "Storage"), stats.disk_health && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      padding: '2px 8px',
      borderRadius: 4,
      background: stats.disk_health === 'PASSED' ? 'rgba(74,222,128,0.15)' : 'rgba(239,68,68,0.15)',
      color: stats.disk_health === 'PASSED' ? 'var(--accent-green)' : 'var(--accent-red)',
      border: `1px solid ${stats.disk_health === 'PASSED' ? 'var(--accent-green)' : 'var(--accent-red)'}`
    }
  }, stats.disk_health)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "System (eMMC)"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, stats.disk_used_gb, " / ", stats.disk_total_gb, " GB")), /*#__PURE__*/React.createElement(Bar, {
    pct: stats.disk_pct
  })), stats.nvme_total_gb != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "Data (NVMe)"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, stats.nvme_used_gb, " / ", stats.nvme_total_gb, " GB")), /*#__PURE__*/React.createElement(Bar, {
    pct: stats.nvme_pct
  })), (stats.disk_read_mbps != null || stats.disk_write_mbps != null) && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 8,
      marginTop: 2
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '8px 10px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      marginBottom: 2
    }
  }, "NVMe Read"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 14,
      fontWeight: 600,
      color: 'var(--accent-cyan)'
    }
  }, (_stats$disk_read_mbps = stats.disk_read_mbps) !== null && _stats$disk_read_mbps !== void 0 ? _stats$disk_read_mbps : '—', " MB/s")), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '8px 10px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      marginBottom: 2
    }
  }, "NVMe Write"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 14,
      fontWeight: 600,
      color: 'var(--accent-amber)'
    }
  }, (_stats$disk_write_mbp = stats.disk_write_mbps) !== null && _stats$disk_write_mbp !== void 0 ? _stats$disk_write_mbp : '—', " MB/s"))), stats.disk_life_pct_used != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      marginBottom: 4
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "NVMe Life Used"), /*#__PURE__*/React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, stats.disk_life_pct_used, "%")), /*#__PURE__*/React.createElement(Bar, {
    pct: stats.disk_life_pct_used,
    color: "var(--accent-amber)"
  })), (stats.disk_power_on_hours != null || stats.disk_total_written_tb != null || stats.disk_total_read_tb != null) && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 16,
      flexWrap: 'wrap'
    }
  }, stats.disk_power_on_hours != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)'
    }
  }, "Power-on"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 12,
      fontWeight: 600
    }
  }, stats.disk_power_on_hours.toLocaleString(), " hrs")), stats.disk_total_written_tb != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)'
    }
  }, "Total Written"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 12,
      fontWeight: 600
    }
  }, stats.disk_total_written_tb, " TB")), stats.disk_total_read_tb != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)'
    }
  }, "Total Read"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 12,
      fontWeight: 600
    }
  }, stats.disk_total_read_tb, " TB"))))))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 10,
      color: 'var(--text-primary)'
    }
  }, "Temperatures"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement(TempBadge, {
    label: "CPU",
    val: stats.temp_cpu
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "GPU",
    val: stats.temp_gpu
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "Hotspot",
    val: stats.temp_tj
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "CPU Cores",
    val: stats.temp_soc0
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "I/O & Video",
    val: stats.temp_soc1
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "Memory Ctrl",
    val: stats.temp_soc2
  }), /*#__PURE__*/React.createElement(TempBadge, {
    label: "M.2 Drive",
    val: stats.disk_temp_c
  }))), stats.power_in_mw != null && /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 10,
      color: 'var(--text-primary)'
    }
  }, "Power Consumption"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(3,1fr)',
      gap: 10
    }
  }, [['Total Input', stats.power_in_mw], ['CPU + GPU + CV', stats.power_cpu_gpu_mw], ['SOC', stats.power_soc_mw]].map(([label, mw]) => mw != null && /*#__PURE__*/React.createElement("div", {
    key: label,
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '10px 14px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      marginBottom: 4
    }
  }, label), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--accent-cyan)'
    }
  }, (mw / 1000).toFixed(2), "W"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)'
    }
  }, mw, " mW"))))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '14px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 10,
      color: 'var(--text-primary)'
    }
  }, "Network (cumulative)"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 24
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "Sent"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 16,
      fontWeight: 600
    }
  }, stats.net_sent_mb, " MB")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "Received"), /*#__PURE__*/React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 16,
      fontWeight: 600
    }
  }, stats.net_recv_mb, " MB")))));
}

// ═══════════════════════════════════════════════
// GPS Location Sharing Section
// ═══════════════════════════════════════════════
function GpsShareSection({
  nodes
}) {
  const [cfg, setCfg] = useState(null); // {mode, nodes: [...], channels: [...], interval}
  const [channels, setChannels] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [warning, setWarning] = useState(null);
  useEffect(() => {
    api.get('/api/gps/share').then(d => {
      if (d && !d.error) setCfg(d);
    });
    api.get('/api/channels').then(d => {
      if (Array.isArray(d)) setChannels(d);
    });
  }, []);
  async function save() {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    setWarning(null);
    try {
      const res = await api.put('/api/gps/share', cfg);
      if (res.error) {
        setError(res.error);
      } else {
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
        if (res.warning) setWarning(res.warning);
      }
    } catch (e) {
      setError(e.message || 'Request failed');
    }
    setSaving(false);
  }
  function toggleNode(id) {
    setCfg(p => {
      const has = p.nodes.includes(id);
      return {
        ...p,
        nodes: has ? p.nodes.filter(n => n !== id) : [...p.nodes, id]
      };
    });
  }
  function toggleChannel(num) {
    setCfg(p => {
      const has = (p.channels || []).includes(num);
      return {
        ...p,
        channels: has ? p.channels.filter(ch => ch !== num) : [...(p.channels || []), num]
      };
    });
  }
  if (!cfg) return /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, "Loading\u2026");
  const IS = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
    borderRadius: 'var(--radius)',
    padding: '6px 10px',
    fontSize: 13,
    outline: 'none',
    fontFamily: 'inherit'
  };
  const SL = {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    marginBottom: 12
  };
  const FL = {
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 4
  };

  // Remote nodes only (exclude own node and fallback)
  const remoteNodes = nodes.filter(n => n.node_id !== 'local_gps');
  const modeLabels = {
    off: 'Off',
    all: 'Broadcast to All',
    selected: 'Selected Targets'
  };
  const modeColors = {
    off: 'var(--text-muted)',
    all: 'var(--accent-green)',
    selected: 'var(--accent-blue)'
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "GPS Location Sharing"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 16,
      lineHeight: 1.5
    }
  }, "Control whether your GPS position is broadcast over the mesh radio and to whom. Turning sharing off also disables the radio's own automatic position broadcasts."), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Sharing Mode"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      flexWrap: 'wrap',
      marginTop: 6
    }
  }, ['off', 'all', 'selected'].map(m => /*#__PURE__*/React.createElement("button", {
    key: m,
    onClick: () => setCfg(p => ({
      ...p,
      mode: m
    })),
    style: {
      padding: '8px 18px',
      borderRadius: 'var(--radius)',
      cursor: 'pointer',
      border: `1px solid ${cfg.mode === m ? modeColors[m] : 'var(--border)'}`,
      background: cfg.mode === m ? m === 'off' ? 'var(--bg-input)' : m === 'all' ? 'var(--accent-green-dim)' : 'var(--accent-blue-dim)' : 'var(--bg-input)',
      color: cfg.mode === m ? modeColors[m] : 'var(--text-secondary)',
      fontFamily: 'inherit',
      fontSize: 13,
      fontWeight: cfg.mode === m ? 600 : 400,
      transition: 'all 150ms'
    }
  }, modeLabels[m]))), cfg.mode === 'off' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, "Your GPS position is not shared with any mesh nodes, including automatic radio broadcasts."), cfg.mode === 'all' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      fontSize: 12,
      color: 'var(--accent-green)'
    }
  }, "Your position will be broadcast to all nodes on the mesh."), cfg.mode === 'selected' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      fontSize: 12,
      color: 'var(--accent-blue)'
    }
  }, "Your position will be sent only to the channels and nodes you select below.")), cfg.mode === 'selected' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Share On Channels"), channels.length === 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginTop: 6
    }
  }, "No channels configured yet.") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      marginTop: 6
    }
  }, channels.map(ch => {
    const checked = (cfg.channels || []).includes(ch.channel_num);
    return /*#__PURE__*/React.createElement("label", {
      key: ch.channel_num,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        cursor: 'pointer',
        padding: '8px 12px',
        borderRadius: 'var(--radius)',
        background: checked ? 'var(--accent-green-dim)' : 'var(--bg-input)',
        border: `1px solid ${checked ? 'var(--accent-green)' : 'var(--border)'}`
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: checked,
      onChange: () => toggleChannel(ch.channel_num),
      style: {
        accentColor: 'var(--accent-green)',
        width: 16,
        height: 16,
        flexShrink: 0
      }
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 13,
        fontWeight: 500,
        color: 'var(--text-primary)'
      }
    }, ch.name), /*#__PURE__*/React.createElement("div", {
      className: "mono",
      style: {
        fontSize: 10,
        color: 'var(--text-muted)'
      }
    }, "Channel ", ch.channel_num)), checked && /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 10,
        color: 'var(--accent-green)',
        fontWeight: 600,
        flexShrink: 0
      }
    }, "SHARING"));
  }))), cfg.mode === 'selected' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Share Directly With Nodes"), remoteNodes.length === 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginTop: 6
    }
  }, "No other nodes known yet \u2014 connect to the mesh first.") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      marginTop: 6,
      maxHeight: 240,
      overflowY: 'auto'
    }
  }, remoteNodes.map(n => {
    const checked = cfg.nodes.includes(n.node_id);
    return /*#__PURE__*/React.createElement("label", {
      key: n.node_id,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        cursor: 'pointer',
        padding: '8px 12px',
        borderRadius: 'var(--radius)',
        background: checked ? 'var(--accent-blue-dim)' : 'var(--bg-input)',
        border: `1px solid ${checked ? 'var(--accent-blue)' : 'var(--border)'}`,
        transition: 'all 150ms'
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: checked,
      onChange: () => toggleNode(n.node_id),
      style: {
        accentColor: 'var(--accent-blue)',
        width: 16,
        height: 16,
        flexShrink: 0
      }
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 13,
        fontWeight: 500,
        color: 'var(--text-primary)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap'
      }
    }, n.alias || n.long_name), /*#__PURE__*/React.createElement("div", {
      className: "mono",
      style: {
        fontSize: 10,
        color: 'var(--text-muted)'
      }
    }, n.node_id)), checked && /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 10,
        color: 'var(--accent-blue)',
        fontWeight: 600,
        flexShrink: 0
      }
    }, "SHARING"));
  })), (cfg.nodes.length > 0 || (cfg.channels || []).length > 0) && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, (cfg.channels || []).length, " channel", (cfg.channels || []).length !== 1 ? 's' : '', " and ", cfg.nodes.length, " node", cfg.nodes.length !== 1 ? 's' : '', " selected")), cfg.mode !== 'off' && /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Broadcast Interval"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center',
      marginTop: 6,
      flexWrap: 'wrap'
    }
  }, [10, 30, 60, 120, 300].map(s => /*#__PURE__*/React.createElement("button", {
    key: s,
    onClick: () => setCfg(p => ({
      ...p,
      interval: s
    })),
    className: `tab-btn ${cfg.interval === s ? 'active' : ''}`,
    style: {
      minWidth: 52
    }
  }, s < 60 ? `${s}s` : `${s / 60}m`)), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "\u2014 position sent every ", cfg.interval < 60 ? `${cfg.interval}s` : `${cfg.interval / 60}m`))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: save,
    disabled: saving
  }, saving ? 'Saving…' : 'Apply'), saved && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 13
    }
  }, "\u2713 Applied"), error && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 13
    }
  }, error), warning && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-amber)',
      fontSize: 12,
      lineHeight: 1.4
    }
  }, warning), cfg.mode !== 'off' && /*#__PURE__*/React.createElement("span", {
    style: {
      marginLeft: 'auto',
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "Changes take effect immediately \u2014 no restart needed.")));
}

// ═══════════════════════════════════════════════
// Restart Setup card (mobile only)
// ═══════════════════════════════════════════════
function RestartSetupCard({
  SL
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState(null);
  const restart = () => {
    // Hand off to the native bridge. AppViewModel.showSetupWizard already:
    //   • POSTs /api/hotspot/start to Atlas (4 s fire-and-forget) so single-radio
    //     Atlas drops its LAN and brings the hotspot back up
    //   • Clears the saved LAN URLs so the next probe goes straight to hotspot
    //   • Resets the wizard to the hotspot-connect step
    // If Atlas can't reach a known LAN on its own it falls back to the hotspot
    // automatically after ~30 seconds, so a missed request still recovers.
    setWorking(true);
    setError(null);
    const bridge = window.AtlasAndroid || window.AtlasIOS;
    if (bridge && bridge.showSetupWizard) {
      bridge.showSetupWizard();
    } else {
      setError('Setup wizard is only available in the Atlas mobile app.');
      setWorking(false);
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Atlas Connection"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      lineHeight: 1.45,
      marginBottom: 12
    }
  }, "Restart setup to drop Atlas's current LAN connection, bring its hotspot back up, and re-pair this phone from scratch. If Atlas can't find a known LAN on its own it falls back to the hotspot automatically after about 30 seconds."), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: restart,
    disabled: working
  }, working ? 'Switching to hotspot…' : 'Restart Setup'), error && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      fontSize: 12,
      color: 'var(--accent-red)'
    }
  }, error));
}

// ═══════════════════════════════════════════════
// General Settings Page
// ═══════════════════════════════════════════════
function SettingsPage({
  onSettingsSaved,
  themeMode,
  onThemeModeChange,
  hiddenPages,
  onHiddenPagesChange,
  onResetSidebarOrder,
  defaultPages,
  nodes
}) {
  const [settings, setSettings] = useState(null);
  const [errors, setErrors] = useState({});
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const isAtlasMobileApp = isAtlasMobileClient();
  useEffect(() => {
    api.get('/api/settings').then(s => {
      if (s) setSettings(s);
    });
  }, []);
  async function save() {
    const errs = validateSettings(settings);
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      await api.put('/api/settings', settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      if (onSettingsSaved) onSettingsSaved(settings);
    } finally {
      setSaving(false);
    }
  }
  function Field({
    label,
    name,
    type = 'text',
    min,
    max,
    options,
    hint
  }) {
    var _settings$name;
    if (!settings) return null;
    const val = (_settings$name = settings[name]) !== null && _settings$name !== void 0 ? _settings$name : '';
    const err = errors[name];
    const update = v => {
      setSettings(p => ({
        ...p,
        [name]: v
      }));
      setErrors(p => ({
        ...p,
        [name]: undefined
      }));
    };
    const inputStyle = {
      background: 'var(--bg-input)',
      border: `1px solid ${err ? 'var(--accent-red)' : 'var(--border)'}`,
      color: 'var(--text-primary)',
      borderRadius: 'var(--radius)',
      padding: '6px 10px',
      fontSize: 13,
      width: '100%',
      outline: 'none',
      fontFamily: 'inherit',
      boxSizing: 'border-box'
    };
    return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        fontWeight: 600,
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        marginBottom: 3
      }
    }, label), hint && /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 10,
        color: 'var(--text-muted)',
        marginBottom: 5,
        lineHeight: 1.3
      }
    }, hint), type === 'select' ? /*#__PURE__*/React.createElement("select", {
      value: val,
      onChange: e => update(e.target.value),
      style: inputStyle
    }, options.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
      key: v,
      value: v
    }, l))) : type === 'checkbox' ? /*#__PURE__*/React.createElement("label", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        cursor: 'pointer',
        marginTop: 4
      }
    }, /*#__PURE__*/React.createElement("input", {
      type: "checkbox",
      checked: val === 'true',
      onChange: e => update(e.target.checked ? 'true' : 'false'),
      style: {
        accentColor: 'var(--accent-green)',
        width: 16,
        height: 16
      }
    }), /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: 13,
        color: 'var(--text-muted)'
      }
    }, "Enabled")) : /*#__PURE__*/React.createElement("input", {
      type: type,
      value: val,
      min: min,
      max: max,
      onChange: e => update(e.target.value),
      style: inputStyle,
      onFocus: e => !err && (e.target.style.borderColor = 'var(--accent-green)'),
      onBlur: e => !err && (e.target.style.borderColor = 'var(--border)')
    }), err && /*#__PURE__*/React.createElement("div", {
      style: {
        color: 'var(--accent-red)',
        fontSize: 11,
        marginTop: 3
      }
    }, err));
  }
  if (!settings) return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      padding: 16
    }
  }, "Loading\u2026"));
  const SL = {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    marginBottom: 12
  };
  const FL = {
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 4
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, isAtlasMobileApp && /*#__PURE__*/React.createElement(RestartSetupCard, {
    SL: SL
  }), /*#__PURE__*/React.createElement("div", {
    className: "two-col",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Connection"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Serial Port",
    name: "serial_port",
    hint: "Restart to apply",
    type: "text"
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Web Port",
    name: "web_port",
    hint: "Restart to apply",
    type: "number",
    min: 1024,
    max: 65535
  }))), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Dashboard"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Refresh Interval (s)",
    name: "dashboard_refresh_s",
    type: "number",
    min: 1,
    max: 60,
    hint: "How often to poll for updates"
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Date Format",
    name: "date_format",
    type: "select",
    options: [['relative', 'Relative (2m ago)'], ['absolute', 'Absolute (14:32:01)']]
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Units",
    name: "units",
    type: "select",
    options: [['metric', 'Metric (km, °C)'], ['imperial', 'Imperial (mi, °F)']]
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Time Zone",
    name: "timezone",
    type: "select",
    options: TIMEZONE_OPTIONS,
    hint: "Clock and timestamps render in this zone. IANA zones follow daylight-saving rules automatically."
  }))), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Nodes"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Show Offline Nodes",
    name: "show_offline_nodes",
    type: "checkbox",
    hint: "Display nodes not recently heard"
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Online Window (hours)",
    name: "node_online_window_h",
    type: "number",
    min: 1,
    max: 24,
    hint: "Hours since last heard to consider online"
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Default Map Zoom",
    name: "map_default_zoom",
    type: "number",
    min: 1,
    max: 19
  }))), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Alerts"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12
    }
  }, /*#__PURE__*/React.createElement(Field, {
    label: "Battery Warning (%)",
    name: "battery_alert_pct",
    type: "number",
    min: 5,
    max: 50,
    hint: "Alert when battery falls below this"
  }), /*#__PURE__*/React.createElement(Field, {
    label: "Offline Threshold (min)",
    name: "node_offline_min",
    type: "number",
    min: 5,
    max: 1440,
    hint: "Alert after this many silent minutes"
  })))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Personalization"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Theme"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, "Dark mode matches the current Atlas look"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      alignItems: 'center',
      flexWrap: 'wrap'
    }
  }, [['dark', 'Dark'], ['light', 'Light']].map(([value, label]) => /*#__PURE__*/React.createElement("button", {
    key: value,
    onClick: () => onThemeModeChange(value),
    className: `btn btn-sm ${themeMode === value ? 'btn-primary' : ''}`,
    style: {
      minWidth: 84
    }
  }, label))))), /*#__PURE__*/React.createElement("div", {
    style: {
      borderTop: '1px solid var(--border)',
      paddingTop: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: FL
  }, "Sidebar Pages"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexWrap: 'wrap',
      gap: 8,
      marginBottom: 10,
      marginTop: 6
    }
  }, (defaultPages || []).filter(p => !ALWAYS_VISIBLE_PAGES.includes(p.id)).map(p => /*#__PURE__*/React.createElement("label", {
    key: p.id,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      cursor: 'pointer',
      background: 'var(--bg-input)',
      padding: '5px 12px',
      borderRadius: 'var(--radius)',
      border: `1px solid ${hiddenPages.includes(p.id) ? 'var(--border)' : 'var(--border-bright)'}`
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !hiddenPages.includes(p.id),
    onChange: e => {
      const next = e.target.checked ? hiddenPages.filter(id => id !== p.id) : [...hiddenPages, p.id];
      onHiddenPagesChange(next);
    },
    style: {
      accentColor: 'var(--accent-green)',
      width: 14,
      height: 14
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: hiddenPages.includes(p.id) ? 'var(--text-muted)' : 'var(--text-secondary)'
    }
  }, p.label)))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "Drag & drop sidebar icons to reorder"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onResetSidebarOrder
  }, "Reset Order")))), /*#__PURE__*/React.createElement(DeviceConfigSection, null), /*#__PURE__*/React.createElement(GpsShareSection, {
    nodes: nodes || []
  }), /*#__PURE__*/React.createElement(SoftwareUpdateSection, null), /*#__PURE__*/React.createElement(FactoryResetSection, null), /*#__PURE__*/React.createElement("div", {
    className: "settings-actions",
    style: {
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: save,
    disabled: saving
  }, saving ? 'Saving…' : 'Save Settings'), saved && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 13
    }
  }, "Saved!"), Object.keys(errors).length > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 13
    }
  }, Object.keys(errors).length, " error(s) \u2014 fix highlighted fields."), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginLeft: 'auto'
    }
  }, "Connection settings require an app restart to take effect.")));
}

// ═══════════════════════════════════════════════
// Software Update Section
// ═══════════════════════════════════════════════
function SoftwareUpdateSection() {
  const [info, setInfo] = useState(null); // light local info from GET
  const [check, setCheck] = useState(null); // remote check result from POST
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [install, setInstall] = useState(null); // {startVersion} while updating
  const [status, setStatus] = useState(null); // /api/update/status payload
  const [unreachable, setUnreachable] = useState(0);
  const pollRef = useRef(null);
  const logRef = useRef(null);
  useEffect(() => {
    api.get('/api/update/check').then(i => {
      setInfo(i);
      // Resume the progress view if an update is already running (e.g. the
      // page reloaded mid-update, or another client started one).
      if (i && i.update_running) setInstall({
        startVersion: i.version
      });
    }).catch(() => {});
    return () => clearInterval(pollRef.current);
  }, []);

  // Poll status while an update runs. The service restarts itself partway
  // through, so fetch failures are expected — keep polling through them.
  useEffect(() => {
    if (!install) return;
    pollRef.current = setInterval(async () => {
      try {
        const s = await api.get('/api/update/status', {
          timeoutMs: 8000
        });
        setUnreachable(0);
        setStatus(s);
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
        const finished = s.state === 'success' || !s.running && install.startVersion && s.version && s.version !== install.startVersion;
        if (finished) {
          clearInterval(pollRef.current);
          setStatus(p => ({
            ...p,
            state: 'success'
          }));
          setTimeout(() => window.location.reload(), 3000);
        } else if (s.state === 'failed') {
          clearInterval(pollRef.current);
        }
      } catch {
        setUnreachable(n => n + 1);
      }
    }, 2500);
    return () => clearInterval(pollRef.current);
  }, [install]);
  async function runCheck() {
    setChecking(true);
    setError('');
    setCheck(null);
    try {
      const r = await api.post('/api/update/check', undefined, {
        timeoutMs: 45000
      });
      setCheck(r);
      if (r.version) setInfo(p => ({
        ...(p || {}),
        ...r
      }));
    } catch (e) {
      setError(e.message || 'Update check failed');
    } finally {
      setChecking(false);
    }
  }
  async function startInstall() {
    setConfirming(false);
    setError('');
    setStatus(null);
    setUnreachable(0);
    try {
      await api.post('/api/update/apply');
      setInstall({
        startVersion: info === null || info === void 0 ? void 0 : info.version
      });
    } catch (e) {
      setError(e.message || 'Could not start the update');
    }
  }
  const SL = {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    marginBottom: 12
  };
  const muted = {
    fontSize: 12,
    color: 'var(--text-muted)'
  };
  const state = status === null || status === void 0 ? void 0 : status.state;
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Software Update"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 14,
      flexWrap: 'wrap',
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--text-primary)',
      fontWeight: 600
    }
  }, "Atlas Control ", info !== null && info !== void 0 && info.version ? `· ${info.version}` : ''), /*#__PURE__*/React.createElement("div", {
    style: muted
  }, info !== null && info !== void 0 && info.installed_at ? `Installed ${info.installed_at.slice(0, 10)} · ` : '', "Updates download from GitHub and preserve all data, maps and settings.")), !install && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      marginLeft: 'auto'
    },
    onClick: runCheck,
    disabled: checking
  }, checking ? 'Checking…' : 'Check for Updates')), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      marginBottom: 8
    }
  }, error), check && !install && (check.update_available ? /*#__PURE__*/React.createElement("div", {
    style: {
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius)',
      padding: '10px 12px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--accent-green)',
      fontWeight: 600,
      marginBottom: 6
    }
  }, "Update available: ", check.latest, " (", check.behind, " new ", check.behind === 1 ? 'commit' : 'commits', ")"), /*#__PURE__*/React.createElement("div", {
    style: {
      maxHeight: 120,
      overflowY: 'auto',
      marginBottom: 10
    }
  }, (check.commits || []).map(c => /*#__PURE__*/React.createElement("div", {
    key: c.rev,
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)',
      padding: '2px 0'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)',
      fontFamily: 'monospace'
    }
  }, c.rev), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, " ", c.date, " "), c.subject))), check.local_changes && /*#__PURE__*/React.createElement("div", {
    style: {
      ...muted,
      color: 'var(--accent-yellow, #d8b54a)',
      marginBottom: 8
    }
  }, "This box has local code changes \u2014 the update keeps them and installs what it can."), !confirming ? /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: () => setConfirming(true)
  }, "Install Update") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)'
    }
  }, "Atlas restarts when done (~1\u20133 min). Install now?"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: startInstall
  }, "Yes, install"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setConfirming(false)
  }, "Cancel"))) : /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--accent-green)'
    }
  }, "\u2713 Up to date", check.ahead > 0 ? ` (${check.ahead} local commits ahead of the release)` : '')), install && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 8
    }
  }, state === 'success' ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 13,
      fontWeight: 600
    }
  }, "\u2713 Updated", status !== null && status !== void 0 && status.version ? ` to ${status.version}` : '', " \u2014 reloading\u2026") : state === 'failed' ? /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 13,
      fontWeight: 600
    }
  }, "Update failed \u2014 see log below") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
    className: "spinner",
    style: {
      width: 14,
      height: 14,
      border: '2px solid var(--border)',
      borderTopColor: 'var(--accent-green)',
      borderRadius: '50%',
      display: 'inline-block',
      animation: 'spin 1s linear infinite'
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-secondary)'
    }
  }, unreachable >= 2 ? 'Atlas is restarting — reconnecting…' : 'Installing update…'))), (status === null || status === void 0 ? void 0 : status.log) && /*#__PURE__*/React.createElement("pre", {
    ref: logRef,
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 10,
      fontSize: 11,
      lineHeight: 1.5,
      maxHeight: 200,
      overflowY: 'auto',
      whiteSpace: 'pre-wrap',
      color: 'var(--text-secondary)',
      margin: 0
    }
  }, status.log), state === 'failed' && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      marginTop: 8
    },
    onClick: () => {
      setInstall(null);
      setStatus(null);
      setCheck(null);
    }
  }, "Dismiss")));
}

// ═══════════════════════════════════════════════
// Factory Reset Section (Danger Zone)
// ═══════════════════════════════════════════════
function FactoryResetSection() {
  var _snapshot$owner, _snapshot$owner2, _snapshot$lora$region, _snapshot$lora, _snapshot$lora$modem_, _snapshot$lora2, _snapshot$lora$hop_li, _snapshot$lora3, _snapshot$lora$tx_pow, _snapshot$lora4, _snapshot$device$role, _snapshot$device, _snapshot$device$rebr, _snapshot$device2, _snapshot$device$node, _snapshot$device3, _snapshot$position$po, _snapshot$position, _snapshot$position$gp, _snapshot$position2;
  const [snapshot, setSnapshot] = useState(null);
  const [deviceConnected, setDeviceConnected] = useState(false);
  const [deviceStatus, setDeviceStatus] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [resetting, setResetting] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const refreshStatus = React.useCallback(async () => {
    try {
      const r = await api.get('/api/factory-defaults');
      setSnapshot((r === null || r === void 0 ? void 0 : r.snapshot) || null);
      setDeviceConnected(!!(r !== null && r !== void 0 && r.device_connected));
      setDeviceStatus((r === null || r === void 0 ? void 0 : r.device_status) || '');
      if (r !== null && r !== void 0 && r.capture_error) setError(r.capture_error);
    } catch (e) {/* ignore poll errors */}
  }, []);

  // Lazy-capture on mount, then re-poll every 5s while the device is offline
  // so the panel becomes usable as soon as the radio link comes back.
  useEffect(() => {
    refreshStatus();
    const id = setInterval(() => {
      // Stop polling once we have a snapshot — re-capture is on demand only.
      if (!snapshot) refreshStatus();
    }, 5000);
    return () => clearInterval(id);
  }, [refreshStatus, snapshot]);
  const recapture = async () => {
    setError(null);
    setCapturing(true);
    try {
      const r = await api.post('/api/factory-defaults/capture', {});
      setSnapshot((r === null || r === void 0 ? void 0 : r.snapshot) || null);
      setDeviceConnected(true);
      setDeviceStatus('connected');
    } catch (e) {
      setError(e.message || String(e));
      // Refresh in the background so the status line reflects current reality.
      refreshStatus();
    } finally {
      setCapturing(false);
    }
  };
  const runReset = async () => {
    setResetting(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.post('/api/factory-reset', {});
      setResult(r);
      setConfirmOpen(false);
      setConfirmText('');
      // Give the radio a moment to reboot before reloading the UI so the
      // dashboard refetches a clean state.
      setTimeout(() => {
        window.location.reload();
      }, 2500);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setResetting(false);
    }
  };
  const SL = {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--accent-red)',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    marginBottom: 10
  };
  const cardStyle = {
    marginBottom: 16,
    borderColor: 'var(--accent-red)'
  };
  const fmtCapturedAt = ts => {
    if (!ts) return '—';
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleString();
    } catch {
      return '—';
    }
  };
  const capturedSections = snapshot ? Object.keys(snapshot).filter(k => !['captured_at', 'owner'].includes(k) && snapshot[k] && typeof snapshot[k] === 'object') : [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: cardStyle
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Danger Zone"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--text-primary)',
      fontWeight: 600
    }
  }, "Factory Reset"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      lineHeight: 1.45
    }
  }, "Wipes all messaging history, AI chat history, node directory, telemetry, topology, alerts, and GPS coordinate data (positions, phone trackers, waypoints, routes, hike sessions, nav favorites, scheduled jobs). The Heltec V4 radio is restored to the factory default snapshot captured below \u2014 only the ", /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)'
    }
  }, "short_name"), " resets to the firmware default. The AI knowledge base, calendar, and app settings are preserved."), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 10,
      marginTop: 6
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em'
    }
  }, "Heltec snapshot"), snapshot ? /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--accent-green)'
    }
  }, "\u2713 Captured") : /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: deviceConnected ? 'var(--accent-amber)' : 'var(--accent-red)'
    }
  }, deviceConnected ? 'Not yet captured' : `Device: ${deviceStatus || 'offline'}`), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: recapture,
    disabled: capturing,
    style: {
      marginLeft: 'auto'
    }
  }, capturing ? 'Capturing…' : 'Re-capture from device')), snapshot && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      display: 'grid',
      gridTemplateColumns: 'auto 1fr',
      rowGap: 4,
      columnGap: 12,
      fontSize: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Last captured"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-mono)'
    }
  }, fmtCapturedAt(snapshot.captured_at)), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Long name"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, ((_snapshot$owner = snapshot.owner) === null || _snapshot$owner === void 0 ? void 0 : _snapshot$owner.long_name) || '—'), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Node ID"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)',
      fontFamily: 'var(--font-mono)'
    }
  }, ((_snapshot$owner2 = snapshot.owner) === null || _snapshot$owner2 === void 0 ? void 0 : _snapshot$owner2.node_id) || '—'), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "LoRa region"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, (_snapshot$lora$region = (_snapshot$lora = snapshot.lora) === null || _snapshot$lora === void 0 ? void 0 : _snapshot$lora.region) !== null && _snapshot$lora$region !== void 0 ? _snapshot$lora$region : '—', " \xB7 preset ", (_snapshot$lora$modem_ = (_snapshot$lora2 = snapshot.lora) === null || _snapshot$lora2 === void 0 ? void 0 : _snapshot$lora2.modem_preset) !== null && _snapshot$lora$modem_ !== void 0 ? _snapshot$lora$modem_ : '—', " \xB7 hop_limit ", (_snapshot$lora$hop_li = (_snapshot$lora3 = snapshot.lora) === null || _snapshot$lora3 === void 0 ? void 0 : _snapshot$lora3.hop_limit) !== null && _snapshot$lora$hop_li !== void 0 ? _snapshot$lora$hop_li : '—', " \xB7 tx_power ", (_snapshot$lora$tx_pow = (_snapshot$lora4 = snapshot.lora) === null || _snapshot$lora4 === void 0 ? void 0 : _snapshot$lora4.tx_power) !== null && _snapshot$lora$tx_pow !== void 0 ? _snapshot$lora$tx_pow : '—', " dBm"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Role / rebroadcast"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, (_snapshot$device$role = (_snapshot$device = snapshot.device) === null || _snapshot$device === void 0 ? void 0 : _snapshot$device.role) !== null && _snapshot$device$role !== void 0 ? _snapshot$device$role : '—', " / ", (_snapshot$device$rebr = (_snapshot$device2 = snapshot.device) === null || _snapshot$device2 === void 0 ? void 0 : _snapshot$device2.rebroadcast_mode) !== null && _snapshot$device$rebr !== void 0 ? _snapshot$device$rebr : '—'), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Broadcast intervals"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, "node_info ", (_snapshot$device$node = (_snapshot$device3 = snapshot.device) === null || _snapshot$device3 === void 0 ? void 0 : _snapshot$device3.node_info_broadcast_secs) !== null && _snapshot$device$node !== void 0 ? _snapshot$device$node : '—', "s \xB7 position ", (_snapshot$position$po = (_snapshot$position = snapshot.position) === null || _snapshot$position === void 0 ? void 0 : _snapshot$position.position_broadcast_secs) !== null && _snapshot$position$po !== void 0 ? _snapshot$position$po : '—', "s \xB7 gps_update ", (_snapshot$position$gp = (_snapshot$position2 = snapshot.position) === null || _snapshot$position2 === void 0 ? void 0 : _snapshot$position2.gps_update_interval) !== null && _snapshot$position$gp !== void 0 ? _snapshot$position$gp : '—', "s"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-muted)'
    }
  }, "Captured sections"), /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-secondary)',
      fontSize: 11
    }
  }, capturedSections.join(' · ') || '—'))), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      marginTop: 4
    }
  }, error), result && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 12,
      marginTop: 4
    }
  }, "\u2713 Reset complete \xB7 reloading\u2026"), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => {
      setConfirmOpen(true);
      setConfirmText('');
      setError(null);
      setResult(null);
    },
    style: {
      background: 'var(--accent-red)',
      borderColor: 'var(--accent-red)',
      color: '#fff'
    }
  }, "Factory Reset\u2026"))), confirmOpen && /*#__PURE__*/React.createElement(FactoryResetConfirm, {
    snapshot: snapshot,
    confirmText: confirmText,
    setConfirmText: setConfirmText,
    resetting: resetting,
    error: error,
    fmtCapturedAt: fmtCapturedAt,
    onCancel: () => {
      if (!resetting) {
        setConfirmOpen(false);
        setConfirmText('');
      }
    },
    onConfirm: runReset
  }));
}

// Two-step confirmation modal — user must check "I understand" AND
// type RESET before the destructive action enables.
function FactoryResetConfirm({
  snapshot,
  confirmText,
  setConfirmText,
  resetting,
  error,
  fmtCapturedAt,
  onCancel,
  onConfirm
}) {
  var _snapshot$owner3, _snapshot$lora$region2, _snapshot$lora5, _snapshot$lora$modem_2, _snapshot$lora6, _snapshot$lora$hop_li2, _snapshot$lora7, _snapshot$lora$tx_pow2, _snapshot$lora8, _snapshot$device$role2, _snapshot$device4, _snapshot$device$rebr2, _snapshot$device5, _snapshot$device$node2, _snapshot$device6, _snapshot$position$po2, _snapshot$position3, _snapshot$position$gp2, _snapshot$position4;
  const [acknowledged, setAcknowledged] = useState(false);
  const ready = acknowledged && confirmText.trim() === 'RESET';
  const wipeList = ['All mesh chat messages (channels and direct messages)', 'All AI chat conversations and history', 'All node directory entries, telemetry, topology, and alerts', 'All GPS coordinate data — position history, phone trackers, waypoints, routes, hike sessions, nav favorites, and scheduled jobs'];
  const preserveList = ['AI knowledge base documents', 'AI model settings (system prompt, temperature, etc.)', 'App settings (theme, units, alerts, hidden pages)', 'Calendar events', 'The Heltec snapshot itself (will be re-applied to the radio)'];
  return /*#__PURE__*/React.createElement("div", {
    className: "rename-overlay",
    onClick: e => e.target === e.currentTarget && !resetting && onCancel()
  }, /*#__PURE__*/React.createElement("div", {
    className: "rename-modal",
    style: {
      borderColor: 'var(--accent-red)',
      maxWidth: 560,
      width: '94%'
    }
  }, /*#__PURE__*/React.createElement("h3", {
    style: {
      color: 'var(--accent-red)',
      marginBottom: 6
    }
  }, "\u26A0 Confirm Factory Reset"), /*#__PURE__*/React.createElement("p", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginTop: 0,
      marginBottom: 14,
      lineHeight: 1.5
    }
  }, "This action permanently deletes data from this Atlas Control device and rewrites the Heltec V4 radio to its captured factory defaults.", /*#__PURE__*/React.createElement("strong", {
    style: {
      color: 'var(--accent-red)'
    }
  }, " This cannot be undone.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 12,
      marginBottom: 14,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: '1 1 220px',
      background: 'var(--bg-input)',
      border: '1px solid var(--accent-red)',
      borderRadius: 'var(--radius)',
      padding: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--accent-red)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      marginBottom: 6
    }
  }, "Will be deleted"), /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      paddingLeft: 18,
      fontSize: 12,
      color: 'var(--text-secondary)',
      lineHeight: 1.5
    }
  }, wipeList.map((w, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, w)))), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: '1 1 220px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--accent-green)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      marginBottom: 6
    }
  }, "Will be preserved"), /*#__PURE__*/React.createElement("ul", {
    style: {
      margin: 0,
      paddingLeft: 18,
      fontSize: 12,
      color: 'var(--text-secondary)',
      lineHeight: 1.5
    }
  }, preserveList.map((p, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, p))))), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: 10,
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-muted)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      marginBottom: 6
    }
  }, "Heltec radio will be restored to"), snapshot ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)',
      lineHeight: 1.6
    }
  }, /*#__PURE__*/React.createElement("div", null, "Snapshot from ", /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      color: 'var(--text-primary)'
    }
  }, fmtCapturedAt(snapshot.captured_at))), /*#__PURE__*/React.createElement("div", null, "Long name: ", /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--text-primary)'
    }
  }, ((_snapshot$owner3 = snapshot.owner) === null || _snapshot$owner3 === void 0 ? void 0 : _snapshot$owner3.long_name) || '—')), /*#__PURE__*/React.createElement("div", null, "Region ", (_snapshot$lora$region2 = (_snapshot$lora5 = snapshot.lora) === null || _snapshot$lora5 === void 0 ? void 0 : _snapshot$lora5.region) !== null && _snapshot$lora$region2 !== void 0 ? _snapshot$lora$region2 : '—', " \xB7 preset ", (_snapshot$lora$modem_2 = (_snapshot$lora6 = snapshot.lora) === null || _snapshot$lora6 === void 0 ? void 0 : _snapshot$lora6.modem_preset) !== null && _snapshot$lora$modem_2 !== void 0 ? _snapshot$lora$modem_2 : '—', " \xB7 hop_limit ", (_snapshot$lora$hop_li2 = (_snapshot$lora7 = snapshot.lora) === null || _snapshot$lora7 === void 0 ? void 0 : _snapshot$lora7.hop_limit) !== null && _snapshot$lora$hop_li2 !== void 0 ? _snapshot$lora$hop_li2 : '—', " \xB7 tx_power ", (_snapshot$lora$tx_pow2 = (_snapshot$lora8 = snapshot.lora) === null || _snapshot$lora8 === void 0 ? void 0 : _snapshot$lora8.tx_power) !== null && _snapshot$lora$tx_pow2 !== void 0 ? _snapshot$lora$tx_pow2 : '—', " dBm"), /*#__PURE__*/React.createElement("div", null, "Role ", (_snapshot$device$role2 = (_snapshot$device4 = snapshot.device) === null || _snapshot$device4 === void 0 ? void 0 : _snapshot$device4.role) !== null && _snapshot$device$role2 !== void 0 ? _snapshot$device$role2 : '—', " \xB7 rebroadcast ", (_snapshot$device$rebr2 = (_snapshot$device5 = snapshot.device) === null || _snapshot$device5 === void 0 ? void 0 : _snapshot$device5.rebroadcast_mode) !== null && _snapshot$device$rebr2 !== void 0 ? _snapshot$device$rebr2 : '—', " \xB7 node_info every ", (_snapshot$device$node2 = (_snapshot$device6 = snapshot.device) === null || _snapshot$device6 === void 0 ? void 0 : _snapshot$device6.node_info_broadcast_secs) !== null && _snapshot$device$node2 !== void 0 ? _snapshot$device$node2 : '—', "s"), /*#__PURE__*/React.createElement("div", null, "Position broadcast every ", (_snapshot$position$po2 = (_snapshot$position3 = snapshot.position) === null || _snapshot$position3 === void 0 ? void 0 : _snapshot$position3.position_broadcast_secs) !== null && _snapshot$position$po2 !== void 0 ? _snapshot$position$po2 : '—', "s \xB7 GPS update every ", (_snapshot$position$gp2 = (_snapshot$position4 = snapshot.position) === null || _snapshot$position4 === void 0 ? void 0 : _snapshot$position4.gps_update_interval) !== null && _snapshot$position$gp2 !== void 0 ? _snapshot$position$gp2 : '—', "s"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      marginTop: 4
    }
  }, "Short name will reset to the Heltec firmware default.")) : /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--accent-amber)'
    }
  }, "No snapshot captured yet \u2014 the radio's existing config will be left as-is, and only the short_name will reset.")), /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'flex-start',
      gap: 8,
      cursor: resetting ? 'default' : 'pointer',
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: acknowledged,
    disabled: resetting,
    onChange: e => setAcknowledged(e.target.checked),
    style: {
      accentColor: 'var(--accent-red)',
      width: 16,
      height: 16,
      marginTop: 2
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)',
      lineHeight: 1.4
    }
  }, "I understand this will permanently delete all messaging, AI chat, node, and GPS data on Atlas Control.")), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 6
    }
  }, "Type ", /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: 'var(--font-mono)',
      color: 'var(--accent-red)'
    }
  }, "RESET"), " below to confirm:"), /*#__PURE__*/React.createElement("input", {
    value: confirmText,
    onChange: e => setConfirmText(e.target.value),
    placeholder: "RESET",
    autoFocus: true,
    disabled: resetting,
    onKeyDown: e => {
      if (e.key === 'Escape' && !resetting) onCancel();
      if (e.key === 'Enter' && ready) onConfirm();
    }
  }), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      margin: '6px 0'
    }
  }, error), /*#__PURE__*/React.createElement("div", {
    className: "rename-modal-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onCancel,
    disabled: resetting
  }, "Cancel"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onConfirm,
    disabled: resetting || !ready,
    style: {
      background: 'var(--accent-red)',
      borderColor: 'var(--accent-red)',
      color: '#fff',
      opacity: ready ? 1 : 0.5
    }
  }, resetting ? 'Resetting…' : 'Confirm Factory Reset'))));
}

// ═══════════════════════════════════════════════
// Device Configuration Section (Heltec V4 / Meshtastic)
// ═══════════════════════════════════════════════
const DEVICE_ROLES = [[0, 'CLIENT'], [1, 'CLIENT_MUTE'], [2, 'ROUTER'], [3, 'ROUTER_CLIENT'], [4, 'REPEATER'], [5, 'TRACKER'], [6, 'SENSOR'], [7, 'TAK'], [8, 'CLIENT_HIDDEN'], [9, 'LOST_AND_FOUND']];
const LORA_REGIONS = [[0, 'UNSET'], [1, 'US'], [2, 'EU_433'], [3, 'EU_868'], [4, 'CN'], [5, 'JP'], [6, 'ANZ'], [7, 'KR'], [8, 'TW'], [9, 'RU'], [10, 'IN'], [11, 'NZ_865'], [12, 'TH'], [13, 'LORA_24'], [14, 'UA_433'], [15, 'UA_868']];
const MODEM_PRESETS = [[0, 'LONG_FAST'], [1, 'LONG_SLOW'], [2, 'VERY_LONG_SLOW'], [3, 'MEDIUM_SLOW'], [4, 'MEDIUM_FAST'], [5, 'SHORT_SLOW'], [6, 'SHORT_FAST'], [7, 'LONG_MODERATE']];
const REBROADCAST_MODES = [[0, 'ALL'], [1, 'ALL_SKIP_DECODING'], [2, 'LOCAL_ONLY'], [3, 'KNOWN_ONLY']];

// Stable style constants and helper components defined outside DeviceConfigSection
// to prevent React from unmounting/remounting inputs on every parent re-render.
const _DC_SL = {
  fontSize: 11,
  fontWeight: 700,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: 10
};
const _DC_FL = {
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  marginBottom: 4
};
const _DC_IS = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
  borderRadius: 'var(--radius)',
  padding: '6px 10px',
  fontSize: 13,
  outline: 'none',
  fontFamily: 'inherit',
  width: '100%',
  boxSizing: 'border-box'
};
function DeviceConfigRow({
  label,
  hint,
  children
}) {
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: _DC_FL
  }, label), hint && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      marginBottom: 4,
      lineHeight: 1.4
    }
  }, hint), children);
}
function DeviceConfigSaveBar({
  section,
  saving,
  saved,
  onSave
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: () => onSave(section),
    disabled: saving[section]
  }, saving[section] ? 'Writing…' : 'Write to Device'), saved[section] && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 12
    }
  }, "\u2713 Applied"));
}
const DeviceConfigSection = React.memo(function DeviceConfigSection() {
  var _cfg$device$role, _cfg$device, _cfg$device$rebroadca, _cfg$device2, _cfg$device3, _cfg$device4, _cfg$device$node_info, _cfg$device5, _cfg$lora$region, _cfg$lora, _cfg$lora$modem_prese, _cfg$lora2, _cfg$lora3, _cfg$lora4, _cfg$lora$hop_limit, _cfg$lora5, _cfg$lora$tx_power, _cfg$lora6, _cfg$lora7, _cfg$lora8, _cfg$lora$bandwidth, _cfg$lora9, _cfg$lora$spread_fact, _cfg$lora10, _cfg$lora$coding_rate, _cfg$lora11, _cfg$position$gps_upd, _cfg$position, _cfg$position$positio, _cfg$position2, _cfg$position3, _cfg$position4, _cfg$power, _cfg$power$on_battery, _cfg$power2, _cfg$power$wait_bluet, _cfg$power3, _cfg$bluetooth, _cfg$bluetooth$fixed_, _cfg$bluetooth2, _cfg$network, _cfg$network$address_, _cfg$network2, _cfg$network$wifi_ssi, _cfg$network3, _cfg$network$wifi_psk, _cfg$network4, _cfg$network$ntp_serv, _cfg$network5, _cfg$network6;
  const [cfg, setCfg] = useState(null);
  const [owner, setOwner] = useState({
    long_name: '',
    short_name: ''
  });
  const [saving, setSaving] = useState({});
  const [saved, setSaved] = useState({});
  const [error, setError] = useState(null);
  const wait = React.useCallback(ms => new Promise(resolve => setTimeout(resolve, ms)), []);
  const reloadDeviceState = React.useCallback(async () => {
    let lastErr = null;
    for (let i = 0; i < 5; i++) {
      try {
        const [config, ownerInfo] = await Promise.all([api.get('/api/device/config'), api.get('/api/device/owner').catch(() => ({}))]);
        if (config && !config.error) {
          setCfg(config);
          setError(null);
        } else if (config && config.error) {
          setError(config.error);
        }
        if (ownerInfo) setOwner(ownerInfo);
        return config;
      } catch (err) {
        lastErr = err;
        if (i < 4) await wait(1000);
      }
    }
    throw lastErr || new Error('Unable to load device configuration');
  }, [wait]);
  const waitForDeviceReconnect = React.useCallback(async () => {
    let lastErr = null;
    for (let i = 0; i < 20; i++) {
      try {
        const device = await api.get('/api/device');
        if (device !== null && device !== void 0 && device.connected) return device;
      } catch (err) {
        lastErr = err;
      }
      await wait(1000);
    }
    if (lastErr) throw lastErr;
    throw new Error('Device did not reconnect after applying settings');
  }, [wait]);
  useEffect(() => {
    reloadDeviceState().catch(err => setError(err.message || String(err)));
  }, [reloadDeviceState]);
  const cfgRef = React.useRef(cfg);
  useEffect(() => {
    cfgRef.current = cfg;
  }, [cfg]);
  const saveSection = React.useCallback(async section => {
    const current = cfgRef.current;
    if (!current || !current[section]) return;
    setError(null);
    setSaving(p => ({
      ...p,
      [section]: true
    }));
    try {
      const res = await api.put(`/api/device/config/${section}`, current[section]);
      if ((res === null || res === void 0 ? void 0 : res.ok) === false) throw new Error(res.error || `Failed to save ${section}`);
      await waitForDeviceReconnect();
      await reloadDeviceState();
      setSaved(p => ({
        ...p,
        [section]: true
      }));
      setTimeout(() => setSaved(p => ({
        ...p,
        [section]: false
      })), 2500);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setSaving(p => ({
        ...p,
        [section]: false
      }));
    }
  }, [reloadDeviceState, waitForDeviceReconnect]);
  const set = React.useCallback((section, key, val) => {
    setCfg(p => ({
      ...p,
      [section]: {
        ...p[section],
        [key]: val
      }
    }));
  }, []);
  const SL = _DC_SL;
  const IS = _DC_IS;
  const Row = DeviceConfigRow;
  const SaveBar = ({
    section
  }) => /*#__PURE__*/React.createElement(DeviceConfigSaveBar, {
    section: section,
    saving: saving,
    saved: saved,
    onSave: saveSection
  });
  if (!cfg) return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Device Configuration"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, error ? `Device not available: ${error}` : cfg === null ? 'Loading…' : 'Device not connected.'));
  if (!cfg.connected) return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Device Configuration"), /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, "Device not connected \u2014 connect the Heltec V4 to configure."));
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: SL
  }, "Device Configuration \u2014 Heltec V4"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--accent-amber)',
      marginBottom: 14,
      background: 'var(--accent-amber-dim)',
      padding: '6px 10px',
      borderRadius: 'var(--radius)',
      border: '1px solid rgba(245,158,11,0.3)'
    }
  }, "\u26A0 Changes are written directly to the radio. The device will reboot to apply some settings."), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Node Identity"), /*#__PURE__*/React.createElement("div", {
    className: "device-identity-grid"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "Long Name",
    hint: "Up to 39 characters \u2014 visible to other mesh nodes"
  }, /*#__PURE__*/React.createElement("input", {
    style: IS,
    value: owner.long_name || '',
    maxLength: 39,
    onChange: e => setOwner(p => ({
      ...p,
      long_name: e.target.value
    })),
    placeholder: "Atlas Control"
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Short Name",
    hint: "Up to 4 characters \u2014 shown on maps and in node lists"
  }, /*#__PURE__*/React.createElement("input", {
    style: IS,
    value: owner.short_name || '',
    maxLength: 4,
    onChange: e => setOwner(p => ({
      ...p,
      short_name: e.target.value.slice(0, 4)
    })),
    placeholder: "ATLS"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "settings-actions",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: async () => {
      setSaving(p => ({
        ...p,
        owner: true
      }));
      setError(null);
      try {
        const res = await api.put('/api/device/owner', {
          long_name: owner.long_name,
          short_name: owner.short_name
        });
        if ((res === null || res === void 0 ? void 0 : res.ok) === false) throw new Error(res.error || 'Failed to save identity');
        setSaved(p => ({
          ...p,
          owner: true
        }));
        setTimeout(() => setSaved(p => ({
          ...p,
          owner: false
        })), 2500);
      } catch (err) {
        setError(err.message || String(err));
      } finally {
        setSaving(p => ({
          ...p,
          owner: false
        }));
      }
    },
    disabled: saving.owner
  }, saving.owner ? 'Saving…' : 'Save Identity'), saved.owner && /*#__PURE__*/React.createElement("span", {
    style: {
      color: 'var(--accent-green)',
      fontSize: 12
    }
  }, "\u2713 Broadcast to mesh"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginLeft: 'auto'
    }
  }, "Name change is sent to the radio immediately \u2014 node ID stays unchanged so mesh comms are unaffected."), owner.node_id && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontFamily: 'var(--font-mono)',
      color: 'var(--text-muted)'
    }
  }, owner.node_id, " \xB7 ", owner.hw_model))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Node Behaviour"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2",
    style: {
      marginBottom: 0
    }
  }, /*#__PURE__*/React.createElement(Row, {
    label: "Node Role",
    hint: "CLIENT=mobile user, ROUTER=fixed repeater, SENSOR=sensor-only"
  }, /*#__PURE__*/React.createElement("select", {
    style: IS,
    value: (_cfg$device$role = (_cfg$device = cfg.device) === null || _cfg$device === void 0 ? void 0 : _cfg$device.role) !== null && _cfg$device$role !== void 0 ? _cfg$device$role : 0,
    onChange: e => set('device', 'role', parseInt(e.target.value))
  }, DEVICE_ROLES.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, l)))), /*#__PURE__*/React.createElement(Row, {
    label: "Rebroadcast Mode",
    hint: "Controls which packets this node repeats"
  }, /*#__PURE__*/React.createElement("select", {
    style: IS,
    value: (_cfg$device$rebroadca = (_cfg$device2 = cfg.device) === null || _cfg$device2 === void 0 ? void 0 : _cfg$device2.rebroadcast_mode) !== null && _cfg$device$rebroadca !== void 0 ? _cfg$device$rebroadca : 0,
    onChange: e => set('device', 'rebroadcast_mode', parseInt(e.target.value))
  }, REBROADCAST_MODES.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, l)))), /*#__PURE__*/React.createElement(Row, {
    label: "Serial Output"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$device3 = cfg.device) !== null && _cfg$device3 !== void 0 && _cfg$device3.serial_enabled),
    onChange: e => set('device', 'serial_enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Enabled"))), /*#__PURE__*/React.createElement(Row, {
    label: "LED Heartbeat"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$device4 = cfg.device) !== null && _cfg$device4 !== void 0 && _cfg$device4.led_heartbeat_disabled),
    onChange: e => set('device', 'led_heartbeat_disabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Disabled"))), /*#__PURE__*/React.createElement(Row, {
    label: "Node Info Broadcast (s)",
    hint: "How often this node broadcasts its identity (0 = default)"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$device$node_info = (_cfg$device5 = cfg.device) === null || _cfg$device5 === void 0 ? void 0 : _cfg$device5.node_info_broadcast_secs) !== null && _cfg$device$node_info !== void 0 ? _cfg$device$node_info : 0,
    min: 0,
    onChange: e => set('device', 'node_info_broadcast_secs', parseInt(e.target.value))
  }))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "device"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "LoRa Radio"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "Region",
    hint: "Must match all nodes in your mesh"
  }, /*#__PURE__*/React.createElement("select", {
    style: IS,
    value: (_cfg$lora$region = (_cfg$lora = cfg.lora) === null || _cfg$lora === void 0 ? void 0 : _cfg$lora.region) !== null && _cfg$lora$region !== void 0 ? _cfg$lora$region : 0,
    onChange: e => set('lora', 'region', parseInt(e.target.value))
  }, LORA_REGIONS.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, l)))), /*#__PURE__*/React.createElement(Row, {
    label: "Modem Preset",
    hint: "LONG_FAST = best range + speed balance"
  }, /*#__PURE__*/React.createElement("select", {
    style: IS,
    value: (_cfg$lora$modem_prese = (_cfg$lora2 = cfg.lora) === null || _cfg$lora2 === void 0 ? void 0 : _cfg$lora2.modem_preset) !== null && _cfg$lora$modem_prese !== void 0 ? _cfg$lora$modem_prese : 0,
    onChange: e => set('lora', 'modem_preset', parseInt(e.target.value)),
    disabled: !((_cfg$lora3 = cfg.lora) !== null && _cfg$lora3 !== void 0 && _cfg$lora3.use_preset)
  }, MODEM_PRESETS.map(([v, l]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, l)))), /*#__PURE__*/React.createElement(Row, {
    label: "Use Preset",
    hint: "Disabling allows manual SF/BW/CR tuning"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$lora4 = cfg.lora) !== null && _cfg$lora4 !== void 0 && _cfg$lora4.use_preset),
    onChange: e => set('lora', 'use_preset', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Use Preset"))), /*#__PURE__*/React.createElement(Row, {
    label: "Hop Limit",
    hint: "1\u20137 \u2014 higher = longer range, more traffic"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$lora$hop_limit = (_cfg$lora5 = cfg.lora) === null || _cfg$lora5 === void 0 ? void 0 : _cfg$lora5.hop_limit) !== null && _cfg$lora$hop_limit !== void 0 ? _cfg$lora$hop_limit : 3,
    min: 1,
    max: 7,
    onChange: e => set('lora', 'hop_limit', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "TX Power (dBm)",
    hint: "0 = max (30 dBm) \xB7 lower to reduce battery draw"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$lora$tx_power = (_cfg$lora6 = cfg.lora) === null || _cfg$lora6 === void 0 ? void 0 : _cfg$lora6.tx_power) !== null && _cfg$lora$tx_power !== void 0 ? _cfg$lora$tx_power : 0,
    min: 0,
    max: 30,
    onChange: e => set('lora', 'tx_power', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "TX Enabled"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$lora7 = cfg.lora) !== null && _cfg$lora7 !== void 0 && _cfg$lora7.tx_enabled),
    onChange: e => set('lora', 'tx_enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Transmit Enabled"))), !((_cfg$lora8 = cfg.lora) !== null && _cfg$lora8 !== void 0 && _cfg$lora8.use_preset) && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Row, {
    label: "Bandwidth (kHz)",
    hint: "Manual preset override"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$lora$bandwidth = (_cfg$lora9 = cfg.lora) === null || _cfg$lora9 === void 0 ? void 0 : _cfg$lora9.bandwidth) !== null && _cfg$lora$bandwidth !== void 0 ? _cfg$lora$bandwidth : 250,
    min: 62,
    max: 500,
    onChange: e => set('lora', 'bandwidth', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Spread Factor",
    hint: "7\u201312 \u2014 higher SF = longer range, slower"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$lora$spread_fact = (_cfg$lora10 = cfg.lora) === null || _cfg$lora10 === void 0 ? void 0 : _cfg$lora10.spread_factor) !== null && _cfg$lora$spread_fact !== void 0 ? _cfg$lora$spread_fact : 11,
    min: 7,
    max: 12,
    onChange: e => set('lora', 'spread_factor', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Coding Rate",
    hint: "5\u20138 \u2014 higher = more error correction overhead"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$lora$coding_rate = (_cfg$lora11 = cfg.lora) === null || _cfg$lora11 === void 0 ? void 0 : _cfg$lora11.coding_rate) !== null && _cfg$lora$coding_rate !== void 0 ? _cfg$lora$coding_rate : 5,
    min: 5,
    max: 8,
    onChange: e => set('lora', 'coding_rate', parseInt(e.target.value))
  })))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "lora"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Position"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "GPS Update Interval (s)",
    hint: "How often to sample GPS internally"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$position$gps_upd = (_cfg$position = cfg.position) === null || _cfg$position === void 0 ? void 0 : _cfg$position.gps_update_interval) !== null && _cfg$position$gps_upd !== void 0 ? _cfg$position$gps_upd : 30,
    min: 1,
    onChange: e => set('position', 'gps_update_interval', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Position Broadcast (s)",
    hint: "How often to broadcast your location (0 = smart)"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$position$positio = (_cfg$position2 = cfg.position) === null || _cfg$position2 === void 0 ? void 0 : _cfg$position2.position_broadcast_secs) !== null && _cfg$position$positio !== void 0 ? _cfg$position$positio : 900,
    min: 0,
    onChange: e => set('position', 'position_broadcast_secs', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Smart Position",
    hint: "Adapts broadcast rate to movement speed"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$position3 = cfg.position) !== null && _cfg$position3 !== void 0 && _cfg$position3.position_broadcast_smart_enabled),
    onChange: e => set('position', 'position_broadcast_smart_enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Smart Position"))), /*#__PURE__*/React.createElement(Row, {
    label: "Fixed Position",
    hint: "Use manually entered coords instead of GPS"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$position4 = cfg.position) !== null && _cfg$position4 !== void 0 && _cfg$position4.fixed_position),
    onChange: e => set('position', 'fixed_position', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Fixed Position")))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "position"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Power"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "Power Save Mode",
    hint: "Reduces radio duty cycle to extend battery"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$power = cfg.power) !== null && _cfg$power !== void 0 && _cfg$power.is_power_saving),
    onChange: e => set('power', 'is_power_saving', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Power Saving"))), /*#__PURE__*/React.createElement(Row, {
    label: "Shutdown after (s)",
    hint: "0 = disabled \u2014 auto shutdown on battery after N seconds"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$power$on_battery = (_cfg$power2 = cfg.power) === null || _cfg$power2 === void 0 ? void 0 : _cfg$power2.on_battery_shutdown_after_secs) !== null && _cfg$power$on_battery !== void 0 ? _cfg$power$on_battery : 0,
    min: 0,
    onChange: e => set('power', 'on_battery_shutdown_after_secs', parseInt(e.target.value))
  })), /*#__PURE__*/React.createElement(Row, {
    label: "BT Sleep Timeout (s)",
    hint: "Seconds idle before Bluetooth enters sleep"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$power$wait_bluet = (_cfg$power3 = cfg.power) === null || _cfg$power3 === void 0 ? void 0 : _cfg$power3.wait_bluetooth_secs) !== null && _cfg$power$wait_bluet !== void 0 ? _cfg$power$wait_bluet : 0,
    min: 0,
    onChange: e => set('power', 'wait_bluetooth_secs', parseInt(e.target.value))
  }))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "power"
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 20,
      paddingBottom: 16,
      borderBottom: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Bluetooth"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "Bluetooth Enabled"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$bluetooth = cfg.bluetooth) !== null && _cfg$bluetooth !== void 0 && _cfg$bluetooth.enabled),
    onChange: e => set('bluetooth', 'enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Enabled"))), /*#__PURE__*/React.createElement(Row, {
    label: "Fixed PIN",
    hint: "0 = random PIN each boot"
  }, /*#__PURE__*/React.createElement("input", {
    type: "number",
    style: IS,
    value: (_cfg$bluetooth$fixed_ = (_cfg$bluetooth2 = cfg.bluetooth) === null || _cfg$bluetooth2 === void 0 ? void 0 : _cfg$bluetooth2.fixed_pin) !== null && _cfg$bluetooth$fixed_ !== void 0 ? _cfg$bluetooth$fixed_ : 0,
    min: 0,
    max: 999999,
    onChange: e => set('bluetooth', 'fixed_pin', parseInt(e.target.value))
  }))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "bluetooth"
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      ...SL,
      marginBottom: 8
    }
  }, "Network / WiFi"), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid-2"
  }, /*#__PURE__*/React.createElement(Row, {
    label: "WiFi Enabled",
    hint: "Enable onboard WiFi (uses more power)"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$network = cfg.network) !== null && _cfg$network !== void 0 && _cfg$network.wifi_enabled),
    onChange: e => set('network', 'wifi_enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "WiFi Enabled"))), /*#__PURE__*/React.createElement(Row, {
    label: "Address Mode",
    hint: "How the device obtains an IP address"
  }, /*#__PURE__*/React.createElement("select", {
    style: IS,
    value: (_cfg$network$address_ = (_cfg$network2 = cfg.network) === null || _cfg$network2 === void 0 ? void 0 : _cfg$network2.address_mode) !== null && _cfg$network$address_ !== void 0 ? _cfg$network$address_ : 0,
    onChange: e => set('network', 'address_mode', parseInt(e.target.value))
  }, /*#__PURE__*/React.createElement("option", {
    value: 0
  }, "DHCP (automatic)"), /*#__PURE__*/React.createElement("option", {
    value: 1
  }, "Static IP"))), /*#__PURE__*/React.createElement(Row, {
    label: "WiFi SSID",
    hint: "Network name to connect to"
  }, /*#__PURE__*/React.createElement("input", {
    type: "text",
    style: {
      ...IS,
      fontFamily: 'var(--font-mono)'
    },
    value: (_cfg$network$wifi_ssi = (_cfg$network3 = cfg.network) === null || _cfg$network3 === void 0 ? void 0 : _cfg$network3.wifi_ssid) !== null && _cfg$network$wifi_ssi !== void 0 ? _cfg$network$wifi_ssi : '',
    maxLength: 32,
    onChange: e => set('network', 'wifi_ssid', e.target.value),
    placeholder: "MyNetwork",
    autoComplete: "off"
  })), /*#__PURE__*/React.createElement(Row, {
    label: "WiFi Password",
    hint: "Leave blank to keep existing password"
  }, /*#__PURE__*/React.createElement(NetworkPasswordField, {
    value: (_cfg$network$wifi_psk = (_cfg$network4 = cfg.network) === null || _cfg$network4 === void 0 ? void 0 : _cfg$network4.wifi_psk) !== null && _cfg$network$wifi_psk !== void 0 ? _cfg$network$wifi_psk : '',
    onChange: v => set('network', 'wifi_psk', v),
    style: IS
  })), /*#__PURE__*/React.createElement(Row, {
    label: "NTP Server",
    hint: "Time sync server (blank = pool.ntp.org)"
  }, /*#__PURE__*/React.createElement("input", {
    type: "text",
    style: {
      ...IS,
      fontFamily: 'var(--font-mono)'
    },
    value: (_cfg$network$ntp_serv = (_cfg$network5 = cfg.network) === null || _cfg$network5 === void 0 ? void 0 : _cfg$network5.ntp_server) !== null && _cfg$network$ntp_serv !== void 0 ? _cfg$network$ntp_serv : '',
    onChange: e => set('network', 'ntp_server', e.target.value),
    placeholder: "pool.ntp.org",
    autoComplete: "off"
  })), /*#__PURE__*/React.createElement(Row, {
    label: "Ethernet",
    hint: "Enable wired Ethernet (hardware dependent)"
  }, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      cursor: 'pointer',
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: !!((_cfg$network6 = cfg.network) !== null && _cfg$network6 !== void 0 && _cfg$network6.eth_enabled),
    onChange: e => set('network', 'eth_enabled', e.target.checked),
    style: {
      accentColor: 'var(--accent-green)',
      width: 16,
      height: 16
    }
  }), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "Ethernet Enabled")))), /*#__PURE__*/React.createElement(SaveBar, {
    section: "network"
  })));
});

// Password field with show/hide toggle
function NetworkPasswordField({
  value,
  onChange,
  style
}) {
  const [show, setShow] = React.useState(false);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative'
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: show ? 'text' : 'password',
    style: {
      ...style,
      paddingRight: 36,
      fontFamily: 'var(--font-mono)'
    },
    value: value,
    onChange: e => onChange(e.target.value),
    placeholder: value ? '(unchanged)' : 'Password',
    autoComplete: "new-password"
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: () => setShow(s => !s),
    style: {
      position: 'absolute',
      right: 8,
      top: '50%',
      transform: 'translateY(-50%)',
      background: 'none',
      border: 'none',
      cursor: 'pointer',
      color: 'var(--text-muted)',
      padding: 2,
      display: 'flex',
      alignItems: 'center'
    },
    title: show ? 'Hide password' : 'Show password'
  }, show ? /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "1",
    y1: "1",
    x2: "23",
    y2: "23"
  })) : /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  }))));
}

// ═══════════════════════════════════════════════
// Favorites (persisted to server DB)
// ═══════════════════════════════════════════════
function useFavorites() {
  const [favorites, setFavorites] = useState([]);
  useEffect(() => {
    api.get('/api/nav/favorites').then(data => {
      if (Array.isArray(data)) setFavorites(data);
    });
  }, []);
  async function saveFav(name, lat, lon) {
    const fav = await api.post('/api/nav/favorites', {
      name,
      lat,
      lon
    });
    if (fav && !fav.error) setFavorites(prev => [...prev, fav].sort((a, b) => a.name.localeCompare(b.name)));
    return fav;
  }
  async function renameFav(id, name) {
    const res = await api.patch(`/api/nav/favorites/${id}`, {
      name
    });
    if (res && !res.error) setFavorites(prev => prev.map(f => f.id === id ? {
      ...f,
      name
    } : f).sort((a, b) => a.name.localeCompare(b.name)));
    return res;
  }
  function removeFav(id) {
    api.del(`/api/nav/favorites/${id}`);
    setFavorites(prev => prev.filter(f => f.id !== id));
  }
  return {
    favorites,
    saveFav,
    renameFav,
    removeFav
  };
}

// ════════════════════════════════════════════════════════════════════════════
// Navigate Page — MapLibre GL, fully offline OSRM routing
// ════════════════════════════════════════════════════════════════════════════

function fmtNavDist(m) {
  if (m == null) return '—';
  const ft = m * 3.28084;
  if (ft < 1000) return `${Math.round(ft)} ft`;
  const mi = ft / 5280;
  return mi < 10 ? `${mi.toFixed(1)} mi` : `${Math.round(mi)} mi`;
}
function fmtNavDuration(s) {
  if (s == null) return '—';
  const m = Math.floor(s / 60),
    h = Math.floor(m / 60);
  return h > 0 ? `${h}h ${m % 60}m` : `${m} min`;
}
function isAtlasMobileClient() {
  const searchParams = new URLSearchParams(window.location.search);
  return searchParams.get('embedded') === '1' || /AtlasMobile(Android|IOS)\//.test(navigator.userAgent);
}
function navStepIcon(type, isWalking, size = 16) {
  const s = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: '2',
    strokeLinecap: 'round',
    strokeLinejoin: 'round'
  };
  switch (type) {
    case 'depart':
      return isWalking ? /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("circle", {
        cx: "12",
        cy: "5",
        r: "1",
        fill: "currentColor",
        stroke: "none"
      }), /*#__PURE__*/React.createElement("path", {
        d: "M9 19l2-6 3 4 1-3"
      }), /*#__PURE__*/React.createElement("path", {
        d: "M14 9l-2 1-1 3"
      })) : /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("path", {
        d: "M5 12h14"
      }), /*#__PURE__*/React.createElement("path", {
        d: "M12 5l7 7-7 7"
      }));
    case 'arrive':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("path", {
        d: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"
      }), /*#__PURE__*/React.createElement("line", {
        x1: "4",
        y1: "22",
        x2: "4",
        y2: "15"
      }));
    case 'roundabout':
    case 'rotary':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("polyline", {
        points: "23 4 23 10 17 10"
      }), /*#__PURE__*/React.createElement("path", {
        d: "M20.49 15a9 9 0 11-2.12-9.36L23 10"
      }));
    case 'merge':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("path", {
        d: "M8 3v9a4 4 0 004 4h8"
      }), /*#__PURE__*/React.createElement("polyline", {
        points: "16 12 20 16 16 20"
      }));
    case 'on ramp':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("line", {
        x1: "5",
        y1: "19",
        x2: "19",
        y2: "5"
      }), /*#__PURE__*/React.createElement("polyline", {
        points: "13 5 19 5 19 11"
      }));
    case 'off ramp':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("line", {
        x1: "5",
        y1: "5",
        x2: "19",
        y2: "19"
      }), /*#__PURE__*/React.createElement("polyline", {
        points: "13 19 19 19 19 13"
      }));
    case 'fork':
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("line", {
        x1: "6",
        y1: "3",
        x2: "6",
        y2: "15"
      }), /*#__PURE__*/React.createElement("circle", {
        cx: "18",
        cy: "6",
        r: "3"
      }), /*#__PURE__*/React.createElement("circle", {
        cx: "6",
        cy: "18",
        r: "3"
      }), /*#__PURE__*/React.createElement("path", {
        d: "M18 9a9 9 0 01-9 9"
      }));
    default:
      return /*#__PURE__*/React.createElement("svg", s, /*#__PURE__*/React.createElement("line", {
        x1: "12",
        y1: "19",
        x2: "12",
        y2: "5"
      }), /*#__PURE__*/React.createElement("polyline", {
        points: "5 12 12 5 19 12"
      }));
  }
}
function navStepLabel(maneuver, mode) {
  const isHiking = mode === 'hiking';
  const verb = isHiking ? 'Bear' : 'Turn';
  const modMap = {
    left: '${verb} left',
    'slight left': 'Slight left',
    'sharp left': 'Sharp left',
    right: '${verb} right',
    'slight right': 'Slight right',
    'sharp right': 'Sharp right',
    straight: 'Continue straight',
    uturn: 'U-turn'
  };
  // fix template literal issue with verb
  modMap.left = `${verb} left`;
  modMap.right = `${verb} right`;
  if (maneuver.type === 'depart') return isHiking ? 'Start hiking' : 'Depart';
  if (maneuver.type === 'arrive') return 'Arrive at destination';
  if (maneuver.type === 'continue' || maneuver.type === 'new name') return 'Continue straight';
  if (maneuver.type === 'use lane') return 'Keep ' + (maneuver.modifier || 'straight');
  return modMap[maneuver.modifier] || maneuver.type.charAt(0).toUpperCase() + maneuver.type.slice(1);
}
const NAV_MODES = [{
  value: 'road',
  label: 'Road',
  color: '#3b82f6',
  icon: /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "1",
    y: "3",
    width: "15",
    height: "13",
    rx: "2"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M16 8h4l3 5v3h-7V8z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "5.5",
    cy: "18.5",
    r: "2.5"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "18.5",
    cy: "18.5",
    r: "2.5"
  }))
}, {
  value: 'hiking',
  label: 'Hike',
  color: '#f59e0b',
  icon: /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M3 17l4-8 3 5 2-3 4 6"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M17 3l-3 14"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "16",
    cy: "3",
    r: "1",
    fill: "currentColor",
    stroke: "none"
  }))
}];
function NavigatePage({
  nodes,
  device,
  gpsStatus,
  initialDest,
  onDestConsumed,
  defaultMode = 'road',
  availableModes = NAV_MODES,
  forceTrailOverlay = false,
  autoShowTrailOverlay = true,
  searchPlaceholder = 'Search address, place, or lat, lon… (or tap map)'
}) {
  var _device$my_node_id2, _ref, _gpsFix$latitude, _ref2, _gpsFix$longitude;
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const userMarkerRef = useRef(null);
  const destMarkerRef = useRef(null);
  const followRef = useRef(false);
  const routedRef = useRef(false);
  const offRouteSince = useRef(null);
  const reroutingRef = useRef(false);
  const lastCheckRef = useRef(0);
  const pendingFlyRef = useRef(null); // destination to fly to once map is loaded
  const autoRouteRef = useRef(false); // set when Navigate Here triggers destination
  const pendingFitRef = useRef(false); // set by getRoute; sync effect fits bounds

  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [destination, setDestination] = useState(null);
  const [mode, setMode] = useState(defaultMode);
  const [route, setRoute] = useState(null);
  const [routing, setRouting] = useState(false);
  const [rerouting, setRerouting] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [followMode, setFollowMode] = useState(false);
  const [error, setError] = useState(null);
  const [showFavorites, setShowFavorites] = useState(false);
  const [savingFav, setSavingFav] = useState(null); // { lat, lon } for new pin
  const [renamingFav, setRenamingFav] = useState(null); // { id, name } for rename
  const [favNameInput, setFavNameInput] = useState('');
  const [mapLoaded, setMapLoaded] = useState(false);
  const [showTopo, setShowTopo] = useState(false);
  const [showHikeTrails, setShowHikeTrails] = useState(false);
  const [directionsOpen, setDirectionsOpen] = useState(() => !isAtlasMobileClient());
  const {
    favorites,
    saveFav,
    renameFav,
    removeFav
  } = useFavorites();
  const myNodeId = (_device$my_node_id2 = device === null || device === void 0 ? void 0 : device.my_node_id) !== null && _device$my_node_id2 !== void 0 ? _device$my_node_id2 : null;
  const myNode = myNodeId ? nodes.find(n => n.node_id === myNodeId) : null;
  const gpsFix = gpsStatus === null || gpsStatus === void 0 ? void 0 : gpsStatus.fix;
  const posLat = (_ref = (_gpsFix$latitude = gpsFix === null || gpsFix === void 0 ? void 0 : gpsFix.latitude) !== null && _gpsFix$latitude !== void 0 ? _gpsFix$latitude : myNode === null || myNode === void 0 ? void 0 : myNode.latitude) !== null && _ref !== void 0 ? _ref : null;
  const posLon = (_ref2 = (_gpsFix$longitude = gpsFix === null || gpsFix === void 0 ? void 0 : gpsFix.longitude) !== null && _gpsFix$longitude !== void 0 ? _gpsFix$longitude : myNode === null || myNode === void 0 ? void 0 : myNode.longitude) !== null && _ref2 !== void 0 ? _ref2 : null;

  // ── Init map ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const center = posLat != null && posLon != null ? [posLon, posLat] : [-98.35, 39.5];
    const zoom = posLat != null && posLon != null ? 14 : 4;
    const m = makeAtlasMap(containerRef.current, {
      center,
      zoom
    });
    if (!m) return;
    m.on('error', e => console.error('[atlas-map] navigate error:', e.error));
    m.resize();
    const resizeTimer = setTimeout(() => m.resize(), 200);
    m.on('load', () => {
      // Route source — two layers: solid and dashed (straight-line fallback)
      m.addSource('route', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: []
        }
      });
      m.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'route',
        paint: {
          'line-color': '#000',
          'line-width': 9,
          'line-opacity': 0.4
        }
      });
      m.addLayer({
        id: 'route-solid',
        type: 'line',
        source: 'route',
        filter: ['!', ['get', 'dashed']],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 5,
          'line-opacity': 0.95
        }
      });
      m.addLayer({
        id: 'route-dashed',
        type: 'line',
        source: 'route',
        filter: ['get', 'dashed'],
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 5,
          'line-opacity': 0.95,
          'line-dasharray': [8, 6]
        }
      });
      setMapLoaded(true);
    });
    m.on('click', e => {
      const {
        lng,
        lat
      } = e.lngLat;
      setDestination({
        lat,
        lon: lng,
        name: `${lat.toFixed(5)}, ${lng.toFixed(5)}`
      });
    });
    m.on('dragstart', () => {
      followRef.current = false;
      setFollowMode(false);
    });
    mapRef.current = m;
    return () => {
      clearTimeout(resizeTimer);
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []); // eslint-disable-line

  // ── Apply destination passed in from map popup "Navigate Here" ───
  useEffect(() => {
    if (!initialDest) return;
    autoRouteRef.current = true;
    setDestination(initialDest);
    setQuery(initialDest.name || '');
    const m = mapRef.current;
    if (m && m.loaded()) {
      m.flyTo({
        center: [initialDest.lon, initialDest.lat],
        zoom: 14
      });
    } else {
      // Map not loaded yet (tab just switched) — store for when load fires
      pendingFlyRef.current = initialDest;
    }
    if (onDestConsumed) onDestConsumed();
  }, [initialDest]); // eslint-disable-line

  // ── Fly to pending popup destination once map is ready ───────────
  useEffect(() => {
    if (!mapLoaded) return;
    const dest = pendingFlyRef.current;
    if (!dest) return;
    pendingFlyRef.current = null;
    const m = mapRef.current;
    if (m) m.flyTo({
      center: [dest.lon, dest.lat],
      zoom: 14
    });
  }, [mapLoaded]);

  // ── Auto-route when Navigate Here sets destination ───────────────
  // Fires once destination + mapLoaded are both ready after a Navigate Here action.
  useEffect(() => {
    if (!autoRouteRef.current) return;
    if (!destination || !mapLoaded) return;
    if (posLat == null || posLon == null) return;
    autoRouteRef.current = false;
    getRoute();
  }, [destination, mapLoaded, posLat, posLon]); // eslint-disable-line

  // ── User position marker + follow ────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!m || posLat == null || posLon == null) return;
    if (userMarkerRef.current) {
      userMarkerRef.current.setLngLat([posLon, posLat]);
    } else {
      const el = document.createElement('div');
      el.style.cssText = 'width:16px;height:16px;background:#00e5a0;border:3px solid #fff;border-radius:50%;box-shadow:0 0 0 2px #00e5a0,0 0 12px #00e5a066;';
      userMarkerRef.current = new maplibregl.Marker({
        element: el
      }).setLngLat([posLon, posLat]).setPopup(new maplibregl.Popup({
        offset: 12
      }).setHTML('<b>Your Location</b>')).addTo(m);
    }
    if (followRef.current) m.panTo([posLon, posLat]);
  }, [posLat, posLon]);

  // ── Destination marker ───────────────────────────────────────────
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    if (!destination) {
      if (destMarkerRef.current) {
        destMarkerRef.current.remove();
        destMarkerRef.current = null;
      }
      return;
    }
    if (destMarkerRef.current) {
      destMarkerRef.current.setLngLat([destination.lon, destination.lat]);
      destMarkerRef.current.getPopup().setHTML(`<b>${escHtml(destination.name || 'Destination')}</b>`);
    } else {
      const el = document.createElement('div');
      el.style.cssText = 'width:0;height:0;border-left:9px solid transparent;border-right:9px solid transparent;border-top:22px solid #ef4444;filter:drop-shadow(0 2px 4px rgba(0,0,0,.5));';
      destMarkerRef.current = new maplibregl.Marker({
        element: el,
        anchor: 'bottom'
      }).setLngLat([destination.lon, destination.lat]).setPopup(new maplibregl.Popup({
        offset: 12
      }).setHTML(`<b>${escHtml(destination.name || 'Destination')}</b>`)).addTo(m);
    }
  }, [destination]);

  // ── Geocode ──────────────────────────────────────────────────────
  async function searchAddress() {
    if (!query.trim()) return;
    const rawQuery = query.trim();
    const coordMatch = query.trim().match(/^([+-]?\d+\.?\d*)\s*[,\s]\s*([+-]?\d+\.?\d*)$/);
    if (coordMatch) {
      const lat = parseFloat(coordMatch[1]),
        lon = parseFloat(coordMatch[2]);
      if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
        const name = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
        setDestination({
          lat,
          lon,
          name
        });
        setResults([]);
        setQuery(name);
        setError(null);
        const m = mapRef.current;
        if (m) {
          if (posLat != null && posLon != null) m.fitBounds([[Math.min(posLon, lon), Math.min(posLat, lat)], [Math.max(posLon, lon), Math.max(posLat, lat)]], {
            padding: 60
          });else m.setCenter([lon, lat]);
        }
        return;
      }
    }
    setSearching(true);
    setError(null);
    setResults([]);
    try {
      let url = '/api/nav/geocode?q=' + encodeURIComponent(rawQuery);
      if (posLat != null && posLon != null) url += `&near_lat=${posLat}&near_lon=${posLon}`;
      const data = await api.get(url);
      let list = Array.isArray(data) ? data : [];
      if (posLat != null && posLon != null && list.length > 1) list = list.slice().sort((a, b) => (haversineKm(posLat, posLon, parseFloat(a.lat), parseFloat(a.lon)) || 99999) - (haversineKm(posLat, posLon, parseFloat(b.lat), parseFloat(b.lon)) || 99999));
      setResults(list);
      if (!list.length) {
        setError(isHiking ? 'No park or trail matches in the local index. Try a National Park name, a trail name, coordinates, or tap the map.' : 'No results. Enter coordinates (lat, lon) or tap the map to set a destination.');
      }
    } catch (err) {
      const fallback = isHiking ? 'Trail search is unavailable right now. Check the local trail index or enter coordinates.' : 'Search unavailable offline. Enter coordinates or tap the map.';
      setError((err === null || err === void 0 ? void 0 : err.message) || fallback);
    } finally {
      setSearching(false);
    }
  }
  function selectResult(r) {
    const lat = parseFloat(r.lat),
      lon = parseFloat(r.lon);
    setDestination({
      lat,
      lon,
      name: r.display_name
    });
    setResults([]);
    setQuery(r.display_name);
    const m = mapRef.current;
    if (m) {
      if (posLat != null && posLon != null) m.fitBounds([[Math.min(posLon, lon), Math.min(posLat, lat)], [Math.max(posLon, lon), Math.max(posLat, lat)]], {
        padding: 60
      });else m.setCenter([lon, lat]);
    }
  }

  // ── Favorites helpers ────────────────────────────────────────────
  function openSaveFav(lat, lon, suggestion) {
    setFavNameInput(suggestion.slice(0, 60));
    setSavingFav({
      lat,
      lon
    });
  }
  function confirmSaveFav() {
    const name = favNameInput.trim() || 'Unnamed';
    if (renamingFav) {
      renameFav(renamingFav.id, name);
      setRenamingFav(null);
    } else if (savingFav) {
      saveFav(name, savingFav.lat, savingFav.lon);
      setSavingFav(null);
    }
  }
  function openRenameFav(fav) {
    setFavNameInput(fav.name);
    setRenamingFav({
      id: fav.id,
      name: fav.name
    });
  }
  function goToFavorite(fav) {
    setDestination({
      lat: fav.lat,
      lon: fav.lon,
      name: fav.name
    });
    setQuery(fav.name);
    const m = mapRef.current;
    if (m) {
      if (posLat != null && posLon != null) m.fitBounds([[Math.min(posLon, fav.lon), Math.min(posLat, fav.lat)], [Math.max(posLon, fav.lon), Math.max(posLat, fav.lat)]], {
        padding: 50
      });else m.setCenter([fav.lon, fav.lat]);
    }
  }

  // ── Route request ────────────────────────────────────────────────
  async function getRoute() {
    if (!destination || posLat == null || posLon == null) {
      setError('Need a GPS fix and a destination.');
      return;
    }
    setRouting(true);
    setError(null);
    try {
      var _data$routes;
      if (mode === 'hiking') {
        api.post('/api/nav/routing/prewarm', {
          lat: destination.lat,
          lon: destination.lon,
          profile: 'hiking'
        }).catch(() => {});
      }
      const data = await api.get(`/api/nav/route?from_lat=${posLat}&from_lon=${posLon}` + `&to_lat=${destination.lat}&to_lon=${destination.lon}&mode=${mode}`);
      if (data.error || !((_data$routes = data.routes) !== null && _data$routes !== void 0 && _data$routes.length)) {
        let msg = data.error || 'No route found.';
        if (data.straight_line_m != null) msg += ` Straight-line: ${fmtNavDist(data.straight_line_m)}.`;
        setError(msg);
        return;
      }
      setRoute(data);
      setCurrentStep(0);
      routedRef.current = true;
      followRef.current = true;
      setFollowMode(true);
      offRouteSince.current = null;
      reroutingRef.current = false;
      setRerouting(false);
      // Signal the sync effect to fit bounds on the next render.
      // We don't render here directly because mapLoaded in this closure may be
      // a stale value from an earlier render. The sync effect uses mapRef.current
      // and m.loaded() which always reflect real map state.
      pendingFitRef.current = true;
    } catch (e) {
      setError((e === null || e === void 0 ? void 0 : e.message) || 'Routing failed — check connectivity.');
    } finally {
      setRouting(false);
    }
  }
  function clearRoute() {
    setDestination(null);
    setRoute(null);
    setQuery('');
    setResults([]);
    setError(null);
    setCurrentStep(0);
    routedRef.current = false;
    followRef.current = false;
    setFollowMode(false);
    offRouteSince.current = null;
    reroutingRef.current = false;
    setRerouting(false);
    const m = mapRef.current;
    if (m && mapLoaded) {
      const src = m.getSource('route');
      if (src) src.setData({
        type: 'FeatureCollection',
        features: []
      });
    }
    if (destMarkerRef.current) {
      destMarkerRef.current.remove();
      destMarkerRef.current = null;
    }
  }
  useEffect(() => {
    setMode(defaultMode);
  }, [defaultMode]);

  // Re-route when mode changes if already routed
  useEffect(() => {
    if (routedRef.current && destination && posLat != null && posLon != null) getRoute();
  }, [mode]); // eslint-disable-line

  // Auto-advance step when within 30 m of maneuver point
  useEffect(() => {
    var _route$routes$;
    if (!route || posLat == null || posLon == null) return;
    const steps = (_route$routes$ = route.routes[0]) === null || _route$routes$ === void 0 || (_route$routes$ = _route$routes$.legs) === null || _route$routes$ === void 0 ? void 0 : _route$routes$.flatMap(leg => leg.steps || []);
    if (!steps || currentStep >= steps.length - 1) return;
    const [sLon, sLat] = steps[currentStep].maneuver.location;
    const dist = haversineKm(posLat, posLon, sLat, sLon);
    if (dist !== null && dist < 0.03) setCurrentStep(p => Math.min(p + 1, steps.length - 1));
  }, [posLat, posLon, route, currentStep]);

  // Keep map route source in sync with route state — single authoritative render path.
  // mapLoaded in the dep array guarantees the source exists before we try setData.
  useEffect(() => {
    var _route$routes;
    const m = mapRef.current;
    if (!m) return;
    const src = m.getSource('route');
    if (!src) return;
    if (!(route !== null && route !== void 0 && (_route$routes = route.routes) !== null && _route$routes !== void 0 && _route$routes[0])) {
      src.setData({
        type: 'FeatureCollection',
        features: []
      });
      return;
    }
    const geom = route.routes[0].geometry;
    const isStraight = route.routes[0]._source === 'straight_line';
    const modeColor = {
      road: '#3b82f6',
      hiking: '#f59e0b'
    }[mode] || '#3b82f6';
    src.setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: geom,
        properties: {
          color: isStraight ? '#ef4444' : modeColor,
          dashed: isStraight
        }
      }]
    });
    // Fit the viewport to the full route when getRoute signals a new route.
    if (pendingFitRef.current) {
      pendingFitRef.current = false;
      const coords = geom.coordinates;
      if (coords && coords.length > 1) {
        const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
        m.fitBounds(bounds, {
          padding: 60,
          maxZoom: 16
        });
      }
    }
  }, [route, mapLoaded, mode]);

  // Off-route detection → auto reroute (throttled to 2 s)
  useEffect(() => {
    var _route$routes$2, _route$routes$3;
    if (!route || posLat == null || posLon == null) return;
    if (routing || reroutingRef.current) return;
    const geom = (_route$routes$2 = route.routes[0]) === null || _route$routes$2 === void 0 ? void 0 : _route$routes$2.geometry;
    if (!geom || ((_route$routes$3 = route.routes[0]) === null || _route$routes$3 === void 0 ? void 0 : _route$routes$3._source) === 'straight_line') return;
    const now = Date.now();
    if (now - lastCheckRef.current < 2000) return;
    lastCheckRef.current = now;
    const thresh = {
      road: [0.05, 5000],
      hiking: [0.035, 8000]
    };
    const [maxDist, confirmMs] = thresh[mode] || [0.04, 6000];
    const d = distToRouteKm(posLat, posLon, geom);
    if (d === null) return;
    if (d > maxDist) {
      if (!offRouteSince.current) offRouteSince.current = now;else if (now - offRouteSince.current >= confirmMs) {
        offRouteSince.current = null;
        reroutingRef.current = true;
        setRerouting(true);
        getRoute();
      }
    } else offRouteSince.current = null;
  }, [posLat, posLon, route]); // eslint-disable-line

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapLoaded) return;
    try {
      m.setLayoutProperty('atlas-topo', 'visibility', showTopo ? 'visible' : 'none');
    } catch (_) {}
  }, [showTopo, mapLoaded]);

  // Auto-enable hiking trail overlay when switching to hiking mode
  useEffect(() => {
    setShowHikeTrails(forceTrailOverlay || autoShowTrailOverlay && mode === 'hiking');
  }, [mode, forceTrailOverlay, autoShowTrailOverlay]);

  // Toggle AllTrails-style trail overlay layers
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !mapLoaded) return;
    const vis = showHikeTrails ? 'visible' : 'none';
    try {
      m.setLayoutProperty('atlas-hike-casing', 'visibility', vis);
    } catch (_) {}
    try {
      m.setLayoutProperty('atlas-hike-line', 'visibility', vis);
    } catch (_) {}
    try {
      m.setLayoutProperty('atlas-hike-track', 'visibility', vis);
    } catch (_) {}
    try {
      m.setLayoutProperty('atlas-hike-label', 'visibility', vis);
    } catch (_) {}
  }, [showHikeTrails, mapLoaded]);
  const routeData = useMemo(() => route === null || route === void 0 ? void 0 : route.routes[0], [route]);
  const steps = useMemo(() => {
    var _routeData$legs;
    return (routeData === null || routeData === void 0 || (_routeData$legs = routeData.legs) === null || _routeData$legs === void 0 ? void 0 : _routeData$legs.flatMap(leg => leg.steps || [])) || [];
  }, [routeData]);
  const isHiking = mode === 'hiking';
  const speedNote = isHiking ? '@ ~2–3 mph on trail' : null;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      flex: 1,
      minHeight: 0,
      overflow: 'visible',
      padding: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '10px 14px',
      flexShrink: 0,
      position: 'relative',
      zIndex: 20,
      overflow: 'visible'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center',
      flexWrap: 'wrap',
      position: 'relative',
      zIndex: 20,
      overflow: 'visible'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 200,
      position: 'relative'
    }
  }, /*#__PURE__*/React.createElement("input", {
    value: query,
    onChange: e => setQuery(e.target.value),
    onKeyDown: e => e.key === 'Enter' && searchAddress(),
    onBlur: () => setTimeout(() => setResults([]), 150),
    placeholder: searchPlaceholder,
    style: {
      width: '100%',
      padding: '8px 12px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 14,
      outline: 'none'
    }
  }), results.length > 0 && /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'absolute',
      top: 'calc(100% + 4px)',
      left: 0,
      right: 0,
      background: 'var(--bg-card)',
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius)',
      zIndex: 2500,
      maxHeight: 220,
      overflowY: 'auto',
      boxShadow: 'var(--shadow)'
    }
  }, results.slice(0, 12).map((r, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    onClick: () => selectResult(r),
    style: {
      padding: '9px 12px',
      cursor: 'pointer',
      fontSize: 13,
      borderBottom: '1px solid var(--border)',
      color: 'var(--text-primary)',
      lineHeight: 1.3
    },
    onMouseEnter: e => e.currentTarget.style.background = 'var(--bg-card-hover)',
    onMouseLeave: e => e.currentTarget.style.background = ''
  }, /*#__PURE__*/React.createElement("div", null, r.display_name), r.subtitle && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-secondary)',
      marginTop: 2
    }
  }, r.subtitle), r.type && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginTop: 2
    }
  }, r.type))))), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: searchAddress,
    disabled: searching
  }, searching ? '…' : 'Search'), availableModes.map(m => /*#__PURE__*/React.createElement("button", {
    key: m.value,
    onClick: () => setMode(m.value),
    title: m.label,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 5,
      padding: '6px 10px',
      borderRadius: 'var(--radius)',
      fontSize: 12,
      fontWeight: 600,
      cursor: 'pointer',
      background: mode === m.value ? m.color + '22' : 'var(--bg-input)',
      border: `1px solid ${mode === m.value ? m.color : 'var(--border)'}`,
      color: mode === m.value ? m.color : 'var(--text-secondary)',
      transition: 'all 0.15s'
    }
  }, m.icon, m.label)), isHiking && /*#__PURE__*/React.createElement("button", {
    onClick: () => setShowHikeTrails(t => !t),
    title: "Toggle trail overlay",
    disabled: forceTrailOverlay,
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 5,
      padding: '6px 10px',
      borderRadius: 'var(--radius)',
      fontSize: 12,
      fontWeight: 600,
      cursor: 'pointer',
      background: showHikeTrails ? '#16a34a33' : 'var(--bg-input)',
      border: `1px solid ${showHikeTrails ? '#22c55e' : 'var(--border)'}`,
      color: showHikeTrails ? '#22c55e' : 'var(--text-secondary)',
      transition: 'all 0.15s'
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M3 17l4-8 3 3 3-5 4 7"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M3 21h18"
  })), "Trails"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: getRoute,
    disabled: routing || rerouting || !destination || posLat == null
  }, rerouting ? 'Rerouting…' : routing ? 'Routing…' : 'Go'), (destination || route) && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: clearRoute
  }, "Clear"), destination && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => {
      const sug = (destination.name || '').slice(0, 60);
      openSaveFav(destination.lat, destination.lon, sug);
    },
    style: {
      color: 'var(--accent-amber)',
      display: 'flex',
      alignItems: 'center',
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("polygon", {
    points: "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
  })), "Save"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setShowFavorites(f => !f),
    style: {
      background: showFavorites ? 'var(--accent-amber-dim)' : '',
      borderColor: showFavorites ? 'var(--accent-amber)' : '',
      color: showFavorites ? 'var(--accent-amber)' : '',
      display: 'flex',
      alignItems: 'center',
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("polygon", {
    points: "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
  })), favorites.length > 0 ? `Favorites (${favorites.length})` : 'Favorites')), error && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      color: 'var(--accent-red)',
      fontSize: 13
    }
  }, error), rerouting && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      color: 'var(--accent-amber)',
      fontSize: 12,
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'inline-block',
      width: 8,
      height: 8,
      borderRadius: '50%',
      background: 'var(--accent-amber)',
      animation: 'pulse 1s infinite'
    }
  }), "Off route \u2014 calculating new route\u2026"), !posLat && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6,
      color: 'var(--accent-amber)',
      fontSize: 12
    }
  }, "No GPS fix \u2014 tap the map to set a destination anyway.")), showFavorites && /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '10px 14px',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      flex: 1
    }
  }, "Saved Favorites"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    disabled: posLat == null,
    onClick: () => openSaveFav(posLat, posLon, `Pin ${new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    })}`),
    style: {
      fontSize: 12,
      display: 'flex',
      alignItems: 'center',
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "10",
    r: "2.5",
    fill: "currentColor",
    stroke: "none"
  })), "Drop Pin Here")), favorites.length === 0 ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)'
    }
  }, "No favorites yet \u2014 search for a place and tap Save, or tap Drop Pin Here.") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      maxHeight: 180,
      overflowY: 'auto'
    }
  }, favorites.map(fav => {
    const dKm = posLat != null && posLon != null ? haversineKm(posLat, posLon, fav.lat, fav.lon) : null;
    return /*#__PURE__*/React.createElement("div", {
      key: fav.id,
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 10px',
        background: 'var(--bg-input)',
        borderRadius: 'var(--radius)',
        border: '1px solid var(--border)'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--accent-amber)',
        flexShrink: 0
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "14",
      height: "14",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    }, /*#__PURE__*/React.createElement("polygon", {
      points: "12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
    }))), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 13,
        fontWeight: 500,
        color: 'var(--text-primary)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap'
      }
    }, fav.name), dKm != null && /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        fontFamily: 'var(--font-mono)'
      }
    }, fmtNavDist(dKm * 1000), " away")), /*#__PURE__*/React.createElement("button", {
      className: "btn btn-primary btn-sm",
      style: {
        fontSize: 11,
        padding: '3px 10px'
      },
      onClick: () => goToFavorite(fav)
    }, "Go"), /*#__PURE__*/React.createElement("button", {
      onClick: () => openRenameFav(fav),
      title: "Rename",
      style: {
        background: 'none',
        border: 'none',
        cursor: 'pointer',
        color: 'var(--text-muted)',
        padding: '0 2px',
        lineHeight: 1,
        display: 'flex',
        alignItems: 'center'
      }
    }, /*#__PURE__*/React.createElement("svg", {
      width: "13",
      height: "13",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round"
    }, /*#__PURE__*/React.createElement("path", {
      d: "M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"
    }), /*#__PURE__*/React.createElement("path", {
      d: "M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"
    }))), /*#__PURE__*/React.createElement("button", {
      onClick: () => removeFav(fav.id),
      title: "Delete",
      style: {
        background: 'none',
        border: 'none',
        cursor: 'pointer',
        color: 'var(--text-muted)',
        fontSize: 18,
        padding: '0 2px',
        lineHeight: 1
      }
    }, "\xD7"));
  }))), /*#__PURE__*/React.createElement("div", {
    className: "nav-map-directions",
    style: {
      flex: 1,
      display: 'flex',
      gap: 12,
      minHeight: 0,
      position: 'relative',
      zIndex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 2,
      minWidth: 0,
      position: 'relative',
      borderRadius: 'var(--radius-lg)',
      border: '1px solid var(--border)',
      overflow: 'hidden'
    }
  }, /*#__PURE__*/React.createElement("div", {
    ref: containerRef,
    style: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0
    }
  }), /*#__PURE__*/React.createElement("button", {
    onClick: () => setShowTopo(v => !v),
    title: "Toggle topo overlay",
    style: {
      position: 'absolute',
      top: 12,
      right: 12,
      zIndex: 1000,
      padding: '6px 10px',
      borderRadius: 16,
      cursor: 'pointer',
      fontSize: 12,
      fontWeight: 600,
      background: showTopo ? 'rgba(167,139,250,0.85)' : 'var(--bg-card)',
      color: showTopo ? '#fff' : 'var(--text-muted)',
      border: showTopo ? 'none' : '1px solid var(--border)',
      boxShadow: 'var(--shadow)'
    }
  }, "Topo"), routedRef.current && /*#__PURE__*/React.createElement("button", {
    onClick: () => {
      followRef.current = true;
      setFollowMode(true);
      if (mapRef.current && posLat != null && posLon != null) mapRef.current.panTo([posLon, posLat]);
    },
    style: {
      position: 'absolute',
      bottom: 16,
      right: 16,
      zIndex: 1000,
      padding: '8px 14px',
      borderRadius: 20,
      cursor: 'pointer',
      background: followMode ? 'var(--accent-green)' : 'var(--bg-card)',
      color: followMode ? 'var(--bg-primary)' : 'var(--text-primary)',
      border: followMode ? 'none' : '1px solid var(--border)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 600,
      boxShadow: 'var(--shadow)',
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5"
  }, /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "12",
    r: "3"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M12 2v4M12 18v4M2 12h4M18 12h4"
  })), followMode ? 'Following' : 'Re-center')), routeData && /*#__PURE__*/React.createElement("div", {
    className: "nav-directions-wrapper"
  }, /*#__PURE__*/React.createElement("div", {
    className: `nav-directions-toggle ${directionsOpen ? 'open' : ''}`,
    onClick: () => setDirectionsOpen(o => !o)
  }, /*#__PURE__*/React.createElement("span", {
    className: "nav-directions-toggle-label"
  }, "Directions"), /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5"
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "6 9 12 15 18 9"
  }))), /*#__PURE__*/React.createElement("div", {
    className: `nav-directions-panel ${directionsOpen ? '' : 'closed'}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '12px 14px',
      flexShrink: 0,
      borderRadius: '0 0 0 0'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 8
    }
  }, "Route Summary"), routeData._source === 'straight_line' && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--accent-amber)',
      background: 'rgba(245,158,11,0.1)',
      border: '1px solid rgba(245,158,11,0.3)',
      borderRadius: 4,
      padding: '4px 8px',
      marginBottom: 8
    }
  }, "Straight-line estimate \u2014 no OSRM data for this area. Run: ", /*#__PURE__*/React.createElement("code", null, "bash setup_osrm_all.sh [state]")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 20
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "DISTANCE"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--accent-blue)'
    }
  }, fmtNavDist(routeData.distance))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)'
    }
  }, "ETA"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--accent-green)'
    }
  }, fmtNavDuration(routeData.duration)), speedNote && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 10,
      color: 'var(--text-muted)',
      marginTop: 2
    }
  }, speedNote)))), steps[currentStep] && /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '12px 14px',
      flexShrink: 0,
      background: 'var(--accent-blue-dim)',
      borderColor: 'rgba(59,130,246,0.3)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--accent-blue)',
      marginBottom: 6
    }
  }, "Next Maneuver"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 10,
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      display: 'flex',
      alignItems: 'center',
      flexShrink: 0
    }
  }, navStepIcon(steps[currentStep].maneuver.type, isHiking, 28)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      fontWeight: 600,
      color: 'var(--text-primary)'
    }
  }, navStepLabel(steps[currentStep].maneuver, mode)), steps[currentStep].name && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-secondary)',
      marginTop: 2
    }
  }, "onto ", steps[currentStep].name), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      fontFamily: 'var(--font-mono)',
      marginTop: 4
    }
  }, fmtNavDist(steps[currentStep].distance), " \xB7 ", fmtNavDuration(steps[currentStep].duration))))), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 0,
      flex: 1,
      overflowY: 'auto'
    }
  }, steps.map((step, i) => {
    const active = i === currentStep;
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      onClick: () => setCurrentStep(i),
      style: {
        padding: '9px 12px',
        cursor: 'pointer',
        background: active ? 'var(--accent-blue-dim)' : 'transparent',
        borderBottom: '1px solid var(--border)',
        borderLeft: active ? '3px solid var(--accent-blue)' : '3px solid transparent',
        transition: 'background var(--transition)'
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start'
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: 20,
        flexShrink: 0,
        color: active ? 'var(--accent-blue)' : 'var(--text-muted)'
      }
    }, navStepIcon(step.maneuver.type, isHiking, 16)), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 12,
        fontWeight: active ? 600 : 400,
        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        lineHeight: 1.3
      }
    }, navStepLabel(step.maneuver, mode), step.name ? ` — ${step.name}` : ''), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        fontFamily: 'var(--font-mono)',
        marginTop: 2
      }
    }, fmtNavDist(step.distance)))));
  }))))), (savingFav || renamingFav) && /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'fixed',
      inset: 0,
      zIndex: 3000,
      background: 'rgba(0,0,0,0.6)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    },
    onClick: e => {
      if (e.target === e.currentTarget) {
        setSavingFav(null);
        setRenamingFav(null);
      }
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-card)',
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius-lg)',
      padding: 24,
      width: 340,
      boxShadow: 'var(--shadow)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 15,
      fontWeight: 600,
      marginBottom: 6
    }
  }, renamingFav ? 'Rename Favorite' : 'Save Favorite'), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 14
    }
  }, renamingFav ? `Current name: ${renamingFav.name}` : `${savingFav.lat.toFixed(5)}, ${savingFav.lon.toFixed(5)}`), /*#__PURE__*/React.createElement("input", {
    autoFocus: true,
    value: favNameInput,
    onChange: e => setFavNameInput(e.target.value),
    onKeyDown: e => {
      if (e.key === 'Enter') confirmSaveFav();
      if (e.key === 'Escape') {
        setSavingFav(null);
        setRenamingFav(null);
      }
    },
    placeholder: "Name this location\u2026",
    style: {
      width: '100%',
      padding: '9px 12px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 14,
      outline: 'none',
      marginBottom: 12
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      justifyContent: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => {
      setSavingFav(null);
      setRenamingFav(null);
    }
  }, "Cancel"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary btn-sm",
    onClick: confirmSaveFav,
    disabled: !favNameInput.trim()
  }, renamingFav ? 'Rename' : 'Save')))));
}

// ═══════════════════════════════════════════════
// Scheduler Page
// ═══════════════════════════════════════════════
function SchedulerPage({
  nodes,
  gpsStatus
}) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(false);
  const [editId, setEditId] = useState(null);
  const [delId, setDelId] = useState(null);
  const [saving, setSaving] = useState(false);

  // Form state
  const blank = {
    name: '',
    job_type: 'message',
    node_id: '',
    channel: 0,
    message_text: '',
    gps_lat: '',
    gps_lon: '',
    gps_alt: '0',
    schedule_type: 'interval',
    once_dt: '',
    interval_value: '30',
    interval_unit: 'minutes',
    daily_time: '08:00',
    weekly_time: '08:00',
    days_of_week: [false, false, false, false, false, false, false] // Mon–Sun
  };
  const [form, setForm] = useState(blank);
  const load = () => api.get('/api/jobs').then(j => {
    setJobs(j);
    setLoading(false);
  }).catch(() => setLoading(false));
  useEffect(() => {
    load();
  }, []);
  function openNew() {
    const gpsFix = gpsStatus && gpsStatus.fix;
    setForm({
      ...blank,
      gps_lat: gpsFix ? String(gpsFix.latitude.toFixed(6)) : '',
      gps_lon: gpsFix ? String(gpsFix.longitude.toFixed(6)) : '',
      gps_alt: gpsFix ? String(Math.round(gpsFix.altitude || 0)) : '0'
    });
    setEditId(null);
    setModal(true);
  }
  function openEdit(job) {
    const dow = [0, 1, 2, 3, 4, 5, 6].map(i => !!(job.days_of_week & 1 << i));
    let interval_value = '30',
      interval_unit = 'minutes';
    if (job.interval_seconds) {
      if (job.interval_seconds % 86400 === 0) {
        interval_value = String(job.interval_seconds / 86400);
        interval_unit = 'days';
      } else if (job.interval_seconds % 3600 === 0) {
        interval_value = String(job.interval_seconds / 3600);
        interval_unit = 'hours';
      } else {
        interval_value = String(job.interval_seconds / 60);
        interval_unit = 'minutes';
      }
    }
    const todSecs = job.run_at || 0;
    const hh = String(Math.floor(todSecs / 3600)).padStart(2, '0');
    const mm = String(Math.floor(todSecs % 3600 / 60)).padStart(2, '0');
    const timeStr = `${hh}:${mm}`;
    let once_dt = '';
    if (job.schedule_type === 'once' && job.run_at) {
      const d = new Date(job.run_at * 1000);
      once_dt = d.toISOString().slice(0, 16);
    }
    setForm({
      name: job.name,
      job_type: job.job_type,
      node_id: job.node_id || '',
      channel: job.channel || 0,
      message_text: job.message_text || '',
      gps_lat: job.gps_lat != null ? String(job.gps_lat) : '',
      gps_lon: job.gps_lon != null ? String(job.gps_lon) : '',
      gps_alt: job.gps_alt != null ? String(job.gps_alt) : '0',
      schedule_type: job.schedule_type,
      once_dt,
      interval_value,
      interval_unit,
      daily_time: timeStr,
      weekly_time: timeStr,
      days_of_week: dow
    });
    setEditId(job.id);
    setModal(true);
  }
  function buildPayload() {
    const f = form;
    let run_at = null,
      interval_seconds = null,
      days_of_week = 0;
    if (f.schedule_type === 'once') {
      run_at = f.once_dt ? Math.floor(new Date(f.once_dt).getTime() / 1000) : null;
    } else if (f.schedule_type === 'interval') {
      const mult = {
        minutes: 60,
        hours: 3600,
        days: 86400
      }[f.interval_unit] || 60;
      interval_seconds = parseInt(f.interval_value || 30) * mult;
    } else if (f.schedule_type === 'daily') {
      const [hh, mm] = (f.daily_time || '08:00').split(':').map(Number);
      run_at = hh * 3600 + mm * 60;
    } else if (f.schedule_type === 'weekly') {
      const [hh, mm] = (f.weekly_time || '08:00').split(':').map(Number);
      run_at = hh * 3600 + mm * 60;
      f.days_of_week.forEach((checked, i) => {
        if (checked) days_of_week |= 1 << i;
      });
    }
    return {
      name: f.name,
      job_type: f.job_type,
      node_id: f.node_id || null,
      channel: parseInt(f.channel) || 0,
      message_text: f.job_type === 'message' ? f.message_text : null,
      gps_lat: f.job_type === 'gps' ? parseFloat(f.gps_lat) || null : null,
      gps_lon: f.job_type === 'gps' ? parseFloat(f.gps_lon) || null : null,
      gps_alt: f.job_type === 'gps' ? parseFloat(f.gps_alt) || 0 : null,
      schedule_type: f.schedule_type,
      run_at,
      interval_seconds,
      days_of_week,
      enabled: 1
    };
  }
  async function saveJob() {
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const payload = buildPayload();
      if (editId != null) await api.put(`/api/jobs/${editId}`, payload);else await api.post('/api/jobs', payload);
      setModal(false);
      load();
    } catch (e) {
      alert('Save failed');
    } finally {
      setSaving(false);
    }
  }
  async function deleteJob() {
    await api.del(`/api/jobs/${delId}`);
    setDelId(null);
    load();
  }
  async function toggleJob(job) {
    await api.post(`/api/jobs/${job.id}/toggle`);
    load();
  }
  function fmtSchedule(job) {
    const tod = secs => {
      const h = Math.floor(secs / 3600),
        m = Math.floor(secs % 3600 / 60);
      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    };
    const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    if (job.schedule_type === 'once') {
      return job.run_at ? `Once — ${new Date(job.run_at * 1000).toLocaleString()}` : 'Once';
    } else if (job.schedule_type === 'interval') {
      const s = job.interval_seconds || 0;
      if (s >= 86400 && s % 86400 === 0) return `Every ${s / 86400}d`;
      if (s >= 3600 && s % 3600 === 0) return `Every ${s / 3600}h`;
      return `Every ${Math.round(s / 60)}m`;
    } else if (job.schedule_type === 'daily') {
      return `Daily @ ${tod(job.run_at || 0)}`;
    } else if (job.schedule_type === 'weekly') {
      const days = dayNames.filter((_, i) => job.days_of_week & 1 << i).join(', ');
      return `${days || 'No days'} @ ${tod(job.run_at || 0)}`;
    }
    return job.schedule_type;
  }
  function fmtTarget(job) {
    if (!job.node_id) return 'Broadcast';
    const n = nodes.find(x => x.node_id === job.node_id);
    return n ? n.alias || n.long_name || job.node_id : job.node_id;
  }
  const F = ({
    label,
    children
  }) => /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, label), children);
  const inp = {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    }
  };
  const setF = (k, v) => setForm(f => ({
    ...f,
    [k]: v
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "page",
    style: {
      padding: 20,
      overflowY: 'auto',
      height: '100%'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      marginBottom: 20,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--text-primary)'
    }
  }, "Scheduled Jobs"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginTop: 2
    }
  }, "Automate messages and GPS coordinates to mesh nodes")), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: openNew
  }, "+ New Job")), loading ? /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13
    }
  }, "Loading\u2026") : jobs.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: 32,
      textAlign: 'center',
      color: 'var(--text-muted)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 32,
      marginBottom: 8
    }
  }, "\uD83D\uDDD3"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      fontWeight: 600,
      marginBottom: 4
    }
  }, "No scheduled jobs yet"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12
    }
  }, "Click ", /*#__PURE__*/React.createElement("strong", null, "+ New Job"), " to automate a message or GPS broadcast.")) : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, jobs.map(job => {
    var _job$gps_lat, _job$gps_lon;
    return /*#__PURE__*/React.createElement("div", {
      key: job.id,
      className: "card",
      style: {
        padding: '12px 16px',
        display: 'flex',
        gap: 14,
        alignItems: 'center',
        flexWrap: 'wrap',
        opacity: job.enabled ? 1 : 0.55
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.08em',
        padding: '3px 8px',
        borderRadius: 4,
        flexShrink: 0,
        background: job.job_type === 'message' ? 'var(--accent-blue-dim)' : 'var(--accent-green-dim)',
        color: job.job_type === 'message' ? 'var(--accent-blue)' : 'var(--accent-green)'
      }
    }, job.job_type.toUpperCase()), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 160
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 14,
        fontWeight: 600,
        color: 'var(--text-primary)'
      }
    }, job.name), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        marginTop: 2,
        display: 'flex',
        gap: 10,
        flexWrap: 'wrap'
      }
    }, /*#__PURE__*/React.createElement("span", null, "\u2192 ", fmtTarget(job)), job.job_type === 'message' && job.channel > 0 && /*#__PURE__*/React.createElement("span", null, "ch ", job.channel), job.job_type === 'message' && /*#__PURE__*/React.createElement("span", {
      style: {
        fontStyle: 'italic',
        maxWidth: 240,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap'
      }
    }, "\"", job.message_text, "\""), job.job_type === 'gps' && /*#__PURE__*/React.createElement("span", null, (_job$gps_lat = job.gps_lat) === null || _job$gps_lat === void 0 ? void 0 : _job$gps_lat.toFixed(5), ", ", (_job$gps_lon = job.gps_lon) === null || _job$gps_lon === void 0 ? void 0 : _job$gps_lon.toFixed(5)))), /*#__PURE__*/React.createElement("div", {
      style: {
        minWidth: 140,
        fontSize: 12,
        color: 'var(--text-secondary)',
        fontFamily: 'var(--font-mono)'
      }
    }, fmtSchedule(job)), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 11,
        color: 'var(--text-muted)',
        minWidth: 100,
        fontFamily: 'var(--font-mono)'
      }
    }, /*#__PURE__*/React.createElement("div", null, "Runs: ", job.run_count), job.last_run && /*#__PURE__*/React.createElement("div", null, "Last: ", timeAgo(job.last_run)), job.enabled && job.next_run && /*#__PURE__*/React.createElement("div", {
      style: {
        color: 'var(--accent-amber)'
      }
    }, "Next: ", timeAgo(job.next_run - Math.floor(Date.now() / 1000) < 0 ? job.next_run : job.next_run))), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 6,
        flexShrink: 0
      }
    }, /*#__PURE__*/React.createElement("button", {
      className: "btn btn-sm",
      onClick: () => toggleJob(job),
      style: {
        background: job.enabled ? 'var(--accent-green-dim)' : '',
        color: job.enabled ? 'var(--accent-green)' : '',
        borderColor: job.enabled ? 'var(--accent-green)' : '',
        fontSize: 11
      }
    }, job.enabled ? 'On' : 'Off'), /*#__PURE__*/React.createElement("button", {
      className: "btn btn-sm",
      onClick: () => openEdit(job),
      style: {
        fontSize: 11
      }
    }, "Edit"), /*#__PURE__*/React.createElement("button", {
      className: "btn btn-sm",
      onClick: () => setDelId(job.id),
      style: {
        color: 'var(--accent-red)',
        borderColor: 'var(--accent-red)',
        fontSize: 11
      }
    }, "Del")));
  })), modal && /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'fixed',
      inset: 0,
      zIndex: 3000,
      background: 'rgba(0,0,0,0.65)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 16
    },
    onClick: e => {
      if (e.target === e.currentTarget) setModal(false);
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-card)',
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius-lg)',
      padding: 24,
      width: '100%',
      maxWidth: 480,
      maxHeight: '90vh',
      overflowY: 'auto',
      boxShadow: 'var(--shadow)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 16,
      fontWeight: 700,
      marginBottom: 16
    }
  }, editId != null ? 'Edit Job' : 'New Scheduled Job'), /*#__PURE__*/React.createElement(F, {
    label: "Job Name"
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    value: form.name,
    onChange: e => setF('name', e.target.value),
    placeholder: "e.g. Morning check-in"
  }))), /*#__PURE__*/React.createElement(F, {
    label: "Job Type"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, ['message', 'gps'].map(t => /*#__PURE__*/React.createElement("button", {
    key: t,
    onClick: () => setF('job_type', t),
    className: `btn btn-sm${form.job_type === t ? ' btn-primary' : ''}`,
    style: {
      flex: 1,
      textTransform: 'capitalize'
    }
  }, t === 'gps' ? 'GPS Coordinates' : 'Message')))), /*#__PURE__*/React.createElement(F, {
    label: "Target Node"
  }, /*#__PURE__*/React.createElement("select", _extends({}, inp, {
    value: form.node_id,
    onChange: e => setF('node_id', e.target.value)
  }), /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Broadcast (all nodes)"), nodes.map(n => /*#__PURE__*/React.createElement("option", {
    key: n.node_id,
    value: n.node_id
  }, n.alias || n.long_name || n.node_id)))), form.job_type === 'message' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(F, {
    label: "Channel"
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    type: "number",
    min: "0",
    max: "7",
    value: form.channel,
    onChange: e => setF('channel', e.target.value),
    placeholder: "0"
  }))), /*#__PURE__*/React.createElement(F, {
    label: "Message Text"
  }, /*#__PURE__*/React.createElement("textarea", _extends({}, inp, {
    rows: 3,
    value: form.message_text,
    onChange: e => setF('message_text', e.target.value),
    placeholder: "Message to send\u2026",
    style: {
      ...inp.style,
      resize: 'vertical'
    }
  })))), form.job_type === 'gps' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginBottom: 12
    }
  }, /*#__PURE__*/React.createElement(F, {
    label: "Latitude",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    value: form.gps_lat,
    onChange: e => setF('gps_lat', e.target.value),
    placeholder: "38.8977"
  }))), /*#__PURE__*/React.createElement(F, {
    label: "Longitude",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    value: form.gps_lon,
    onChange: e => setF('gps_lon', e.target.value),
    placeholder: "-77.0365"
  }))), /*#__PURE__*/React.createElement(F, {
    label: "Alt (m)",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    value: form.gps_alt,
    onChange: e => setF('gps_alt', e.target.value),
    placeholder: "0"
  })))), gpsStatus && gpsStatus.fix && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      marginBottom: 12,
      fontSize: 11
    },
    onClick: () => setForm(f => ({
      ...f,
      gps_lat: gpsStatus.fix.latitude.toFixed(6),
      gps_lon: gpsStatus.fix.longitude.toFixed(6),
      gps_alt: String(Math.round(gpsStatus.fix.altitude || 0))
    }))
  }, "\uD83D\uDCCD Use Current GPS Position")), /*#__PURE__*/React.createElement(F, {
    label: "Schedule Type"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      flexWrap: 'wrap'
    }
  }, [['once', 'One Time'], ['interval', 'Repeating'], ['daily', 'Daily'], ['weekly', 'Weekly']].map(([v, l]) => /*#__PURE__*/React.createElement("button", {
    key: v,
    onClick: () => setF('schedule_type', v),
    className: `btn btn-sm${form.schedule_type === v ? ' btn-primary' : ''}`
  }, l)))), form.schedule_type === 'once' && /*#__PURE__*/React.createElement(F, {
    label: "Date & Time"
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    type: "datetime-local",
    value: form.once_dt,
    onChange: e => setF('once_dt', e.target.value)
  }))), form.schedule_type === 'interval' && /*#__PURE__*/React.createElement(F, {
    label: "Repeat Every"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    type: "number",
    min: "1",
    value: form.interval_value,
    onChange: e => setF('interval_value', e.target.value),
    style: {
      ...inp.style,
      width: 80
    }
  })), /*#__PURE__*/React.createElement("select", _extends({}, inp, {
    value: form.interval_unit,
    onChange: e => setF('interval_unit', e.target.value),
    style: {
      ...inp.style,
      flex: 1
    }
  }), /*#__PURE__*/React.createElement("option", {
    value: "minutes"
  }, "Minutes"), /*#__PURE__*/React.createElement("option", {
    value: "hours"
  }, "Hours"), /*#__PURE__*/React.createElement("option", {
    value: "days"
  }, "Days")))), form.schedule_type === 'daily' && /*#__PURE__*/React.createElement(F, {
    label: "Time of Day"
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    type: "time",
    value: form.daily_time,
    onChange: e => setF('daily_time', e.target.value)
  }))), form.schedule_type === 'weekly' && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(F, {
    label: "Days of Week"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6,
      flexWrap: 'wrap'
    }
  }, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => /*#__PURE__*/React.createElement("button", {
    key: i,
    onClick: () => setForm(f => {
      const dow = [...f.days_of_week];
      dow[i] = !dow[i];
      return {
        ...f,
        days_of_week: dow
      };
    }),
    className: "btn btn-sm",
    style: {
      background: form.days_of_week[i] ? 'var(--accent-blue)' : '',
      color: form.days_of_week[i] ? '#fff' : '',
      borderColor: form.days_of_week[i] ? 'var(--accent-blue)' : ''
    }
  }, d)))), /*#__PURE__*/React.createElement(F, {
    label: "Time of Day"
  }, /*#__PURE__*/React.createElement("input", _extends({}, inp, {
    type: "time",
    value: form.weekly_time,
    onChange: e => setF('weekly_time', e.target.value)
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginTop: 20,
      justifyContent: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setModal(false)
  }, "Cancel"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: saveJob,
    disabled: saving || !form.name.trim()
  }, saving ? 'Saving…' : editId != null ? 'Save Changes' : 'Create Job')))), delId != null && /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'fixed',
      inset: 0,
      zIndex: 3000,
      background: 'rgba(0,0,0,0.65)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center'
    },
    onClick: e => {
      if (e.target === e.currentTarget) setDelId(null);
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-card)',
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius-lg)',
      padding: 24,
      width: 320,
      boxShadow: 'var(--shadow)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: 700,
      fontSize: 15,
      marginBottom: 8
    }
  }, "Delete Job?"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      color: 'var(--text-muted)',
      marginBottom: 20
    }
  }, "This job and all its settings will be permanently deleted."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      justifyContent: 'flex-end'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setDelId(null)
  }, "Cancel"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: deleteJob,
    style: {
      background: 'var(--accent-red)',
      color: '#fff',
      border: 'none'
    }
  }, "Delete")))));
}

// ═══════════════════════════════════════════════
// Top Bar Clock
// ═══════════════════════════════════════════════
function TopBarClock() {
  const {
    timezone
  } = useSettings();
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const tz = resolveTz(timezone);
  const timeStr = now.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: tz
  });
  const dateStr = now.toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: tz
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "top-bar-clock hide-mobile"
  }, /*#__PURE__*/React.createElement("span", {
    className: "clock-time"
  }, timeStr), /*#__PURE__*/React.createElement("span", {
    className: "clock-date"
  }, dateStr));
}

// ═══════════════════════════════════════════════
// WiFi & Hotspot Page
// ═══════════════════════════════════════════════
const EyeOffIcon = () => /*#__PURE__*/React.createElement("svg", {
  width: "15",
  height: "15",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "2",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, /*#__PURE__*/React.createElement("path", {
  d: "M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"
}), /*#__PURE__*/React.createElement("line", {
  x1: "1",
  y1: "1",
  x2: "23",
  y2: "23"
}));
const EyeIcon = () => /*#__PURE__*/React.createElement("svg", {
  width: "15",
  height: "15",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "2",
  strokeLinecap: "round",
  strokeLinejoin: "round"
}, /*#__PURE__*/React.createElement("path", {
  d: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"
}), /*#__PURE__*/React.createElement("circle", {
  cx: "12",
  cy: "12",
  r: "3"
}));
const ChevronIcon = ({
  open
}) => /*#__PURE__*/React.createElement("svg", {
  width: "14",
  height: "14",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "2.5",
  style: {
    transition: 'transform 0.2s',
    transform: open ? 'rotate(90deg)' : 'rotate(0deg)'
  }
}, /*#__PURE__*/React.createElement("polyline", {
  points: "9 18 15 12 9 6"
}));
const HotspotIcon = ({
  color
}) => /*#__PURE__*/React.createElement("svg", {
  width: "16",
  height: "16",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: color || 'currentColor',
  strokeWidth: "2",
  style: {
    display: 'inline',
    verticalAlign: 'middle'
  }
}, /*#__PURE__*/React.createElement("circle", {
  cx: "12",
  cy: "12",
  r: "2"
}), /*#__PURE__*/React.createElement("path", {
  d: "M8.56 8.56a5 5 0 0 0 0 6.88"
}), /*#__PURE__*/React.createElement("path", {
  d: "M15.44 8.56a5 5 0 0 1 0 6.88"
}));
function WifiSignal({
  signal
}) {
  const bars = signal >= 80 ? 4 : signal >= 60 ? 3 : signal >= 40 ? 2 : signal >= 20 ? 1 : 0;
  const heights = [4, 7, 11, 16];
  return /*#__PURE__*/React.createElement("div", {
    className: "wifi-signal-bars"
  }, heights.map((h, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    style: {
      height: h
    },
    className: i < bars ? 'lit' : ''
  })));
}
function WifiPage() {
  var _status$current, _status$current2, _status$connectivity, _status$connectivity2, _status$current3;
  const [status, setStatus] = useState(null);
  const [networks, setNetworks] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [selected, setSelected] = useState(null);
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [manualSSID, setManualSSID] = useState('');
  const [manualPass, setManualPass] = useState('');
  const [showManualPass, setShowManualPass] = useState(false);
  const [msg, setMsg] = useState(null);
  const [hotspot, setHotspot] = useState(null);
  const [hsSSID, setHsSSID] = useState('');
  const [hsPass, setHsPass] = useState('');
  const [hsWorking, setHsWorking] = useState(false);
  const [showHsPass, setShowHsPass] = useState(false);
  const [lastSwitch, setLastSwitch] = useState(null);
  const [lastScanAt, setLastScanAt] = useState(null);
  const [activeTab, setActiveTab] = useState('wifi');
  const errText = (err, fallback) => (err === null || err === void 0 ? void 0 : err.message) || (err === null || err === void 0 ? void 0 : err.error) || fallback;
  const flash = (text, ok = true) => {
    setMsg({
      text,
      ok
    });
    setTimeout(() => setMsg(null), 4500);
  };
  const loadStatus = () => api.get('/api/wifi/status').then(d => setStatus(d)).catch(() => {});
  const loadHotspot = (initFields = false) => api.get('/api/hotspot/status').then(d => {
    setHotspot(d);
    if (initFields) {
      if (d.ssid) setHsSSID(d.ssid);
      if (d.password) setHsPass(d.password);
    }
  }).catch(() => {});
  useEffect(() => {
    loadStatus();
    loadHotspot(true);
    scan();
  }, []);
  useEffect(() => {
    var _result$lan, _result$lan2, _result$lan3;
    const sw = status === null || status === void 0 ? void 0 : status.wifiSwitch;
    if (!sw || !sw.ssid) return;
    const result = sw.result || {};
    const urls = result.accessUrls || (status === null || status === void 0 ? void 0 : status.accessUrls) || [];
    setLastSwitch({
      ssid: (result === null || result === void 0 || (_result$lan = result.lan) === null || _result$lan === void 0 ? void 0 : _result$lan.ssid) || sw.ssid,
      kind: (result === null || result === void 0 || (_result$lan2 = result.lan) === null || _result$lan2 === void 0 ? void 0 : _result$lan2.kind) || 'wifi',
      ip: (result === null || result === void 0 || (_result$lan3 = result.lan) === null || _result$lan3 === void 0 ? void 0 : _result$lan3.ip) || (result === null || result === void 0 ? void 0 : result.ip) || '',
      url: urls[0] || 'https://atlas.local',
      pending: !!sw.pending,
      limited: !!(result !== null && result !== void 0 && result.limited),
      ok: sw.ok,
      message: sw.message || ''
    });
  }, [status]);
  useEffect(() => {
    if (!(lastSwitch !== null && lastSwitch !== void 0 && lastSwitch.pending)) return;
    const timer = setInterval(() => {
      loadStatus();
      loadHotspot();
    }, 3000);
    return () => clearInterval(timer);
  }, [lastSwitch === null || lastSwitch === void 0 ? void 0 : lastSwitch.pending]);

  // Auto-switch tab to hotspot when hotspot becomes active
  useEffect(() => {
    if (hotspot !== null && hotspot !== void 0 && hotspot.active) setActiveTab('hotspot');
  }, [hotspot === null || hotspot === void 0 ? void 0 : hotspot.active]);
  const scan = () => {
    setScanning(true);
    api.get('/api/wifi/networks').then(d => {
      const found = d.networks || [];
      setNetworks(found);
      setLastScanAt(new Date());
      flash(`${found.length} network${found.length !== 1 ? 's' : ''} found`);
    }).catch(e => flash(errText(e, 'Scan failed'), false)).finally(() => setScanning(false));
  };
  const doConnect = (ssid, pass) => {
    const targetSSID = (ssid || '').trim();
    if (!targetSSID) {
      flash('SSID is required', false);
      return;
    }
    setConnecting(true);
    if (window.AtlasAndroid && window.AtlasAndroid.lanSwitching) window.AtlasAndroid.lanSwitching();
    api.post('/api/wifi/connect', {
      ssid: targetSSID,
      password: pass || '',
      stopHotspot: true,
      background: !!(hotspot !== null && hotspot !== void 0 && hotspot.active)
    }).then(res => {
      var _res$lan, _res$lan2, _res$lan3, _res$lan4, _window$AtlasAndroid;
      const urls = (res === null || res === void 0 ? void 0 : res.accessUrls) || [];
      const primaryUrl = urls[0] || 'https://atlas.local';
      setLastSwitch({
        ssid: (res === null || res === void 0 || (_res$lan = res.lan) === null || _res$lan === void 0 ? void 0 : _res$lan.ssid) || targetSSID,
        kind: (res === null || res === void 0 || (_res$lan2 = res.lan) === null || _res$lan2 === void 0 ? void 0 : _res$lan2.kind) || 'wifi',
        ip: (res === null || res === void 0 || (_res$lan3 = res.lan) === null || _res$lan3 === void 0 ? void 0 : _res$lan3.ip) || (res === null || res === void 0 ? void 0 : res.ip) || '',
        url: primaryUrl,
        pending: !!(res !== null && res !== void 0 && res.pending),
        limited: !!(res !== null && res !== void 0 && res.limited),
        ok: res !== null && res !== void 0 && res.pending ? null : true,
        message: (res === null || res === void 0 ? void 0 : res.message) || ''
      });
      flash(res !== null && res !== void 0 && res.pending ? `Switching to ${targetSSID}… stay on the hotspot until it drops.` : (res === null || res === void 0 ? void 0 : res.message) || `Atlas is now on ${(res === null || res === void 0 || (_res$lan4 = res.lan) === null || _res$lan4 === void 0 ? void 0 : _res$lan4.ssid) || targetSSID}.`, !(res !== null && res !== void 0 && res.limited));
      if ((_window$AtlasAndroid = window.AtlasAndroid) !== null && _window$AtlasAndroid !== void 0 && _window$AtlasAndroid.lanSwitchedTo) {
        var _res$lan5;
        const allUrls = [...((res === null || res === void 0 ? void 0 : res.accessUrls) || [])];
        const hintIp = (res === null || res === void 0 ? void 0 : res.hintIp) || (res === null || res === void 0 || (_res$lan5 = res.lan) === null || _res$lan5 === void 0 ? void 0 : _res$lan5.ip) || (res === null || res === void 0 ? void 0 : res.ip) || '';
        if (hintIp) {
          if (!allUrls.some(u => u.includes(hintIp))) allUrls.unshift(`https://${hintIp}`);
          if (!allUrls.some(u => u.includes(':5000') && u.includes(hintIp))) allUrls.push(`http://${hintIp}:5000`);
        }
        window.AtlasAndroid.lanSwitchedTo(JSON.stringify(allUrls), !!(res !== null && res !== void 0 && res.pending), hintIp || '');
      }
      setSelected(null);
      setPassword('');
      setManualSSID('');
      setManualPass('');
      loadStatus();
      loadHotspot();
      // Don't force a WiFi rescan while a background connect is in progress —
      // the active rescan races nmcli connect on the same interface and can
      // cause the first attempt to fail.
      if (!(res !== null && res !== void 0 && res.pending)) scan();
    }).catch(e => flash(errText(e, 'Connection failed'), false)).finally(() => setConnecting(false));
  };
  const disconnect = () => api.post('/api/wifi/disconnect', {}).then(() => {
    setLastSwitch(null);
    flash('Disconnected from network');
    loadStatus();
    scan();
  }).catch(e => flash(errText(e, 'Disconnect failed'), false));
  const startHotspot = () => {
    if (!hsSSID) {
      flash('SSID is required', false);
      return;
    }
    if (hsPass.length < 8) {
      flash('Password must be at least 8 characters', false);
      return;
    }
    setHsWorking(true);
    api.post('/api/hotspot/start', {
      ssid: hsSSID,
      password: hsPass
    }).then(() => {
      setLastSwitch(null);
      flash('Hotspot started');
      loadHotspot();
      loadStatus();
    }).catch(e => flash(errText(e, 'Failed to start hotspot'), false)).finally(() => setHsWorking(false));
  };
  const stopHotspot = () => {
    setHsWorking(true);
    api.post('/api/hotspot/stop', {}).then(() => {
      flash('Hotspot stopped');
      loadHotspot();
      loadStatus();
    }).catch(e => flash(errText(e, 'Failed to stop hotspot'), false)).finally(() => setHsWorking(false));
  };
  const saveHotspotConfig = () => {
    if (!hsSSID) {
      flash('SSID is required', false);
      return;
    }
    api.post('/api/hotspot/config', {
      ssid: hsSSID,
      password: hsPass
    }).then(() => flash('Hotspot config saved')).catch(e => flash(errText(e, 'Failed to save config'), false));
  };
  const currentSSID = status === null || status === void 0 || (_status$current = status.current) === null || _status$current === void 0 ? void 0 : _status$current.ssid;
  const currentKind = (status === null || status === void 0 || (_status$current2 = status.current) === null || _status$current2 === void 0 ? void 0 : _status$current2.kind) || 'wifi';
  const internetOnline = !!(status !== null && status !== void 0 && (_status$connectivity = status.connectivity) !== null && _status$connectivity !== void 0 && _status$connectivity.online);
  const connectivityChecked = !!(status !== null && status !== void 0 && (_status$connectivity2 = status.connectivity) !== null && _status$connectivity2 !== void 0 && _status$connectivity2.checked);
  const primaryCurrentUrl = ((status === null || status === void 0 ? void 0 : status.accessUrls) || [])[0] || 'https://atlas.local';
  const PassToggle = ({
    show,
    onToggle
  }) => /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: onToggle,
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexShrink: 0
    }
  }, show ? /*#__PURE__*/React.createElement(EyeOffIcon, null) : /*#__PURE__*/React.createElement(EyeIcon, null));
  return /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, msg && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderRadius: 'var(--radius)',
      background: msg.ok ? 'var(--accent-green-dim)' : 'var(--accent-red-dim)',
      color: msg.ok ? 'var(--accent-green)' : 'var(--accent-red)',
      border: `1px solid ${msg.ok ? 'var(--accent-green)' : 'var(--accent-red)'}`,
      fontSize: 13
    }
  }, msg.text), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '12px 16px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      flexWrap: 'wrap',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 34,
      height: 34,
      borderRadius: 9,
      flexShrink: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: hotspot !== null && hotspot !== void 0 && hotspot.active ? 'rgba(87,216,255,0.14)' : currentSSID ? 'rgba(94,242,194,0.14)' : 'rgba(255,255,255,0.05)'
    }
  }, hotspot !== null && hotspot !== void 0 && hotspot.active ? /*#__PURE__*/React.createElement(HotspotIcon, {
    color: "var(--accent-cyan)"
  }) : React.cloneElement(Icons.wifi, {
    style: {
      width: 16,
      height: 16
    }
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 600,
      color: 'var(--text-primary)',
      lineHeight: 1.3
    }
  }, hotspot !== null && hotspot !== void 0 && hotspot.active ? `Hotspot · ${hotspot.ssid || hsSSID || '—'}` : currentSSID ? `${currentKind === 'ethernet' ? 'Ethernet' : 'WiFi'} · ${currentSSID}` : 'Not Connected'), !(hotspot !== null && hotspot !== void 0 && hotspot.active) && currentSSID && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      fontFamily: 'var(--font-mono)',
      marginTop: 1
    }
  }, status !== null && status !== void 0 && (_status$current3 = status.current) !== null && _status$current3 !== void 0 && _status$current3.ip ? `${status.current.ip} · ` : '', primaryCurrentUrl))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      alignItems: 'center',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: `connection-badge ${!connectivityChecked ? 'offline' : internetOnline ? 'online' : 'offline'}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "dot"
  }), !connectivityChecked ? 'UNKNOWN' : internetOnline ? 'INTERNET' : 'LOCAL'), (hotspot === null || hotspot === void 0 ? void 0 : hotspot.active) && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: stopHotspot,
    disabled: hsWorking,
    style: {
      color: 'var(--accent-red)',
      borderColor: 'var(--accent-red)',
      fontSize: 12
    }
  }, hsWorking ? 'Stopping…' : 'Stop'), !(hotspot !== null && hotspot !== void 0 && hotspot.active) && currentSSID && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: disconnect,
    style: {
      color: 'var(--accent-red)',
      borderColor: 'var(--accent-red)',
      fontSize: 12
    }
  }, "Disconnect")))), /*#__PURE__*/React.createElement("div", {
    className: "wifi-mode-tabs"
  }, /*#__PURE__*/React.createElement("button", {
    className: `wifi-tab-btn${activeTab === 'wifi' ? ' active' : ''}`,
    onClick: () => setActiveTab('wifi')
  }, /*#__PURE__*/React.createElement("svg", {
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M5 12.55a11 11 0 0114.08 0"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M1.42 9a16 16 0 0121.16 0"
  }), /*#__PURE__*/React.createElement("path", {
    d: "M8.53 16.11a6 6 0 016.95 0"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "12",
    cy: "20",
    r: "1",
    fill: "currentColor"
  })), "WiFi Network", !(hotspot !== null && hotspot !== void 0 && hotspot.active) && currentSSID && /*#__PURE__*/React.createElement("span", {
    style: {
      width: 6,
      height: 6,
      borderRadius: '50%',
      background: 'var(--accent-green)',
      flexShrink: 0
    }
  })), /*#__PURE__*/React.createElement("button", {
    className: `wifi-tab-btn${activeTab === 'hotspot' ? ' active' : ''}`,
    onClick: () => setActiveTab('hotspot')
  }, /*#__PURE__*/React.createElement(HotspotIcon, {
    color: "currentColor"
  }), "Hotspot", (hotspot === null || hotspot === void 0 ? void 0 : hotspot.active) && /*#__PURE__*/React.createElement("span", {
    style: {
      width: 6,
      height: 6,
      borderRadius: '50%',
      background: 'var(--accent-cyan)',
      flexShrink: 0
    }
  }))), activeTab === 'wifi' && /*#__PURE__*/React.createElement(React.Fragment, null, lastSwitch && /*#__PURE__*/React.createElement("div", {
    className: "wifi-next-step"
  }, lastSwitch.pending ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("strong", null, "Switching to ", lastSwitch.ssid, "\u2026"), /*#__PURE__*/React.createElement("br", null), "Stay on the Atlas hotspot until it disconnects, then join", ' ', /*#__PURE__*/React.createElement("span", {
    className: "wifi-next-url"
  }, lastSwitch.ssid), " and open", ' ', /*#__PURE__*/React.createElement("span", {
    className: "wifi-next-url"
  }, lastSwitch.url), ".", /*#__PURE__*/React.createElement("br", null), "If the hotspot comes back, the switch failed \u2014 retry with different credentials.") : lastSwitch.ok === false ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("strong", null, "Atlas could not join ", lastSwitch.ssid, "."), /*#__PURE__*/React.createElement("br", null), lastSwitch.message || 'The connection failed.', /*#__PURE__*/React.createElement("br", null), "If the hotspot is back, Atlas is reachable there again and you can retry with corrected credentials.") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("strong", null, "Atlas joined ", lastSwitch.kind === 'ethernet' ? 'Ethernet' : lastSwitch.ssid, "."), /*#__PURE__*/React.createElement("br", null), "Connect your device to the same network and open", ' ', /*#__PURE__*/React.createElement("span", {
    className: "wifi-next-url"
  }, lastSwitch.url), ".", lastSwitch.ip ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("br", null), "Direct IP: ", /*#__PURE__*/React.createElement("span", {
    className: "wifi-next-url"
  }, lastSwitch.ip)) : null)), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("h2", {
    className: "card-title"
  }, "Nearby Networks"), networks.length > 0 && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      fontFamily: 'var(--font-mono)',
      color: 'var(--accent-blue)',
      padding: '3px 8px',
      border: '1px solid rgba(90,167,255,0.22)',
      borderRadius: '999px',
      background: 'rgba(90,167,255,0.08)'
    }
  }, networks.length), lastScanAt && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      fontFamily: 'var(--font-mono)'
    }
  }, lastScanAt.toLocaleTimeString())), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm btn-primary",
    onClick: scan,
    disabled: scanning
  }, scanning ? 'Scanning…' : 'Scan')), /*#__PURE__*/React.createElement("div", {
    className: "wifi-network-list"
  }, networks.length === 0 && !scanning && /*#__PURE__*/React.createElement("p", {
    style: {
      color: 'var(--text-muted)',
      fontSize: 13,
      textAlign: 'center',
      padding: '18px 0'
    }
  }, "No networks found \u2014 tap Scan to search"), networks.map((net, idx) => {
    const isSelected = selected === net;
    const needsPass = net.security && net.security !== 'Open';
    return /*#__PURE__*/React.createElement("div", {
      key: `${net.ssid || 'hidden'}|${net.signal}|${net.security || 'open'}|${idx}`
    }, /*#__PURE__*/React.createElement("div", {
      className: `wifi-network-row${net.in_use ? ' active' : ''}${isSelected ? ' selected-row' : ''}`,
      onClick: () => {
        if (net.in_use) return;
        const next = isSelected ? null : net;
        setSelected(next);
        setPassword('');
        setShowPass(false);
      }
    }, /*#__PURE__*/React.createElement(WifiSignal, {
      signal: net.signal
    }), /*#__PURE__*/React.createElement("div", {
      style: {
        flex: 1,
        minWidth: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "wifi-ssid"
    }, net.ssid || '(hidden)'), /*#__PURE__*/React.createElement("div", {
      className: "wifi-security"
    }, net.security || 'Open', " \xB7 ", net.signal, "%")), net.in_use ? /*#__PURE__*/React.createElement("span", {
      style: {
        color: 'var(--accent-green)',
        display: 'flex',
        alignItems: 'center'
      }
    }, Icons.check) : /*#__PURE__*/React.createElement(ChevronIcon, {
      open: isSelected
    })), isSelected && /*#__PURE__*/React.createElement("div", {
      className: "wifi-inline-form"
    }, needsPass && /*#__PURE__*/React.createElement("div", {
      style: {
        marginBottom: 10
      }
    }, /*#__PURE__*/React.createElement("label", {
      style: {
        display: 'block',
        fontSize: 11,
        color: 'var(--text-muted)',
        marginBottom: 5
      }
    }, "Password for ", /*#__PURE__*/React.createElement("strong", {
      style: {
        color: 'var(--text-secondary)'
      }
    }, net.ssid)), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 6
      }
    }, /*#__PURE__*/React.createElement("input", {
      className: "input",
      type: showPass ? 'text' : 'password',
      placeholder: "Network password",
      value: password,
      onChange: e => setPassword(e.target.value),
      onKeyDown: e => e.key === 'Enter' && doConnect(net.ssid, password),
      style: {
        flex: 1
      },
      autoFocus: true
    }), /*#__PURE__*/React.createElement(PassToggle, {
      show: showPass,
      onToggle: () => setShowPass(p => !p)
    }))), /*#__PURE__*/React.createElement("div", {
      style: {
        display: 'flex',
        gap: 8
      }
    }, /*#__PURE__*/React.createElement("button", {
      className: "btn btn-primary",
      onClick: () => doConnect(net.ssid, password),
      disabled: connecting,
      style: {
        flex: 1
      }
    }, connecting ? 'Connecting…' : `Connect to ${net.ssid}`), /*#__PURE__*/React.createElement("button", {
      className: "btn",
      onClick: () => {
        setSelected(null);
        setPassword('');
      }
    }, "Cancel"))));
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 16,
      paddingTop: 14,
      borderTop: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 8,
      fontWeight: 500
    }
  }, "Not in the list? Enter manually"), /*#__PURE__*/React.createElement("div", {
    className: "hotspot-grid"
  }, /*#__PURE__*/React.createElement("input", {
    className: "input",
    type: "text",
    placeholder: "Network name (SSID)",
    value: manualSSID,
    onChange: e => setManualSSID(e.target.value),
    style: {
      width: '100%'
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("input", {
    className: "input",
    type: showManualPass ? 'text' : 'password',
    placeholder: "Password (blank if open)",
    value: manualPass,
    onChange: e => setManualPass(e.target.value),
    onKeyDown: e => e.key === 'Enter' && manualSSID.trim() && doConnect(manualSSID, manualPass),
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(PassToggle, {
    show: showManualPass,
    onToggle: () => setShowManualPass(p => !p)
  }))), manualSSID.trim() && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: () => doConnect(manualSSID, manualPass),
    disabled: connecting,
    style: {
      width: '100%',
      marginTop: 10
    }
  }, connecting ? 'Connecting…' : `Connect to ${manualSSID.trim()}`)))), activeTab === 'hotspot' && /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header"
  }, /*#__PURE__*/React.createElement("h2", {
    className: "card-title"
  }, "Hotspot Configuration"), hotspot && /*#__PURE__*/React.createElement("span", {
    className: `connection-badge ${hotspot.active ? 'online' : 'offline'}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "dot"
  }), hotspot.active ? 'ACTIVE' : 'OFF')), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "hotspot-grid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'block',
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 6
    }
  }, "Network Name (SSID)"), /*#__PURE__*/React.createElement("input", {
    className: "input",
    type: "text",
    placeholder: "atlas_navigate",
    value: hsSSID,
    onChange: e => setHsSSID(e.target.value),
    style: {
      width: '100%'
    }
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'block',
      fontSize: 12,
      color: 'var(--text-muted)',
      marginBottom: 6
    }
  }, "Password ", /*#__PURE__*/React.createElement("span", {
    style: {
      opacity: 0.6
    }
  }, "(min 8 chars)")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("input", {
    className: "input",
    type: showHsPass ? 'text' : 'password',
    placeholder: "Min 8 characters",
    value: hsPass,
    onChange: e => setHsPass(e.target.value),
    style: {
      flex: 1
    }
  }), /*#__PURE__*/React.createElement(PassToggle, {
    show: showHsPass,
    onToggle: () => setShowHsPass(p => !p)
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: saveHotspotConfig,
    style: {
      flexShrink: 0
    }
  }, "Save Config"), !(hotspot !== null && hotspot !== void 0 && hotspot.active) ? /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    onClick: startHotspot,
    disabled: hsWorking,
    style: {
      flex: 1
    }
  }, hsWorking ? 'Starting…' : 'Start Hotspot') : /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: stopHotspot,
    disabled: hsWorking,
    style: {
      flex: 1,
      color: 'var(--accent-red)',
      borderColor: 'var(--accent-red)'
    }
  }, hsWorking ? 'Stopping…' : 'Stop Hotspot')), (hotspot === null || hotspot === void 0 ? void 0 : hotspot.active) && /*#__PURE__*/React.createElement("div", {
    style: {
      padding: '10px 14px',
      borderRadius: 'var(--radius)',
      background: 'rgba(87,216,255,0.08)',
      border: '1px solid rgba(87,216,255,0.2)',
      fontSize: 12,
      color: 'var(--text-muted)',
      lineHeight: 1.5
    }
  }, "Hotspot", ' ', /*#__PURE__*/React.createElement("strong", {
    style: {
      color: 'var(--accent-cyan)'
    }
  }, hotspot.ssid || hsSSID), ' ', "is active. Devices on this network reach Atlas at", ' ', /*#__PURE__*/React.createElement("span", {
    className: "wifi-next-url"
  }, "10.42.0.1"), ".")))));
}

// ═══════════════════════════════════════════════
// VPN Page
// ═══════════════════════════════════════════════
// CalendarPage
// ═══════════════════════════════════════════════
function CalendarPage() {
  const today = new Date();
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth()); // 0-indexed
  const [events, setEvents] = useState([]);
  const [modal, setModal] = useState(false);
  const [editEvent, setEditEvent] = useState(null);
  const [selectedDay, setSelectedDay] = useState(null);
  const blank = {
    title: '',
    description: '',
    date: '',
    time: '09:00',
    end_date: '',
    end_time: '10:00',
    all_day: false,
    color: 'blue'
  };
  const [form, setForm] = useState(blank);
  const [saving, setSaving] = useState(false);
  const [delId, setDelId] = useState(null);
  const COLOR_OPTS = ['blue', 'green', 'amber', 'red', 'purple', 'cyan'];
  function loadEvents() {
    const start = Math.floor(new Date(viewYear, viewMonth, 1).getTime() / 1000);
    const end = Math.floor(new Date(viewYear, viewMonth + 1, 0, 23, 59, 59).getTime() / 1000);
    api.get(`/api/calendar?start=${start}&end=${end}`).then(setEvents).catch(() => {});
  }
  useEffect(() => {
    loadEvents();
  }, [viewYear, viewMonth]);
  function prevMonth() {
    if (viewMonth === 0) {
      setViewYear(y => y - 1);
      setViewMonth(11);
    } else setViewMonth(m => m - 1);
  }
  function nextMonth() {
    if (viewMonth === 11) {
      setViewYear(y => y + 1);
      setViewMonth(0);
    } else setViewMonth(m => m + 1);
  }
  function openNew(dayNum) {
    const dateStr = `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(dayNum).padStart(2, '0')}`;
    setForm({
      ...blank,
      date: dateStr,
      end_date: dateStr
    });
    setEditEvent(null);
    setSelectedDay(dayNum);
    setModal(true);
  }
  function openEdit(ev, e) {
    e.stopPropagation();
    const sd = new Date(ev.start_ts * 1000);
    const ed = ev.end_ts ? new Date(ev.end_ts * 1000) : sd;
    const pad = n => String(n).padStart(2, '0');
    setForm({
      title: ev.title,
      description: ev.description || '',
      date: `${sd.getFullYear()}-${pad(sd.getMonth() + 1)}-${pad(sd.getDate())}`,
      time: `${pad(sd.getHours())}:${pad(sd.getMinutes())}`,
      end_date: `${ed.getFullYear()}-${pad(ed.getMonth() + 1)}-${pad(ed.getDate())}`,
      end_time: `${pad(ed.getHours())}:${pad(ed.getMinutes())}`,
      all_day: !!ev.all_day,
      color: ev.color || 'blue'
    });
    setEditEvent(ev);
    setModal(true);
  }
  function buildTimestamp(dateStr, timeStr, allDay) {
    if (!dateStr) return null;
    if (allDay) return Math.floor(new Date(dateStr + 'T00:00:00').getTime() / 1000);
    return Math.floor(new Date(`${dateStr}T${timeStr || '00:00'}:00`).getTime() / 1000);
  }
  async function saveEvent() {
    if (!form.title.trim() || !form.date) return;
    setSaving(true);
    const payload = {
      title: form.title.trim(),
      description: form.description.trim(),
      start_ts: buildTimestamp(form.date, form.time, form.all_day),
      end_ts: form.end_date ? buildTimestamp(form.end_date, form.end_time, form.all_day) : null,
      all_day: form.all_day,
      color: form.color
    };
    try {
      if (editEvent) {
        await api.put(`/api/calendar/${editEvent.id}`, payload);
      } else {
        await api.post('/api/calendar', payload);
      }
      setModal(false);
      loadEvents();
    } catch (err) {}
    setSaving(false);
  }
  async function deleteEvent() {
    if (!delId) return;
    await api.del(`/api/calendar/${delId}`);
    setDelId(null);
    setModal(false);
    loadEvents();
  }

  // Build calendar grid
  const firstDay = new Date(viewYear, viewMonth, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const accentMap = {
    blue: 'var(--accent-blue)',
    green: 'var(--accent-green)',
    amber: 'var(--accent-amber)',
    red: 'var(--accent-red)',
    purple: 'var(--accent-purple)',
    cyan: 'var(--accent-cyan)'
  };
  function eventsForDay(day) {
    const dayStart = Math.floor(new Date(viewYear, viewMonth, day).getTime() / 1000);
    const dayEnd = dayStart + 86399;
    return events.filter(ev => {
      const s = ev.start_ts,
        e = ev.end_ts || ev.start_ts;
      return s <= dayEnd && e >= dayStart;
    });
  }
  const isToday = d => d === today.getDate() && viewMonth === today.getMonth() && viewYear === today.getFullYear();
  return /*#__PURE__*/React.createElement("div", {
    className: "page",
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      flexWrap: 'wrap'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: prevMonth,
    style: {
      padding: '4px 10px'
    }
  }, "\u2039"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 18,
      fontWeight: 700,
      color: 'var(--text-primary)',
      minWidth: 160,
      textAlign: 'center'
    }
  }, MONTH_NAMES[viewMonth], " ", viewYear), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: nextMonth,
    style: {
      padding: '4px 10px'
    }
  }, "\u203A")), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => {
      setViewYear(today.getFullYear());
      setViewMonth(today.getMonth());
    },
    style: {
      marginLeft: 'auto'
    }
  }, "Today")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(7,1fr)',
      gap: 2,
      flex: 1
    }
  }, DAY_NAMES.map(d => /*#__PURE__*/React.createElement("div", {
    key: d,
    style: {
      textAlign: 'center',
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--text-muted)',
      padding: '4px 0',
      letterSpacing: '0.05em'
    }
  }, d)), Array.from({
    length: firstDay
  }).map((_, i) => /*#__PURE__*/React.createElement("div", {
    key: `e${i}`
  })), Array.from({
    length: daysInMonth
  }, (_, i) => i + 1).map(day => {
    const dayEvs = eventsForDay(day);
    return /*#__PURE__*/React.createElement("div", {
      key: day,
      onClick: () => openNew(day),
      style: {
        minHeight: 72,
        background: 'var(--bg-card)',
        border: `1px solid ${isToday(day) ? 'var(--accent-green)' : 'var(--border)'}`,
        borderRadius: 'var(--radius)',
        padding: '4px 6px',
        cursor: 'pointer',
        transition: 'background var(--transition)'
      },
      onMouseOver: e => e.currentTarget.style.background = 'var(--bg-card-hover)',
      onMouseOut: e => e.currentTarget.style.background = 'var(--bg-card)'
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 12,
        fontWeight: isToday(day) ? 700 : 400,
        color: isToday(day) ? 'var(--accent-green)' : 'var(--text-secondary)',
        marginBottom: 2
      }
    }, day), dayEvs.slice(0, 3).map(ev => /*#__PURE__*/React.createElement("div", {
      key: ev.id,
      onClick: e => openEdit(ev, e),
      style: {
        fontSize: 10,
        padding: '1px 5px',
        borderRadius: 3,
        marginBottom: 2,
        background: (accentMap[ev.color] || accentMap.blue) + '33',
        borderLeft: `2px solid ${accentMap[ev.color] || accentMap.blue}`,
        color: 'var(--text-primary)',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        cursor: 'pointer'
      },
      title: ev.title
    }, ev.title)), dayEvs.length > 3 && /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 9,
        color: 'var(--text-muted)'
      }
    }, "+", dayEvs.length - 3, " more"));
  })), modal && /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.65)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 3000,
      padding: 16
    },
    onClick: () => setModal(false)
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-card)',
      border: '1px solid var(--border-bright)',
      borderRadius: 'var(--radius-lg)',
      padding: 24,
      width: '100%',
      maxWidth: 480,
      maxHeight: '90vh',
      overflowY: 'auto',
      boxShadow: 'var(--shadow)'
    },
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 15,
      fontWeight: 700,
      color: 'var(--text-primary)'
    }
  }, editEvent ? 'Edit Event' : 'New Event')), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    },
    placeholder: "Title *",
    value: form.title,
    onChange: e => setForm(f => ({
      ...f,
      title: e.target.value
    })),
    autoFocus: true
  }), /*#__PURE__*/React.createElement("textarea", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box',
      resize: 'vertical'
    },
    placeholder: "Description",
    rows: 2,
    value: form.description,
    onChange: e => setForm(f => ({
      ...f,
      description: e.target.value
    }))
  }), /*#__PURE__*/React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: 13,
      color: 'var(--text-primary)'
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: form.all_day,
    onChange: e => setForm(f => ({
      ...f,
      all_day: e.target.checked
    }))
  }), "All day"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 1fr',
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "Start date"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    },
    type: "date",
    value: form.date,
    onChange: e => setForm(f => ({
      ...f,
      date: e.target.value
    }))
  })), !form.all_day && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "Start time"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    },
    type: "time",
    value: form.time,
    onChange: e => setForm(f => ({
      ...f,
      time: e.target.value
    }))
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "End date"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    },
    type: "date",
    value: form.end_date,
    onChange: e => setForm(f => ({
      ...f,
      end_date: e.target.value
    }))
  })), !form.all_day && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      color: 'var(--text-muted)',
      marginBottom: 4
    }
  }, "End time"), /*#__PURE__*/React.createElement("input", {
    style: {
      width: '100%',
      padding: '7px 10px',
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      color: 'var(--text-primary)',
      fontSize: 13,
      outline: 'none',
      boxSizing: 'border-box'
    },
    type: "time",
    value: form.end_time,
    onChange: e => setForm(f => ({
      ...f,
      end_time: e.target.value
    }))
  }))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6
    }
  }, "Color"), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8
    }
  }, COLOR_OPTS.map(c => /*#__PURE__*/React.createElement("div", {
    key: c,
    onClick: () => setForm(f => ({
      ...f,
      color: c
    })),
    style: {
      width: 22,
      height: 22,
      borderRadius: '50%',
      background: accentMap[c],
      cursor: 'pointer',
      outline: form.color === c ? `2px solid white` : '2px solid transparent',
      outlineOffset: 2
    }
  })))), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-primary",
    style: {
      flex: 1
    },
    onClick: saveEvent,
    disabled: saving || !form.title.trim() || !form.date
  }, saving ? 'Saving…' : 'Save'), editEvent && /*#__PURE__*/React.createElement("button", {
    className: "btn",
    style: {
      color: 'var(--accent-red)',
      borderColor: 'var(--accent-red)'
    },
    onClick: () => setDelId(editEvent.id)
  }, "Delete"), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => setModal(false)
  }, "Cancel")), delId && /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--accent-red-dim)',
      border: '1px solid var(--accent-red)',
      borderRadius: 'var(--radius)',
      padding: '10px 12px',
      fontSize: 13
    }
  }, "Delete \"", editEvent === null || editEvent === void 0 ? void 0 : editEvent.title, "\"?", /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      background: 'var(--accent-red)',
      color: '#fff',
      border: 'none'
    },
    onClick: deleteEvent
  }, "Delete"), /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    onClick: () => setDelId(null)
  }, "Cancel")))))));
}

// ═══════════════════════════════════════════════
// CalculatorPage
// ═══════════════════════════════════════════════
function CalculatorPage() {
  const [expr, setExpr] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [history, setHistory] = useState([]);
  const inputRef = useRef(null);
  const BUTTONS = [['7', '8', '9', '÷', '('], ['4', '5', '6', '×', ')'], ['1', '2', '3', '-', '**'], ['0', '.', '±', '+', '%'], ['C', '←', 'sin', 'cos', 'tan'], ['sqrt', 'log', 'log10', 'pi', 'e'], ['radians', 'degrees', '=', '', '']];
  function press(val) {
    var _inputRef$current;
    if (val === '=') {
      calculate();
      return;
    }
    if (val === 'C') {
      setExpr('');
      setResult(null);
      setError('');
      return;
    }
    if (val === '←') {
      setExpr(e => e.slice(0, -1));
      return;
    }
    if (val === '±') {
      setExpr(e => e.startsWith('-') ? e.slice(1) : '-' + e);
      return;
    }
    if (val === '') return;
    const insertMap = {
      '÷': '/',
      '×': '*',
      'sin': 'sin(',
      'cos': 'cos(',
      'tan': 'tan(',
      'sqrt': 'sqrt(',
      'log': 'log(',
      'log10': 'log10(',
      'radians': 'radians(',
      'degrees': 'degrees(',
      'pi': 'pi',
      'e': 'e'
    };
    setExpr(e => {
      var _insertMap$val;
      return e + ((_insertMap$val = insertMap[val]) !== null && _insertMap$val !== void 0 ? _insertMap$val : val);
    });
    (_inputRef$current = inputRef.current) === null || _inputRef$current === void 0 || _inputRef$current.focus();
  }
  async function calculate() {
    if (!expr.trim()) return;
    setError('');
    setResult(null);
    try {
      const data = await api.post('/api/calculator', {
        expression: expr
      });
      const res = data.result;
      setResult(res);
      setHistory(h => [{
        expr,
        result: res
      }, ...h].slice(0, 20));
    } catch (err) {
      setError(err.message || 'Calculation error');
    }
  }
  function handleKey(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      calculate();
    }
  }
  function loadFromHistory(item) {
    setExpr(item.expr);
    setResult(item.result);
    setError('');
  }
  const btnStyle = val => ({
    padding: '10px 6px',
    fontSize: 13,
    fontWeight: 500,
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
    background: 'var(--bg-card)',
    color: val === '=' ? 'var(--bg-primary)' : val === 'C' ? 'var(--accent-red)' : 'var(--text-primary)',
    background: val === '=' ? 'var(--accent-green)' : val === 'C' ? 'var(--accent-red-dim)' : 'var(--bg-card)',
    cursor: val === '' ? 'default' : 'pointer',
    transition: 'background var(--transition)',
    opacity: val === '' ? 0 : 1
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "page",
    style: {
      display: 'flex',
      gap: 20,
      flexWrap: 'wrap',
      alignItems: 'flex-start'
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      width: 340,
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header",
    style: {
      marginBottom: 14
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "card-title"
  }, "Scientific Calculator")), /*#__PURE__*/React.createElement("div", {
    style: {
      background: 'var(--bg-input)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '10px 14px',
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("input", {
    ref: inputRef,
    style: {
      width: '100%',
      background: 'transparent',
      border: 'none',
      fontSize: 15,
      fontFamily: 'var(--font-mono)',
      padding: 0,
      color: 'var(--text-primary)',
      outline: 'none'
    },
    value: expr,
    onChange: e => {
      setExpr(e.target.value);
      setResult(null);
      setError('');
    },
    onKeyDown: handleKey,
    placeholder: "Enter expression\u2026"
  }), result !== null && /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 20,
      fontWeight: 700,
      color: 'var(--accent-green)',
      fontFamily: 'var(--font-mono)',
      marginTop: 4
    }
  }, "= ", typeof result === 'number' ? Number.isInteger(result) ? result : parseFloat(result.toPrecision(10)) : result), error && /*#__PURE__*/React.createElement("div", {
    style: {
      color: 'var(--accent-red)',
      fontSize: 12,
      marginTop: 4
    }
  }, error)), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(5,1fr)',
      gap: 5
    }
  }, BUTTONS.flat().map((val, i) => /*#__PURE__*/React.createElement("button", {
    key: i,
    style: btnStyle(val),
    onClick: () => press(val),
    onMouseOver: e => {
      if (val && val !== '=') e.currentTarget.style.background = 'var(--bg-card-hover)';
    },
    onMouseOut: e => {
      if (val && val !== '=') e.currentTarget.style.background = btnStyle(val).background;
    }
  }, val))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 14,
      fontSize: 11,
      color: 'var(--text-muted)',
      lineHeight: 1.7
    }
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      color: 'var(--text-secondary)'
    }
  }, "Tips:"), " Use Python syntax.", /*#__PURE__*/React.createElement("br", null), "sin/cos/tan take radians \u2014 wrap with ", /*#__PURE__*/React.createElement("code", null, "radians()"), " for degrees.", /*#__PURE__*/React.createElement("br", null), "e.g. ", /*#__PURE__*/React.createElement("code", null, "sin(radians(45))"), " \u2192 0.7071", /*#__PURE__*/React.createElement("br", null), /*#__PURE__*/React.createElement("code", null, "sqrt(9.8 * 2 * 100)"), " \u2192 ballistic velocity")), /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      flex: 1,
      minWidth: 200
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-header",
    style: {
      marginBottom: 10
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "card-title"
  }, "History"), history.length > 0 && /*#__PURE__*/React.createElement("button", {
    className: "btn btn-sm",
    style: {
      fontSize: 11
    },
    onClick: () => setHistory([])
  }, "Clear")), history.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "empty-state"
  }, "No calculations yet.") : /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6
    }
  }, history.map((item, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    onClick: () => loadFromHistory(item),
    style: {
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '8px 12px',
      cursor: 'pointer'
    },
    onMouseOver: e => e.currentTarget.style.background = 'var(--bg-card-hover)',
    onMouseOut: e => e.currentTarget.style.background = 'var(--bg-card)'
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      color: 'var(--text-secondary)'
    }
  }, item.expr), /*#__PURE__*/React.createElement("div", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 14,
      fontWeight: 600,
      color: 'var(--accent-green)'
    }
  }, "= ", typeof item.result === 'number' ? Number.isInteger(item.result) ? item.result : parseFloat(item.result.toPrecision(10)) : item.result))))));
}

// ═══════════════════════════════════════════════
// Section page wrapper — shared tab chrome for grouped pages
// ═══════════════════════════════════════════════
function SectionPage({
  tabs,
  children
}) {
  const [tab, setTab] = useState(tabs[0].id);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "page-tab-bar"
  }, tabs.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.id,
    className: `page-tab-btn${tab === t.id ? ' active' : ''}`,
    onClick: () => setTab(t.id)
  }, t.label))), children(tab));
}
function MeshSection({
  nodes,
  device
}) {
  return /*#__PURE__*/React.createElement(SectionPage, {
    tabs: [{
      id: 'nodes',
      label: 'Nodes'
    }, {
      id: 'topology',
      label: 'Topology'
    }, {
      id: 'power',
      label: 'Power'
    }, {
      id: 'location',
      label: 'Location Sharing'
    }]
  }, tab => /*#__PURE__*/React.createElement(React.Fragment, null, tab === 'nodes' && /*#__PURE__*/React.createElement(NodesPage, {
    nodes: nodes,
    device: device
  }), tab === 'topology' && /*#__PURE__*/React.createElement(TopologyPage, {
    nodes: nodes
  }), tab === 'power' && /*#__PURE__*/React.createElement(PowerPage, {
    nodes: nodes
  }), tab === 'location' && /*#__PURE__*/React.createElement("div", {
    className: "page"
  }, /*#__PURE__*/React.createElement(GpsShareSection, {
    nodes: nodes
  }))));
}
function MapSection({
  nodes,
  trackerDevices,
  device,
  gpsStatus,
  onRemoveTracker
}) {
  const [tab, setTab] = useState('map');
  const [pendingDest, setPendingDest] = useState(null);

  // Global callback so plain-HTML map popups can trigger navigation
  useEffect(() => {
    window._atlasNavTo = (lat, lon, name) => {
      setPendingDest({
        lat,
        lon,
        name
      });
      setTab('navigate');
    };
    return () => {
      delete window._atlasNavTo;
    };
  }, []);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "page-tab-bar"
  }, [{
    id: 'map',
    label: 'Map'
  }, {
    id: 'navigate',
    label: 'Road Nav'
  }].map(t => /*#__PURE__*/React.createElement("button", {
    key: t.id,
    className: `page-tab-btn${tab === t.id ? ' active' : ''}`,
    onClick: () => setTab(t.id)
  }, t.label))), tab === 'map' && /*#__PURE__*/React.createElement(MapPage, {
    nodes: nodes,
    trackerDevices: trackerDevices,
    device: device,
    onRemoveTracker: onRemoveTracker
  }), tab === 'navigate' && /*#__PURE__*/React.createElement(NavigatePage, {
    nodes: nodes,
    device: device,
    gpsStatus: gpsStatus,
    initialDest: pendingDest,
    onDestConsumed: () => setPendingDest(null),
    defaultMode: "road",
    availableModes: NAV_MODES.filter(m => m.value === 'road'),
    searchPlaceholder: "Search road destination or enter lat, lon\u2026"
  }));
}
function HikingSection({
  nodes,
  device,
  gpsStatus
}) {
  const MAX_VISIBLE_TRAILS = 250;
  const [parks, setParks] = useState([]);
  const [parksLoading, setParksLoading] = useState(true);
  const [parksError, setParksError] = useState('');
  const [selectedPark, setSelectedPark] = useState('');
  const [trails, setTrails] = useState([]);
  const [trailsLoading, setTrailsLoading] = useState(false);
  const [trailsError, setTrailsError] = useState('');
  const [selectedTrail, setSelectedTrail] = useState('');
  const [trailQuery, setTrailQuery] = useState('');
  const [pendingDest, setPendingDest] = useState(null);
  const [selectorOpen, setSelectorOpen] = useState(() => !isAtlasMobileClient());
  const deferredTrailQuery = useDeferredValue(trailQuery);
  const visibleTrails = useMemo(() => {
    const needle = deferredTrailQuery.trim().toLowerCase();
    let list = trails;
    if (needle) {
      list = trails.filter(trail => {
        const name = String(trail.name || '').toLowerCase();
        return name.includes(needle);
      });
    }
    let sliced = list.slice(0, MAX_VISIBLE_TRAILS);
    if (selectedTrail && !sliced.some(trail => String(trail.id) === selectedTrail)) {
      const activeTrail = trails.find(trail => String(trail.id) === selectedTrail);
      if (activeTrail) sliced = [activeTrail, ...sliced].slice(0, MAX_VISIBLE_TRAILS);
    }
    return sliced;
  }, [trails, deferredTrailQuery, selectedTrail]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get('/api/hiking/parks');
        if (!cancelled) setParks(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setParksError((e === null || e === void 0 ? void 0 : e.message) || 'Unable to load National Parks.');
      } finally {
        if (!cancelled) setParksLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (!selectedPark) {
      setTrails([]);
      setSelectedTrail('');
      setTrailQuery('');
      setTrailsError('');
      return;
    }
    let cancelled = false;
    setTrailsLoading(true);
    setTrailsError('');
    setTrails([]);
    setSelectedTrail('');
    setTrailQuery('');
    (async () => {
      try {
        const data = await api.get(`/api/hiking/parks/${encodeURIComponent(selectedPark)}/trails`);
        if (!cancelled) setTrails(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setTrailsError((e === null || e === void 0 ? void 0 : e.message) || 'Unable to load park trails.');
      } finally {
        if (!cancelled) setTrailsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedPark]);
  useEffect(() => {
    if (!selectedTrail) return;
    const trail = trails.find(t => String(t.id) === selectedTrail);
    if (!trail) return;
    setPendingDest({
      lat: Number(trail.lat),
      lon: Number(trail.lon),
      name: trail.name
    });
  }, [selectedTrail, trails]);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      flex: 1,
      minHeight: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      padding: '16px 18px',
      margin: '16px 16px 0 16px',
      flexShrink: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      marginBottom: selectorOpen ? 14 : 0,
      cursor: 'pointer'
    },
    onClick: () => setSelectorOpen(o => !o)
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: 36,
      height: 36,
      borderRadius: 12,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, var(--accent-amber-dim), rgba(90,167,255,0.12))',
      border: '1px solid var(--border)',
      color: 'var(--accent-amber)',
      flexShrink: 0
    }
  }, Icons.mountain), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 14,
      fontWeight: 700,
      color: 'var(--text-primary)',
      letterSpacing: '-0.02em'
    }
  }, "National Park Trails"), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--text-muted)'
    }
  }, selectorOpen ? 'Pick a park first, then choose a trail.' : selectedPark ? `Park selected${selectedTrail ? ' · Trail selected' : ''}` : 'Tap to expand')), /*#__PURE__*/React.createElement("svg", {
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2.5",
    style: {
      flexShrink: 0,
      color: 'var(--text-muted)',
      transition: 'transform 200ms',
      transform: selectorOpen ? 'rotate(180deg)' : 'none'
    }
  }, /*#__PURE__*/React.createElement("polyline", {
    points: "6 9 12 15 18 9"
  }))), selectorOpen && /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
      gap: 12,
      padding: '14px',
      borderRadius: '16px',
      background: 'linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01))',
      border: '1px solid var(--border)'
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6,
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, "Park"), /*#__PURE__*/React.createElement("select", {
    className: "input",
    value: selectedPark,
    onChange: e => setSelectedPark(e.target.value),
    disabled: parksLoading,
    style: {
      width: '100%',
      appearance: 'none',
      WebkitAppearance: 'none'
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, parksLoading ? 'Loading parks…' : 'Select a National Park'), parks.map(park => /*#__PURE__*/React.createElement("option", {
    key: park.park_code,
    value: park.park_code
  }, park.name)))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--text-muted)',
      marginBottom: 6,
      textTransform: 'uppercase',
      letterSpacing: '0.05em'
    }
  }, "Trail"), selectedPark && trails.length > MAX_VISIBLE_TRAILS && /*#__PURE__*/React.createElement("input", {
    className: "input",
    value: trailQuery,
    onChange: e => setTrailQuery(e.target.value),
    placeholder: `Filter ${trails.length.toLocaleString()} trails…`,
    style: {
      width: '100%',
      marginBottom: 8
    }
  }), /*#__PURE__*/React.createElement("select", {
    className: "input",
    value: selectedTrail,
    onChange: e => setSelectedTrail(e.target.value),
    disabled: !selectedPark || trailsLoading,
    style: {
      width: '100%',
      appearance: 'none',
      WebkitAppearance: 'none'
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, !selectedPark ? 'Select a park first' : trailsLoading ? 'Loading trails…' : visibleTrails.length ? 'Select a trail' : 'No trails found'), visibleTrails.map(trail => /*#__PURE__*/React.createElement("option", {
    key: trail.id,
    value: String(trail.id)
  }, trail.name))))), selectorOpen && (parksError || trailsError) && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      fontSize: 12,
      color: 'var(--accent-red)',
      background: 'var(--accent-red-dim)',
      border: '1px solid rgba(255,111,111,0.2)',
      borderRadius: '12px',
      padding: '10px 12px'
    }
  }, parksError || trailsError), selectorOpen && !!selectedPark && !trailsLoading && !trailsError && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 10,
      fontSize: 12,
      color: 'var(--text-muted)',
      background: 'rgba(90,167,255,0.08)',
      border: '1px solid var(--border)',
      borderRadius: '12px',
      padding: '10px 12px'
    }
  }, trails.length.toLocaleString(), " trail", trails.length === 1 ? '' : 's', " loaded for the selected park.", trails.length > visibleTrails.length && ` Showing ${visibleTrails.length.toLocaleString()} at a time to keep the page responsive.`)), /*#__PURE__*/React.createElement(NavigatePage, {
    nodes: nodes,
    device: device,
    gpsStatus: gpsStatus,
    initialDest: pendingDest,
    onDestConsumed: () => setPendingDest(null),
    defaultMode: "hiking",
    availableModes: NAV_MODES.filter(m => m.value === 'hiking'),
    autoShowTrailOverlay: false,
    searchPlaceholder: "Search within trails, or enter lat, lon\u2026"
  }));
}
function CommsSection({
  messages,
  alerts,
  setAlerts,
  nodes,
  device,
  gpsStatus,
  refresh
}) {
  return /*#__PURE__*/React.createElement(SectionPage, {
    tabs: [{
      id: 'messages',
      label: 'Messages'
    }, {
      id: 'alerts',
      label: 'Alerts'
    }]
  }, tab => /*#__PURE__*/React.createElement(React.Fragment, null, tab === 'messages' && /*#__PURE__*/React.createElement(MessagesPage, {
    messages: messages,
    nodes: nodes,
    device: device,
    refresh: refresh
  }), tab === 'alerts' && /*#__PURE__*/React.createElement(AlertsPage, {
    alerts: alerts,
    refresh: refresh,
    setAlerts: setAlerts
  })));
}
function ToolsSection() {
  return /*#__PURE__*/React.createElement(SectionPage, {
    tabs: [{
      id: 'calculator',
      label: 'Calculator'
    }, {
      id: 'calendar',
      label: 'Calendar'
    }]
  }, tab => /*#__PURE__*/React.createElement(React.Fragment, null, tab === 'calculator' && /*#__PURE__*/React.createElement(CalculatorPage, null), tab === 'calendar' && /*#__PURE__*/React.createElement(CalendarPage, null)));
}
function SystemSection(props) {
  return /*#__PURE__*/React.createElement(SectionPage, {
    tabs: [{
      id: 'wifi',
      label: 'WiFi'
    }, {
      id: 'stats',
      label: 'Stats'
    }, {
      id: 'settings',
      label: 'Settings'
    }]
  }, tab => /*#__PURE__*/React.createElement(React.Fragment, null, tab === 'wifi' && /*#__PURE__*/React.createElement(WifiPage, null), tab === 'stats' && /*#__PURE__*/React.createElement(SystemPage, null), tab === 'settings' && /*#__PURE__*/React.createElement(SettingsPage, props)));
}

// ═══════════════════════════════════════════════
// Main App
// ═══════════════════════════════════════════════
const DEFAULT_PAGES = [{
  id: 'dashboard',
  icon: Icons.dashboard,
  label: 'Dashboard',
  group: 'main'
}, {
  id: 'mesh',
  icon: Icons.topology,
  label: 'Mesh',
  group: 'mesh'
}, {
  id: 'map',
  icon: Icons.map,
  label: 'Map',
  group: 'map'
}, {
  id: 'hiking',
  icon: Icons.mountain,
  label: 'Hiking',
  group: 'map'
}, {
  id: 'comms',
  icon: Icons.messages,
  label: 'Messages',
  group: 'comms'
}, {
  id: 'tools',
  icon: Icons.calculator,
  label: 'Tools',
  group: 'tools'
}, {
  id: 'system',
  icon: Icons.settings,
  label: 'Settings',
  group: 'sys'
}, {
  id: 'ai',
  icon: Icons.ai,
  label: 'Ray',
  group: 'ai'
}];
const ALWAYS_VISIBLE_PAGES = ['system'];
function App() {
  const searchParams = new URLSearchParams(window.location.search);
  const isAtlasMobileApp = isAtlasMobileClient();
  const isAtlasMobileAndroid = /AtlasMobileAndroid\//.test(navigator.userAgent);
  const dashboardTitle = 'Atlas Control';
  const initialPage = searchParams.get('page') || 'dashboard';
  const [page, setPage] = useState(initialPage);
  const [stats, setStats] = useState({});
  const [nodes, setNodes] = useState([]);
  const [messages, setMessages] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [device, setDevice] = useState({});
  const [trackerDevices, setTrackerDevices] = useState([]);
  const [gpsStatus, setGpsStatus] = useState({
    connected: false,
    fix: null
  });
  const [appSettings, setAppSettings] = useState({
    units: 'metric',
    date_format: 'relative',
    timezone: 'auto',
    show_offline_nodes: 'true',
    node_online_window_h: '2',
    dashboard_refresh_s: '5',
    map_default_zoom: '13',
    battery_alert_pct: '20',
    node_offline_min: '30',
    serial_port: '/dev/ttyACM0'
  });
  const socketRef = useRef(null);
  const intervalRef = useRef(null);
  const [socketConnected, setSocketConnected] = useState(false);

  // Sidebar personalization from localStorage
  const [sidebarOrder, setSidebarOrder] = useState(() => {
    try {
      const s = localStorage.getItem('sidebarOrder');
      return s ? JSON.parse(s) : null;
    } catch {
      return null;
    }
  });
  const [hiddenPages, setHiddenPages] = useState(() => {
    try {
      const s = localStorage.getItem('hiddenPages');
      const parsed = s ? JSON.parse(s) : [];
      return Array.isArray(parsed) ? parsed.filter(id => !ALWAYS_VISIBLE_PAGES.includes(id)) : [];
    } catch {
      return [];
    }
  });
  const [themeMode, setThemeMode] = useState(() => localStorage.getItem('uiTheme') || 'dark');
  const [dragItem, setDragItem] = useState(null);
  const [dragOver, setDragOver] = useState(null);

  // Apply persisted UI theme.
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = themeMode === 'light' ? 'light' : 'dark';
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      const resolved = getComputedStyle(root).getPropertyValue('--theme-color').trim();
      if (resolved) meta.setAttribute('content', resolved);
    }
  }, [themeMode]);
  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([api.get('/api/stats'), api.get('/api/nodes'), api.get('/api/messages'), api.get('/api/alerts'), api.get('/api/device'), api.get('/api/tracker/devices')]);
    const values = results.map(r => r.status === 'fulfilled' ? r.value : undefined);
    const [s, n, m, a, d, t] = values;
    if (s !== undefined) setStats(s);
    if (n !== undefined) setNodes(Array.isArray(n) ? n : []);
    if (m !== undefined) setMessages(Array.isArray(m) ? m : []);
    if (a !== undefined) setAlerts(Array.isArray(a) ? a : []);
    if (d !== undefined) setDevice(d || {});
    if (t !== undefined) setTrackerDevices(Array.isArray(t) ? t : []);
  }, []);

  // Load app settings from server
  useEffect(() => {
    api.get('/api/settings').then(s => {
      if (!s || s.error) return;
      setAppSettings(prev => ({
        ...prev,
        ...s
      }));
      if (s.ui_theme) {
        setThemeMode(s.ui_theme);
        localStorage.setItem('uiTheme', s.ui_theme);
      }
      if (s.hidden_pages) {
        try {
          const parsed = JSON.parse(s.hidden_pages);
          if (Array.isArray(parsed)) {
            const next = parsed.filter(id => !ALWAYS_VISIBLE_PAGES.includes(id));
            setHiddenPages(next);
            localStorage.setItem('hiddenPages', JSON.stringify(next));
          }
        } catch (_) {}
      }
      if (s.sidebar_order) {
        try {
          const parsed = JSON.parse(s.sidebar_order);
          if (Array.isArray(parsed) && parsed.length) {
            setSidebarOrder(parsed);
            localStorage.setItem('sidebarOrder', JSON.stringify(parsed));
          }
        } catch (_) {}
      }
    }).catch(() => {});
  }, []);

  // Load GPS status on mount
  useEffect(() => {
    api.get('/api/gps').then(s => {
      if (s) setGpsStatus(s);
    }).catch(() => {});
  }, []);

  // Data refresh interval — restarts when interval setting changes
  useEffect(() => {
    refresh();
    const baseMs = Math.max(1, parseInt(appSettings.dashboard_refresh_s || '5')) * 1000;
    // When realtime socket updates are healthy, keep a slow background sync
    // instead of hammering the API with full refreshes every few seconds.
    const ms = socketConnected ? Math.max(baseMs, 30000) : baseMs;
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(refresh, ms);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [refresh, appSettings.dashboard_refresh_s, socketConnected]);

  // Map page → room(s) to join
  const pageRooms = {
    dashboard: ['mesh', 'chat'],
    messages: ['chat'],
    nodes: ['mesh', 'telemetry'],
    topology: ['mesh'],
    map: ['mesh', 'map'],
    hiking: ['mesh', 'map'],
    navigate: ['mesh'],
    power: ['system'],
    alerts: ['mesh', 'chat'],
    ai: [],
    system: ['system'],
    settings: []
  };

  // Re-join rooms whenever the active page changes
  useEffect(() => {
    const socket = socketRef.current;
    if (!socket || !socket.connected) return;
    socket.emit('join_rooms', {
      rooms: pageRooms[page] || []
    });
  }, [page]); // eslint-disable-line

  // WebSocket (runs once)
  useEffect(() => {
    try {
      const socket = io(window.location.origin, {
        transports: isAtlasMobileApp ? ['polling'] : ['websocket', 'polling'],
        upgrade: !isAtlasMobileApp
        // perMessageDeflate is negotiated automatically when server supports it
      });
      socketRef.current = socket;
      // Expose to MapPage so it can subscribe to position_update without a second connection.
      window._atlasSocket = socket;
      socket.on('connect', () => {
        setSocketConnected(true);
        // Announce which rooms this client wants on (re)connect
        socket.emit('join_rooms', {
          rooms: pageRooms[page] || []
        });
      });
      socket.on('disconnect', () => {
        setSocketConnected(false);
      });

      // node_update carries the node dict directly — update state without full refresh
      socket.on('node_update', data => {
        if (data && data.node_id) {
          setNodes(prev => {
            const idx = prev.findIndex(n => n.node_id === data.node_id);
            if (idx >= 0) {
              var _data$latitude, _data$longitude;
              const cur = prev[idx];
              const next = [...prev];
              // Preserve existing GPS coordinates — a non-position packet may
              // arrive with null lat/lon (radio node-DB has no fresh position)
              // and a naive spread would wipe the previously-known location.
              next[idx] = {
                ...cur,
                ...data,
                latitude: (_data$latitude = data.latitude) !== null && _data$latitude !== void 0 ? _data$latitude : cur.latitude,
                longitude: (_data$longitude = data.longitude) !== null && _data$longitude !== void 0 ? _data$longitude : cur.longitude
              };
              return next;
            }
            return [data, ...prev];
          });
        } else {
          refresh();
        }
      });

      // position_update: move node marker to new coords immediately
      socket.on('position_update', data => {
        if (data && data.node_id && data.latitude && data.longitude) {
          setNodes(prev => {
            const idx = prev.findIndex(n => n.node_id === data.node_id);
            if (idx < 0) {
              // Node not yet in state — add a stub so it appears on the map
              // immediately. node_update will arrive shortly and fill in the rest.
              return [{
                node_id: data.node_id,
                long_name: 'Unknown',
                short_name: '???',
                hw_model: 'UNKNOWN',
                latitude: data.latitude,
                longitude: data.longitude,
                last_heard: data.timestamp || null,
                ...(data.altitude != null ? {
                  altitude: data.altitude
                } : {})
              }, ...prev];
            }
            const next = [...prev];
            next[idx] = {
              ...next[idx],
              latitude: data.latitude,
              longitude: data.longitude,
              last_heard: data.timestamp || next[idx].last_heard,
              ...(data.altitude != null ? {
                altitude: data.altitude
              } : {})
            };
            return next;
          });
        }
      });

      // new_message carries the message dict directly
      socket.on('new_message', data => {
        if (data && data.from_id) {
          setMessages(prev => {
            // deduplicate by packet_id if present
            if (data.packet_id && prev.some(m => m.packet_id === data.packet_id)) return prev;
            return [data, ...prev].slice(0, 200);
          });
        } else {
          refresh();
        }
      });
      socket.on('new_alert', data => {
        refresh();
        if (Notification.permission === 'granted' && data) {
          new Notification(dashboardTitle + ' Alert', {
            body: data.text || data.node || 'New alert'
          });
        }
      });
      socket.on('connection_status', () => refresh());
      socket.on('tracker_update', dev => {
        if (!dev) return;
        setTrackerDevices(prev => {
          const idx = prev.findIndex(d => d.id === dev.id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = dev;
            return next;
          }
          return [dev, ...prev];
        });
      });
      socket.on('node_removed', ({
        node_id
      }) => {
        setNodes(prev => prev.filter(n => n.node_id !== node_id));
      });
      socket.on('tracker_removed', ({
        id
      }) => {
        setTrackerDevices(prev => prev.filter(d => d.id !== id));
      });
      socket.on('gps_status', data => {
        if (data) setGpsStatus(prev => ({
          ...prev,
          ...data
        }));
      });
      socket.on('gps_update', fix => {
        if (fix) setGpsStatus(prev => ({
          ...prev,
          connected: true,
          fix: {
            ...fix
          }
        }));
      });
    } catch (e) {
      console.log('WebSocket unavailable, using polling');
    }
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    return () => {
      setSocketConnected(false);
      if (socketRef.current) socketRef.current.disconnect();
    };
  }, []); // eslint-disable-line

  // Computed ordered + visible sidebar pages
  const orderedPages = useMemo(() => {
    const allIds = DEFAULT_PAGES.map(p => p.id);
    const order = sidebarOrder || allIds;
    // Ensure all pages are represented (handles new pages added after order was saved)
    const fullOrder = [...order, ...allIds.filter(id => !order.includes(id))];
    return fullOrder.map(id => DEFAULT_PAGES.find(p => p.id === id)).filter(p => p && !hiddenPages.includes(p.id));
  }, [sidebarOrder, hiddenPages]);

  // Sidebar drag-and-drop handlers
  // Drop is handled at the <nav> level so releasing over a divider still works.
  function handleDragStart(e, pageId) {
    setDragItem(pageId);
    e.dataTransfer.effectAllowed = 'move';
  }
  function handleDragEnd() {
    setDragItem(null);
    setDragOver(null);
  }

  // Find the sidebar button whose centre is closest to a given clientY coordinate.
  function closestSidebarPageId(clientY) {
    const btns = document.querySelectorAll('.sidebar-btn[data-page-id]');
    let closest = null,
      minDist = Infinity;
    for (const btn of btns) {
      const rect = btn.getBoundingClientRect();
      const centre = rect.top + rect.height / 2;
      const dist = Math.abs(clientY - centre);
      if (dist < minDist) {
        minDist = dist;
        closest = btn;
      }
    }
    return closest ? closest.getAttribute('data-page-id') : null;
  }
  function handleSidebarDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (!dragItem) return;
    setDragOver(closestSidebarPageId(e.clientY));
  }
  function handleSidebarDragLeave(e) {
    // Only clear the highlight when the cursor leaves the nav entirely.
    if (!e.currentTarget.contains(e.relatedTarget)) setDragOver(null);
  }
  function handleSidebarDrop(e) {
    e.preventDefault();
    setDragOver(null);
    const item = dragItem;
    setDragItem(null);
    if (!item) return;
    const targetId = closestSidebarPageId(e.clientY);
    if (!targetId || targetId === item) return;
    const allIds = DEFAULT_PAGES.map(p => p.id);
    const cur = sidebarOrder || allIds;
    const fullOrder = [...cur, ...allIds.filter(id => !cur.includes(id))];
    const from = fullOrder.indexOf(item);
    const to = fullOrder.indexOf(targetId);
    if (from === -1 || to === -1) return;
    const next = [...fullOrder];
    next.splice(from, 1);
    next.splice(to, 0, item);
    setSidebarOrder(next);
    localStorage.setItem('sidebarOrder', JSON.stringify(next));
    api.put('/api/settings', {
      sidebar_order: JSON.stringify(next)
    }).catch(() => {});
  }
  function onSettingsSaved(newSettings) {
    setAppSettings(prev => ({
      ...prev,
      ...newSettings
    }));
  }
  function persistUiPrefs(next) {
    api.put('/api/settings', next).catch(() => {});
    setAppSettings(prev => ({
      ...prev,
      ...next
    }));
  }
  function updateThemeMode(mode) {
    setThemeMode(mode);
    localStorage.setItem('uiTheme', mode);
    persistUiPrefs({
      ui_theme: mode
    });
  }
  function updateHiddenPages(hp) {
    const next = hp.filter(id => !ALWAYS_VISIBLE_PAGES.includes(id));
    setHiddenPages(next);
    localStorage.setItem('hiddenPages', JSON.stringify(next));
    persistUiPrefs({
      hidden_pages: JSON.stringify(next)
    });
  }
  function resetSidebarOrder() {
    setSidebarOrder(null);
    localStorage.removeItem('sidebarOrder');
    persistUiPrefs({
      sidebar_order: '[]'
    });
  }
  const hasAlerts = alerts.filter(a => !a.acknowledged).length > 0;
  return /*#__PURE__*/React.createElement(SettingsContext.Provider, {
    value: {
      ...appSettings,
      refresh
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "app",
    style: isAtlasMobileApp ? {
      paddingLeft: 0,
      display: 'flex',
      flexDirection: 'column',
      position: 'fixed',
      inset: 0,
      height: '100%',
      overflow: 'hidden'
    } : null
  }, !isAtlasMobileApp && /*#__PURE__*/React.createElement("nav", {
    className: "sidebar",
    onDragOver: handleSidebarDragOver,
    onDragLeave: handleSidebarDragLeave,
    onDrop: handleSidebarDrop
  }, /*#__PURE__*/React.createElement("div", {
    className: "sidebar-logo",
    title: dashboardTitle
  }, /*#__PURE__*/React.createElement("img", {
    src: "/static/img/atlas-control-logo.png",
    alt: "Atlas Control"
  })), orderedPages.map((p, i) => /*#__PURE__*/React.createElement(React.Fragment, {
    key: p.id
  }, i > 0 && p.group !== orderedPages[i - 1].group && /*#__PURE__*/React.createElement("div", {
    className: "sidebar-divider"
  }), /*#__PURE__*/React.createElement("button", {
    className: `sidebar-btn ${page === p.id ? 'active' : ''} ${dragItem === p.id ? 'dragging' : ''} ${dragOver === p.id && dragItem !== p.id ? 'drag-over' : ''}`,
    onClick: () => setPage(p.id),
    "data-tip": p.label,
    "data-page-id": p.id,
    draggable: true,
    onDragStart: e => handleDragStart(e, p.id),
    onDragEnd: handleDragEnd
  }, p.icon, p.id === 'comms' && hasAlerts && /*#__PURE__*/React.createElement("span", {
    className: "badge"
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "main-content",
    style: isAtlasMobileApp ? {
      marginLeft: 0,
      width: '100%',
      maxWidth: '100%',
      padding: '0',
      flex: '1',
      minHeight: '0',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden'
    } : null
  }, /*#__PURE__*/React.createElement("header", {
    className: "top-bar",
    style: isAtlasMobileApp ? {
      padding: '10px 12px',
      paddingTop: isAtlasMobileAndroid ? '10px' : 'calc(env(safe-area-inset-top, 0px) + 10px)',
      minHeight: 'auto'
    } : null
  }, /*#__PURE__*/React.createElement("h1", null, dashboardTitle), /*#__PURE__*/React.createElement("span", {
    className: "hide-mobile",
    style: {
      fontSize: 12,
      color: 'var(--text-muted)',
      fontFamily: 'var(--font-mono)'
    }
  }, appSettings.serial_port || '/dev/ttyACM0'), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: 'auto',
      display: 'flex',
      gap: '12px',
      alignItems: 'center'
    }
  }, /*#__PURE__*/React.createElement(TopBarClock, null), device.battery_pct != null && (() => {
    const batteryBits = [`ATLAS ${device.battery_pct}%`];
    const batteryStatusLabel = (() => {
      const phase = String(device.battery_phase || '').toLowerCase();
      const status = String(device.battery_status || '').toLowerCase();
      if (phase === 'topping_off') return 'TOPPING OFF';
      if (status) return status.toUpperCase();
      if (phase) return phase.replaceAll('_', ' ').toUpperCase();
      return null;
    })();
    if (device.battery_voltage != null) batteryBits.push(`${Number(device.battery_voltage).toFixed(2)}V`);
    if (device.battery_current_ma != null) batteryBits.push(`${(Math.abs(Number(device.battery_current_ma)) / 1000).toFixed(2)}A`);
    if (device.battery_power_w != null) batteryBits.push(`${Math.abs(Number(device.battery_power_w)).toFixed(1)}W`);
    if (batteryStatusLabel) batteryBits.push(batteryStatusLabel);
    const batteryTitleBits = [];
    if (device.battery_pct != null) batteryTitleBits.push(`display ${device.battery_pct}%`);
    if (device.battery_pct_raw != null) batteryTitleBits.push(`raw ${device.battery_pct_raw}%`);
    if (device.battery_voltage != null) batteryTitleBits.push(`voltage ${Number(device.battery_voltage).toFixed(3)}V`);
    if (device.battery_voltage_raw != null) batteryTitleBits.push(`raw voltage ${Number(device.battery_voltage_raw).toFixed(3)}V`);
    if (device.battery_current_ma != null) batteryTitleBits.push(`current ${(Number(device.battery_current_ma) / 1000).toFixed(3)}A`);
    if (device.battery_power_w != null) batteryTitleBits.push(`power ${Number(device.battery_power_w).toFixed(3)}W`);
    if (device.battery_power_w_raw != null) batteryTitleBits.push(`raw power ${Number(device.battery_power_w_raw).toFixed(3)}W`);
    if (device.battery_status) batteryTitleBits.push(`status ${device.battery_status}`);
    if (device.battery_phase) batteryTitleBits.push(`phase ${device.battery_phase}`);
    if (device.battery_is_full != null) batteryTitleBits.push(`full ${device.battery_is_full ? 'yes' : 'no'}`);
    if (device.battery_soc_note) batteryTitleBits.push(`note ${device.battery_soc_note}`);
    if (device.battery_soc_model) batteryTitleBits.push(`model ${device.battery_soc_model}`);
    if (device.battery_source) batteryTitleBits.push(`source ${device.battery_source}`);
    return /*#__PURE__*/React.createElement("div", {
      className: `connection-badge ${device.battery_pct < 20 ? 'offline' : device.battery_pct < 50 ? 'dead-reckoning' : 'online'}`,
      title: `Atlas battery · ${batteryTitleBits.join(' · ')}`
    }, /*#__PURE__*/React.createElement("span", {
      className: "dot"
    }), /*#__PURE__*/React.createElement("span", null, batteryBits.join(' · ')));
  })(), /*#__PURE__*/React.createElement("div", {
    className: `connection-badge ${device.connected ? 'online' : 'offline'}`,
    title: `Meshtastic: ${appSettings.serial_port || '/dev/ttyACM1'}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "dot"
  }), /*#__PURE__*/React.createElement("span", {
    className: "hide-mobile"
  }, device.long_name ? `${device.long_name} · ` : '', device.connected ? 'CONNECTED' : 'DISCONNECTED')), (() => {
    const fix = gpsStatus.fix;
    const isDR = fix && fix.fix_qual === 6;
    const badgeClass = !gpsStatus.connected ? 'offline' : isDR ? 'dead-reckoning' : fix ? 'online' : 'offline';
    const label = !gpsStatus.connected ? 'GPS OFF' : isDR ? 'GPS DR' : fix ? `GPS ${fix.sats_in_view != null ? fix.sats_in_view + 'sat' : 'FIX'}` : 'GPS NO FIX';
    return /*#__PURE__*/React.createElement("div", {
      className: `connection-badge ${badgeClass}`,
      title: `GPS: ${gpsStatus.port || '/dev/ttyACM0'}`
    }, /*#__PURE__*/React.createElement("span", {
      className: "dot"
    }), /*#__PURE__*/React.createElement("span", {
      className: "hide-mobile"
    }, label));
  })())), page === 'dashboard' && /*#__PURE__*/React.createElement(DashboardPage, {
    stats: stats,
    nodes: nodes,
    messages: messages,
    alerts: alerts,
    setPage: setPage,
    device: device,
    gpsStatus: gpsStatus,
    trackerDevices: trackerDevices,
    isAtlasMobileApp: isAtlasMobileApp
  }), page === 'mesh' && /*#__PURE__*/React.createElement(MeshSection, {
    nodes: nodes,
    device: device
  }), page === 'map' && /*#__PURE__*/React.createElement(MapSection, {
    nodes: nodes,
    trackerDevices: trackerDevices,
    device: device,
    gpsStatus: gpsStatus,
    onRemoveTracker: id => api.del(`/api/tracker/devices/${id}`).then(() => setTrackerDevices(prev => prev.filter(d => d.id !== id)))
  }), page === 'hiking' && /*#__PURE__*/React.createElement(HikingSection, {
    nodes: nodes,
    device: device,
    gpsStatus: gpsStatus
  }), page === 'comms' && /*#__PURE__*/React.createElement(CommsSection, {
    messages: messages,
    alerts: alerts,
    setAlerts: setAlerts,
    nodes: nodes,
    device: device,
    gpsStatus: gpsStatus,
    refresh: refresh
  }), page === 'tools' && /*#__PURE__*/React.createElement(ToolsSection, null), page === 'system' && /*#__PURE__*/React.createElement(SystemSection, {
    onSettingsSaved: onSettingsSaved,
    themeMode: themeMode,
    onThemeModeChange: updateThemeMode,
    hiddenPages: hiddenPages,
    onHiddenPagesChange: updateHiddenPages,
    onResetSidebarOrder: resetSidebarOrder,
    defaultPages: DEFAULT_PAGES,
    nodes: nodes
  }), page === 'ai' && /*#__PURE__*/React.createElement(AIPage, null), isAtlasMobileApp && /*#__PURE__*/React.createElement("nav", {
    style: {
      display: 'flex',
      flexDirection: 'row',
      flexShrink: 0,
      height: '80px',
      borderTop: '1px solid rgba(126,156,191,0.12)',
      background: 'linear-gradient(180deg,rgba(14,23,37,0.9),rgba(10,17,28,0.98))',
      paddingBottom: 'env(safe-area-inset-bottom,0)',
      overflowX: 'auto',
      scrollbarWidth: 'none'
    }
  }, orderedPages.map(p => /*#__PURE__*/React.createElement("button", {
    key: p.id,
    className: `sidebar-btn ${page === p.id ? 'active' : ''}`,
    style: {
      flex: 1,
      borderRadius: 0,
      height: '100%',
      margin: 0,
      width: 'auto',
      minWidth: 44
    },
    onClick: () => setPage(p.id)
  }, p.icon, p.id === 'comms' && hasAlerts && /*#__PURE__*/React.createElement("span", {
    className: "badge"
  })))))));
}
ReactDOM.createRoot(document.getElementById('root')).render( /*#__PURE__*/React.createElement(App, null));