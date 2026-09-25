// node tests/js/safe_href.test.js — exits non-zero on failure.
'use strict';
const assert = require('assert');
const { atlasSafeHref } = require('../../static/js/atlas-safety.js');

const ch = (code) => String.fromCharCode(code);
const TAB = ch(9), NL = ch(10), SOH = ch(1), ZWSP = ch(0x200b), RLO = ch(0x202e);

const allowed = [
  'https://example.com/page?q=1#x',
  'http://192.168.1.20/map',
  ' HTTPS://Example.com ',
  '/api/ai/documents/3',
  '#section-2',
];
const blocked = [
  'javascript:alert(1)',
  'JaVaScRiPt:alert(1)',
  ' javascript:alert(1)',
  'java' + TAB + 'script:alert(1)',
  'java' + NL + 'script:alert(1)',
  SOH + 'javascript:alert(1)',
  'jav' + ZWSP + 'ascript:alert(1)',
  RLO + 'javascript:alert(1)',
  'javascript&colon;alert(1)',
  'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
  'vbscript:msgbox(1)',
  'file:///etc/passwd',
  '//attacker.example/x',
  '/' + ch(92) + 'attacker.example',
  ch(92) + ch(92) + 'attacker.example' + ch(92) + 'share',
  'ftp://example.com',
  'blob:https://example.com/uuid',
  'mailto:someone@example.com',
  'ht' + ZWSP + 'tps://example.com',
  'relative/path',
  '',
  null,
  undefined,
];

let failures = 0;
for (const u of allowed) {
  try { assert.ok(atlasSafeHref(u), `should allow ${JSON.stringify(u)}`); }
  catch (e) { failures++; console.error('FAIL', e.message); }
}
for (const u of blocked) {
  try { assert.strictEqual(atlasSafeHref(u), null, `should block ${JSON.stringify(u)}`); }
  catch (e) { failures++; console.error('FAIL', e.message); }
}
if (failures) { console.error(`${failures} failure(s)`); process.exit(1); }
console.log(`safe_href: ${allowed.length + blocked.length} cases passed`);
