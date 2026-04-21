/**
 * debug-ctx-pct.mjs — verifica se o % de contexto actualiza em tempo real
 *
 * Usa dois canais em paralelo:
 *   1. SSE raw — lê /events directamente e loga session_ctx_tokens
 *   2. Playwright — abre o monitor e verifica se o DOM muda
 *
 * Uso:
 *   node debug-ctx-pct.mjs [project-name] [duration-seconds]
 *
 * Exemplo:
 *   node debug-ctx-pct.mjs project-finances 30
 */

import { chromium } from 'playwright';
import http from 'http';

const PROJECT = process.argv[2] || 'project-finances';
const DURATION_S = parseInt(process.argv[3] || '30', 10);
const MONITOR_URL = 'http://localhost:19001';

// ── 1. SSE raw reader ─────────────────────────────────────────────────────────
function watchSSE(projectName, durationMs) {
  return new Promise((resolve) => {
    const seen = [];
    const start = Date.now();

    console.log(`\n[SSE] A observar eventos para "${projectName}" por ${durationMs / 1000}s...\n`);

    const req = http.get(`${MONITOR_URL}/events`, (res) => {
      let buf = '';
      res.on('data', (chunk) => {
        buf += chunk.toString();
        const lines = buf.split('\n');
        buf = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const msg = JSON.parse(line.slice(6));
            if (msg.type !== 'update') continue;
            if (msg.project_name !== projectName) continue;

            const stats = msg.data?.stats || {};
            const ctxTokens = stats.session_ctx_tokens || 0;
            const sessIn = stats.session_input_tokens || 0;
            const sessOut = stats.session_output_tokens || 0;
            const model = stats.model || '?';
            const elapsed = ((Date.now() - start) / 1000).toFixed(1);

            const entry = { elapsed, ctxTokens, sessIn, sessOut, model };
            seen.push(entry);

            const prev = seen[seen.length - 2];
            const changed = prev && prev.ctxTokens !== ctxTokens ? ' ← CHANGED' : '';
            console.log(
              `[SSE +${elapsed}s] ctx_tokens=${ctxTokens} | sess_in=${sessIn} | sess_out=${sessOut} | model=${model}${changed}`
            );
          } catch {
            // ignore parse errors
          }
        }
      });
    });

    req.on('error', (err) => {
      console.error('[SSE] Erro ao ligar:', err.message);
      resolve(seen);
    });

    setTimeout(() => {
      req.destroy();
      resolve(seen);
    }, durationMs);
  });
}

// ── 2. Playwright DOM watcher ─────────────────────────────────────────────────
async function watchDOM(projectName, durationMs) {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage();

  console.log(`\n[DOM] A abrir ${MONITOR_URL} ...`);
  await page.goto(MONITOR_URL);
  await page.waitForTimeout(2000);

  const observations = [];
  const start = Date.now();
  const interval = 2000; // check every 2s

  console.log(`[DOM] A observar o DOM a cada ${interval / 1000}s...\n`);

  while (Date.now() - start < durationMs) {
    // Find the context % badge for the target project
    const pct = await page.evaluate((proj) => {
      // Look for a dash-project element containing the project name
      const cards = document.querySelectorAll('.dash-project');
      for (const card of cards) {
        const nameEl = card.querySelector('.dash-project-name');
        if (!nameEl || !nameEl.textContent.includes(proj)) continue;
        const pctEl = card.querySelector('.dash-ctx-pct');
        return pctEl ? pctEl.textContent.trim() : null;
      }
      return null;
    }, projectName);

    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    const prev = observations[observations.length - 1];
    const changed = prev && prev.pct !== pct ? ' ← CHANGED' : '';
    console.log(`[DOM +${elapsed}s] ctx_pct="${pct}"${changed}`);
    observations.push({ elapsed, pct });

    await page.waitForTimeout(interval);
  }

  // Summary
  const unique = [...new Set(observations.map((o) => o.pct))];
  console.log(`\n[DOM] Valores únicos observados: ${JSON.stringify(unique)}`);
  if (unique.length > 1) {
    console.log('[DOM] ✅ O % ACTUALIZOU durante o período de observação');
  } else {
    console.log('[DOM] ❌ O % NÃO actualizou — ficou sempre em:', unique[0]);
  }

  await browser.close();
  return observations;
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  const durationMs = DURATION_S * 1000;

  console.log('═'.repeat(60));
  console.log(`Projecto: ${PROJECT}`);
  console.log(`Duração:  ${DURATION_S}s`);
  console.log(`Monitor:  ${MONITOR_URL}`);
  console.log('═'.repeat(60));

  // Run SSE and DOM watchers in parallel
  const [sseEvents, domObs] = await Promise.all([
    watchSSE(PROJECT, durationMs),
    watchDOM(PROJECT, durationMs),
  ]);

  // ── SSE Summary ──────────────────────────────────────────────────────────
  console.log('\n' + '─'.repeat(60));
  console.log('RESUMO SSE:');
  const uniqueCtx = [...new Set(sseEvents.map((e) => e.ctxTokens))];
  if (uniqueCtx.length === 0) {
    console.log('  ⚠️  Nenhum evento SSE recebido para este projecto!');
    console.log('  → Verificar se o projecto está activo e o nome está correcto.');
  } else if (uniqueCtx.length === 1) {
    console.log(`  ❌ session_ctx_tokens não mudou: sempre ${uniqueCtx[0]}`);
    console.log('  → O servidor não está a enviar valores actualizados.');
    console.log('  → Verificar _get_project_stats e a lógica de merge em app.py.');
  } else {
    console.log(`  ✅ session_ctx_tokens mudou: ${uniqueCtx.join(' → ')}`);
  }
  console.log(`  Total de eventos SSE para "${PROJECT}": ${sseEvents.length}`);
}

main().catch((err) => {
  console.error('Erro fatal:', err);
  process.exit(1);
});
