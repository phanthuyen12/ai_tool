#!/usr/bin/env node
// Drains queue.sqlite one job at a time (same resource-conscious, single-concurrency
// pattern as the Python backend's Dramatiq worker) and renders each into out/.
// Run with: node worker.mjs   (or ../motion-worker from the project root)
import {existsSync, readFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {generateAndRender} from './scripts/lib/pipeline.mjs';
import {getNextPendingJob, getSettings, updateJob} from './db.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = __dirname;
const POLL_INTERVAL_MS = 2000;

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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function processOne(job) {
  // Settings saved via the /motion web UI (stored in SQLite) take priority over
  // .env, so the key can be entered through the browser without editing files.
  const settings = getSettings();
  const baseUrl = settings?.base_url || process.env.MOTION_LLM_BASE_URL || 'https://api.openai.com/v1';
  const apiKey = settings?.api_key || process.env.MOTION_LLM_API_KEY || '';
  const model = settings?.model || process.env.MOTION_LLM_MODEL || 'gpt-4o-mini';

  if (!apiKey) {
    updateJob(job.id, {
      status: 'failed',
      error: 'No LLM API key configured. Set it on the /motion page (AI settings) or in motion-studio/.env.',
    });
    console.error(`[job ${job.id}] FAILED: no API key configured`);
    return;
  }

  console.log(`[job ${job.id}] rendering: "${job.description.slice(0, 60)}..."`);
  updateJob(job.id, {status: 'processing', error: null});
  try {
    const result = await generateAndRender({
      rootDir: ROOT_DIR,
      description: job.description,
      duration: job.duration_sec,
      aspect: job.aspect,
      baseUrl,
      apiKey,
      model,
    });
    updateJob(job.id, {status: 'completed', output_path: result.outputPath, error: null});
    console.log(`[job ${job.id}] done -> ${result.outputPath}`);
  } catch (err) {
    updateJob(job.id, {status: 'failed', error: String(err.message || err).slice(0, 2000)});
    console.error(`[job ${job.id}] FAILED:`, err.message || err);
  }
}

async function main() {
  loadEnvFile(join(ROOT_DIR, '.env'));
  console.log('motion-studio worker started. Polling queue.sqlite every', POLL_INTERVAL_MS, 'ms. Ctrl+C to stop.');
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const job = getNextPendingJob();
    if (job) {
      await processOne(job);
    } else {
      await sleep(POLL_INTERVAL_MS);
    }
  }
}

main();
