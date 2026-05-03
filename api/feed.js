/**
 * LATAMODDS — Vercel Serverless Function
 * Proxy del latamodds_feed.json generado por GitHub Actions cada hora.
 * Aplica traducción EN→ES al vuelo y cachea 15 min en Vercel edge.
 */

const FEED_URL = 'https://raw.githubusercontent.com/jarce99/Latamodds/main/latamodds_feed.json';

async function get(url, ms = 10000) {
  try {
    const r = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(ms),
    });
    return r.ok ? r.json() : null;
  } catch { return null; }
}

// ─── Batch translate EN→ES via unofficial Google endpoint ────────────────────

async function translateES(texts) {
  if (!texts.length) return texts;
  const SIZE = 40;
  const chunks = [];
  for (let i = 0; i < texts.length; i += SIZE) chunks.push(texts.slice(i, i + SIZE));

  const results = await Promise.all(chunks.map(async chunk => {
    const q = chunk.join('\n');
    const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=es&dt=t&q=${encodeURIComponent(q)}`;
    const data = await get(url, 8000);
    if (!data?.[0]) return chunk;
    const full = data[0].map(t => t[0]).join('');
    const parts = full.split('\n');
    return parts.length === chunk.length ? parts : chunk;
  }));

  return results.flat();
}

// ─── Handler ──────────────────────────────────────────────────────────────────

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=900, stale-while-revalidate=3600');

  const data = await get(FEED_URL, 15000);
  if (!data) {
    return res.status(503).json({ error: 'Feed no disponible — GitHub Actions aún no ha corrido' });
  }

  const eventos = data.eventos_latam || [];

  // Translate titles and event names in parallel (dedup event_names to save requests)
  try {
    const titulos   = eventos.map(e => e.titulo || '');
    const uniqueEN  = [...new Set(eventos.map(e => e.event_name).filter(Boolean))];

    const [titES, enES] = await Promise.all([
      translateES(titulos),
      translateES(uniqueEN),
    ]);

    const enMap = Object.fromEntries(uniqueEN.map((k, i) => [k, enES[i] || k]));
    eventos.forEach((e, i) => {
      e.titulo     = titES[i]          || e.titulo;
      e.event_name = enMap[e.event_name] || e.event_name;
    });
  } catch {}

  res.json({
    ...data,
    eventos_latam: eventos,
  });
};
