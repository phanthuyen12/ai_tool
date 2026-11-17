// Shared generation pipeline: description -> LLM-generated Remotion TSX -> rendered MP4.
// Used by both the one-off CLI (scripts/generate.mjs) and the batch queue worker (worker.mjs).
import {execFileSync} from 'node:child_process';
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

export const ASPECTS = {
  '9:16': {width: 1080, height: 1920},
  '16:9': {width: 1920, height: 1080},
  '1:1': {width: 1080, height: 1080},
};

export function slugify(text) {
  return (
    text
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '')
      .slice(0, 40) || 'scene'
  );
}

function extractCode(raw) {
  const fence = raw.match(/```(?:tsx|jsx|ts|js)?\s*([\s\S]*?)```/);
  if (!fence) {
    // No closing fence usually means the response was cut off mid-generation
    // (hit max_tokens) rather than genuinely lacking code — never fall back to
    // the raw text in that case, it's almost always a truncated code block.
    throw new Error(
      'AI output has no closing ``` fence — the response was likely truncated (hit max_tokens) before ' +
        'finishing the code. Try a shorter/simpler description or a shorter duration. Raw response tail:\n' +
        raw.slice(-1500)
    );
  }
  const code = fence[1].trim();
  if (!/export\s+default/.test(code)) {
    throw new Error('AI output missing a default export. Raw response:\n' + raw.slice(0, 2000));
  }
  return code;
}

function sanitizeReservedExports(code) {
  return code
    .split('\n')
    .filter((line) => !/^\s*export\s+const\s+(durationInSeconds|fps|width|height)\s*=/.test(line))
    .join('\n');
}

async function callLLM({baseUrl, apiKey, model, prompt}) {
  const url = `${baseUrl.replace(/\/$/, '')}/chat/completions`;
  const headers = {'Content-Type': 'application/json'};
  if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      model,
      temperature: 0.5,
      max_tokens: 8000,
      messages: [
        {
          role: 'system',
          content: 'You are an expert Remotion (React video) developer. You output ONLY a single TSX code block, nothing else.',
        },
        {role: 'user', content: prompt},
      ],
    }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`LLM request failed: HTTP ${res.status} ${res.statusText}\n${body.slice(0, 1000)}`);
  }
  const data = await res.json();
  const text = data?.choices?.[0]?.message?.content;
  const finishReason = data?.choices?.[0]?.finish_reason;
  if (!text) throw new Error('LLM response had no content: ' + JSON.stringify(data).slice(0, 1000));
  if (finishReason === 'length') {
    throw new Error(
      'LLM response was cut off (hit max_tokens) before finishing the code. Try a shorter/simpler description, ' +
        'or split it into a shorter duration — a very long multi-beat story in one scene can exceed the output budget.'
    );
  }
  return text;
}

function buildPrompt({description, durationSeconds, width, height}) {
  return `Generate a single self-contained Remotion scene as a React functional component.

Scene description: ${description}

Hard requirements:
- Canvas size is fixed at ${width}x${height}px, target duration is ~${durationSeconds} seconds.
- Import only from 'react' and 'remotion' (useCurrentFrame, useVideoConfig, interpolate, spring, Sequence, AbsoluteFill, Easing, etc). No other npm packages, no remote image/font URLs, no network calls.
- Output budget is limited. For a long duration (30s+) or a multi-beat story, generate data (e.g. a candle/price history) with a short loop and a seeded/derived formula instead of hand-writing long literal arrays — this keeps the file compact enough to finish within the token limit. Favor fewer, reusable helper functions over repeating similar JSX blocks per story beat.
- Build all visuals with inline SVG, CSS, and text. Use useVideoConfig() inside the component to read { fps, durationInFrames, width, height } instead of hardcoding them, so animation timing (fade in/out, pacing) scales correctly to the actual duration.
- Animate using useCurrentFrame() + interpolate()/spring() — do not render a static frame.
- CRITICAL pacing rule: something must keep visibly changing for the ENTIRE durationInFrames, not just the first portion. A common bug is computing a fixed-size dataset (e.g. N candles) with a fixed per-item step and finishing it long before the video ends, leaving the rest of the video static. Instead, derive the per-item step FROM durationInFrames (e.g. \`stepFrames = durationInFrames / totalItems\`) so the last item finishes right as the video ends, or keep adding smaller continuous motion (camera pan/zoom, price ticker, live pulse) that spans the full timeline after the main content settles.
- CRITICAL interpolate() rule: every inputRange array must be strictly increasing — no two values may be equal, even if they come from adjacent "phase" boundaries (e.g. phase1.end === phase2.start is a common bug). Before writing any multi-keyframe interpolate() call, double check every value in the array is strictly greater than the one before it; if two phase boundaries could be equal, add +1 to the later one.
- Export the component as the default export. Do NOT export durationInSeconds, fps, width, or height yourself — the caller adds those.
- Output ONLY one \`\`\`tsx fenced code block with the full file contents (imports + component). No prose before or after.`;
}

/**
 * Full pipeline for one job: ask the LLM for a Remotion scene, write it into the
 * project's live composition slot, and render it to an MP4.
 *
 * @param {object} opts
 * @param {string} opts.rootDir - motion-studio project root (contains src/, out/, node_modules/).
 * @param {string} opts.description - scene description / prompt.
 * @param {number} opts.duration - target duration in seconds.
 * @param {string} opts.aspect - one of '9:16' | '16:9' | '1:1'.
 * @param {number} [opts.fps]
 * @param {string} opts.baseUrl - LLM base URL (OpenAI-compatible).
 * @param {string} opts.apiKey
 * @param {string} opts.model
 * @param {string} [opts.outputPath] - absolute output MP4 path; defaults to out/<slug>-<stamp>.mp4.
 * @returns {Promise<{outputPath: string, historyPath: string}>}
 */
export async function generateAndRender(opts) {
  const {rootDir, description, duration, aspect, fps = 30, baseUrl, apiKey, model} = opts;
  const dims = ASPECTS[aspect];
  if (!dims) throw new Error(`Unknown aspect "${aspect}". Use one of: ${Object.keys(ASPECTS).join(', ')}`);
  if (!apiKey) throw new Error('No LLM API key configured (MOTION_LLM_API_KEY).');

  const prompt = buildPrompt({description, durationSeconds: duration, width: dims.width, height: dims.height});
  const raw = await callLLM({baseUrl, apiKey, model, prompt});
  const code = sanitizeReservedExports(extractCode(raw));

  const finalCode =
    code.trimEnd() +
    `\n\nexport const durationInSeconds = ${duration};\nexport const fps = ${fps};\nexport const width = ${dims.width};\nexport const height = ${dims.height};\n`;

  const currentPath = join(rootDir, 'src/compositions/generated/Current.tsx');
  writeFileSync(currentPath, finalCode, 'utf8');

  const slug = slugify(description);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const historyDir = join(rootDir, 'src/compositions/generated/history');
  mkdirSync(historyDir, {recursive: true});
  const historyPath = join(historyDir, `${stamp}-${slug}.tsx`);
  writeFileSync(historyPath, finalCode, 'utf8');

  const outDir = join(rootDir, 'out');
  mkdirSync(outDir, {recursive: true});
  const outputPath = opts.outputPath ? opts.outputPath : join(outDir, `${slug}-${stamp}.mp4`);
  if (!existsSync(outDir)) mkdirSync(outDir, {recursive: true});

  execFileSync('npx', ['remotion', 'render', 'src/index.ts', 'Generated', outputPath], {
    cwd: rootDir,
    stdio: 'inherit',
  });

  return {outputPath, historyPath};
}
