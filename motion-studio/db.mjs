// Lightweight batch job queue for motion-studio, backed by node's built-in sqlite
// module (Node >=22.5, no extra dependency / no Redis). Shared by worker.mjs (which
// drains the queue) and read by the Next.js frontend's API routes (which enqueue
// jobs and poll status) by opening this same file.
import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const DB_PATH = join(__dirname, 'queue.sqlite');

let dbInstance = null;

export function getDb() {
  if (!dbInstance) {
    dbInstance = new DatabaseSync(DB_PATH);
    dbInstance.exec(`
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        batch_label TEXT,
        description TEXT NOT NULL,
        duration_sec REAL NOT NULL DEFAULT 8,
        aspect TEXT NOT NULL DEFAULT '9:16',
        status TEXT NOT NULL DEFAULT 'pending',
        output_path TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      )
    `);
    dbInstance.exec(`
      CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        base_url TEXT NOT NULL DEFAULT 'https://api.deepseek.com/v1',
        api_key TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT 'deepseek-chat',
        updated_at TEXT NOT NULL
      )
    `);
  }
  return dbInstance;
}

// LLM settings entered via the /motion web UI (SQLite-backed so the worker doesn't
// require hand-editing .env). Falls back to env vars in worker.mjs when unset.
export function getSettings() {
  const db = getDb();
  return db.prepare(`SELECT * FROM settings WHERE id = 1`).get();
}

export function saveSettings({baseUrl, apiKey, model} = {}) {
  const db = getDb();
  const now = new Date().toISOString();
  const existing = getSettings();
  if (existing) {
    const sets = [];
    const values = [];
    if (baseUrl !== undefined) {
      sets.push('base_url = ?');
      values.push(baseUrl);
    }
    if (apiKey) {
      sets.push('api_key = ?');
      values.push(apiKey);
    }
    if (model !== undefined) {
      sets.push('model = ?');
      values.push(model);
    }
    sets.push('updated_at = ?');
    values.push(now);
    db.prepare(`UPDATE settings SET ${sets.join(', ')} WHERE id = 1`).run(...values);
  } else {
    db.prepare(
      `INSERT INTO settings (id, base_url, api_key, model, updated_at) VALUES (1, ?, ?, ?, ?)`
    ).run(baseUrl || 'https://api.deepseek.com/v1', apiKey || '', model || 'deepseek-chat', now);
  }
}

export function enqueueJobs(items, batchLabel) {
  const db = getDb();
  const now = new Date().toISOString();
  const insert = db.prepare(`
    INSERT INTO jobs (id, batch_label, description, duration_sec, aspect, status, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
  `);
  const ids = [];
  for (const item of items) {
    const id = randomUUID();
    insert.run(id, batchLabel ?? null, item.description, item.duration ?? 8, item.aspect ?? '9:16', now, now);
    ids.push(id);
  }
  return ids;
}

export function listJobs(limit = 300) {
  const db = getDb();
  return db.prepare(`SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?`).all(limit);
}

export function getJob(id) {
  const db = getDb();
  return db.prepare(`SELECT * FROM jobs WHERE id = ?`).get(id);
}

export function getNextPendingJob() {
  const db = getDb();
  return db.prepare(`SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1`).get();
}

export function updateJob(id, fields) {
  const db = getDb();
  const keys = Object.keys(fields);
  const sets = keys.map((k) => `${k} = ?`).join(', ');
  const values = keys.map((k) => fields[k]);
  db.prepare(`UPDATE jobs SET ${sets}, updated_at = ? WHERE id = ?`).run(...values, new Date().toISOString(), id);
}

export function deleteJob(id) {
  const db = getDb();
  db.prepare(`DELETE FROM jobs WHERE id = ? AND status = 'pending'`).run(id);
}
