#!/usr/bin/env node
/**
 * compile_jsx.js — pre-compiles the Atlas JSX app block to plain JS.
 *
 * Uses @babel/standalone (already vendored at static/vendor/babel.min.js)
 * so no npm install is required. Called once at Flask startup.
 *
 * Usage: node compile_jsx.js <input.jsx> <output.js>
 */
'use strict';

const fs   = require('fs');
const path = require('path');
const vm   = require('vm');

const [, , inputFile, outputFile] = process.argv;
if (!inputFile || !outputFile) {
  process.stderr.write('Usage: node compile_jsx.js <input.jsx> <output.js>\n');
  process.exit(1);
}

// ── Load Babel standalone ──────────────────────────────────────────────────
const babelPath = path.resolve(__dirname, 'static', 'vendor', 'babel.min.js');

let Babel;

// Attempt 1: direct require (works when babel.min.js is a proper UMD bundle)
try {
  Babel = require(babelPath);
  if (!Babel || typeof Babel.transform !== 'function') throw new Error('no .transform');
} catch (_) {
  // Attempt 2: eval in a VM context that provides window/global shims
  try {
    const ctx = vm.createContext({
      window: {},
      global: global,
      process,
      console,
    });
    const src = fs.readFileSync(babelPath, 'utf8');
    vm.runInContext(src, ctx);
    Babel = ctx.Babel || ctx.window.Babel;
  } catch (e) {
    process.stderr.write('Failed to load Babel standalone: ' + e.message + '\n');
    process.exit(2);
  }
}

if (!Babel || typeof Babel.transform !== 'function') {
  process.stderr.write('Babel.transform not available after loading\n');
  process.exit(2);
}

// ── Compile ────────────────────────────────────────────────────────────────
const jsx = fs.readFileSync(inputFile, 'utf8');

let result;
try {
  result = Babel.transform(jsx, {
    presets: [[
      'env',
      {
        // The embedded Atlas dashboard runs inside Android System WebView,
        // which can lag well behind desktop Chromium on real devices.
        targets: { chrome: '80' },
        bugfixes: true,
      },
    ], 'react'],
    plugins: [
      'transform-optional-chaining',
      'transform-nullish-coalescing-operator',
    ],
    sourceType: 'script',
  });
} catch (e) {
  process.stderr.write('Babel transform error: ' + e.message + '\n');
  process.exit(3);
}

fs.writeFileSync(outputFile, result.code, 'utf8');
process.stdout.write('Compiled ' + path.basename(inputFile) + ' → ' + path.basename(outputFile)
  + ' (' + Math.round(result.code.length / 1024) + ' KB)\n');
