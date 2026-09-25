/*
 * Atlas web safety helpers, loaded before the app bundle.
 *
 * atlasSafeHref(url) — the only way a link from Ray's (model-written, and
 *   therefore steerable) Markdown becomes an <a href>. Allows http(s) URLs,
 *   same-origin absolute paths ("/…", never protocol-relative "//…") and
 *   in-page anchors. Everything else — javascript:, data:, vbscript:, file:,
 *   obfuscated variants — returns null and the caller renders plain text.
 *
 * fetch() — same-origin state-changing requests carry X-Atlas-CSRF. A
 *   custom header cannot be sent cross-site without a CORS preflight, which
 *   Atlas never grants, so the server can tell its own pages from forgeries.
 */
(function (root) {
  'use strict';

  // Code points browsers silently drop or ignore while parsing a URL scheme
  // (controls, spaces, soft hyphen, zero-width and bidi formatting marks).
  var INVISIBLE_RANGES = [
    [0x00, 0x20], [0x7f, 0xa0], [0xad, 0xad], [0x61c, 0x61c], [0x115f, 0x1160],
    [0x17b4, 0x17b5], [0x180e, 0x180e], [0x200b, 0x200f], [0x2028, 0x202e],
    [0x2060, 0x206f], [0x3164, 0x3164], [0xfeff, 0xfeff], [0xffa0, 0xffa0]
  ];

  function isInvisible(code) {
    for (var i = 0; i < INVISIBLE_RANGES.length; i++) {
      if (code >= INVISIBLE_RANGES[i][0] && code <= INVISIBLE_RANGES[i][1]) return true;
    }
    return false;
  }

  function stripInvisible(s) {
    var out = '';
    for (var i = 0; i < s.length; i++) {
      if (!isInvisible(s.charCodeAt(i))) out += s.charAt(i);
    }
    return out;
  }

  function atlasSafeHref(raw) {
    if (raw === null || raw === undefined) return null;
    var s = String(raw).trim();
    var compact = stripInvisible(s);
    if (!compact) return null;
    var first = compact.charAt(0);
    if (first === '#') return s;
    if (first === '/') {
      var second = compact.charAt(1);
      return (second === '/' || second === '\\') ? null : s;
    }
    var m = /^([a-z][a-z0-9+.\-]*):/i.exec(compact);
    if (!m) return null;
    var scheme = m[1].toLowerCase();
    if (scheme !== 'http' && scheme !== 'https') return null;
    // Scheme must be spelled plainly in the original too (no hidden characters
    // splitting it) so what we checked is what the browser will use.
    if (!/^https?:/i.test(s)) return null;
    return s;
  }

  root.atlasSafeHref = atlasSafeHref;

  var MUTATING = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };

  function csrfToken() {
    return root.__atlasCsrf || '1';
  }
  root.atlasCsrfToken = csrfToken;

  if (typeof root.fetch === 'function' && root.location) {
    var nativeFetch = root.fetch.bind(root);
    root.fetch = function (input, init) {
      try {
        var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
        var url = new URL(typeof input === 'string' ? input : (input && input.url) || '', root.location.href);
        if (MUTATING[method] && url.origin === root.location.origin) {
          init = Object.assign({}, init || {});
          var headers = new Headers(init.headers || (input && input.headers) || {});
          if (!headers.has('X-Atlas-CSRF')) headers.set('X-Atlas-CSRF', csrfToken());
          init.headers = headers;
        }
      } catch (e) { /* fall through with the original arguments */ }
      return nativeFetch(input, init);
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { atlasSafeHref: atlasSafeHref };
  }
})(typeof window !== 'undefined' ? window : globalThis);
