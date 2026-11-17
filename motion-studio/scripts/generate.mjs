#!/usr/bin/env node
// Standalone AI code-generated video pipeline (Remotion/React), one-off CLI entry point.
// Independent from ../backend (Flow2API/Veo3) by design.
//
// Usage:
//   npm run create -- "mô tả cảnh bạn muốn" --duration 8 --aspect 9:16 --output out/scene.mp4
//
// For batch/queued generation from the web UI, see worker.mjs instead — it drains
// the same queue.sqlite that the Next.js frontend writes jobs into.

import {existsSync, readFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {ASPECTS, generateAndRender} from './lib/pipeline.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = resolve(__dirname, '..');

function loadEnvFile(path) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

function parseArgs(argv) {
  const args = {_: []};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (tok.startsWith('--')) {
      const key = tok.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith('--')) {
        args[key] = next;
        i++;
      } else {
        args[key] = true;
      }
    } else {
      args._.push(tok);
    }
  }
  return args;
}

async function main() {
  loadEnvFile(join(ROOT_DIR, '.env'));

  const argv = process.argv.slice(2);
  const args = parseArgs(argv);
  const description = args._.join(' ').trim();
  if (!description) {
    console.error('Usage: npm run create -- "mô tả cảnh bạn muốn" [--duration 8] [--aspect 9:16] [--output out/scene.mp4]');
    process.exit(1);
  }

  const duration = Number(args.duration || 8);
  const aspect = String(args.aspect || '9:16');
  if (!ASPECTS[aspect]) {
    console.error(`Unknown --aspect "${aspect}". Use one of: ${Object.keys(ASPECTS).join(', ')}`);
    process.exit(1);
  }
  const fps = Number(args.fps || 30);

  const baseUrl = args['base-url'] || process.env.MOTION_LLM_BASE_URL || 'https://api.openai.com/v1';
  const apiKey = args['api-key'] || process.env.MOTION_LLM_API_KEY || '';
  const model = args.model || process.env.MOTION_LLM_MODEL || 'gpt-4o-mini';

  if (!apiKey) {
    console.error(
      'No LLM API key configured. Copy .env.example to .env and set MOTION_LLM_API_KEY ' +
        '(and MOTION_LLM_BASE_URL/MOTION_LLM_MODEL if not using OpenAI directly).'
    );
    process.exit(1);
  }

  console.log(`[1/2] Asking ${model} to generate the Remotion scene...`);
  const outputPath = args.output ? resolve(ROOT_DIR, args.output) : undefined;
  const result = await generateAndRender({rootDir: ROOT_DIR, description, duration, aspect, fps, baseUrl, apiKey, model, outputPath});
  console.log(`[2/2] Rendered.`);
  console.log(`\nDone: ${result.outputPath}`);
}

main().catch((err) => {
  console.error('\nFAILED:', err.message || err);
  process.exit(1);
});
