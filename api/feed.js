/**
 * LATAMODDS — Vercel Serverless Function
 * Kalshi game series + Polymarket sports futures
 * Cached 15 min on Vercel edge.
 */

const KALSHI = 'https://api.elections.kalshi.com/trade-api/v2';
const POLY   = 'https://gamma-api.polymarket.com';

async function get(url, ms = 10000) {
  try {
    const r = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(ms),
    });
    return r.ok ? r.json() : null;
  } catch { return null; }
}

function day(n = 0) {
  return new Date(Date.now() + n * 86400000).toISOString().slice(0, 10);
}
function daysUntil(s) {
  if (!s || s === '—') return 999;
  return (new Date(s + 'T12:00:00') - Date.now()) / 86400000;
}

const LATAM_SPORTS = ['liga mx','ucl','champions','laliga','la liga','nba','copa america','world cup','libertadores','nfl','super bowl','mlb'];
function relevance(item) {
  const d = daysUntil(item.fecha_evento || item.fecha || '');
  if (d < -1) return 9999;
  const urg = d < 1 ? 0 : d < 3 ? 8 : d < 7 ? 16 : 24;
  const unc = item.probabilidad != null ? Math.abs(item.probabilidad - 50) : 35;
  const srcs = (item.fuentes || []).length;
  const big = LATAM_SPORTS.some(l => (item.event_name || item.sport || '').toLowerCase().includes(l)) ? -10 : 0;
  return urg + unc - srcs * 5 + big;
}

// ─── KALSHI game series ───────────────────────────────────────────────────────

const GAME_SERIES = [
  { sport:'NBA',        s:'KXNBAGAME'    },
  { sport:'UCL',        s:'KXUCLGAME'    },
  { sport:'EPL',        s:'KXEPLGAME'    },
  { sport:'LaLiga',     s:'KXLALIGAGAME' },
  { sport:'Liga MX',    s:'KXLIGAMXGAME' },
  { sport:'MLB',        s:'KXMLBGAME'    },
  { sport:'MLS',        s:'KXMLSGAME'    },
  { sport:'NHL',        s:'KXNHLGAME'    },
  { sport:'Bundesliga', s:'KXBUNGAME'    },
];

const MON = {JAN:1,FEB:2,MAR:3,APR:4,MAY:5,JUN:6,JUL:7,AUG:8,SEP:9,OCT:10,NOV:11,DEC:12};
function kDate(t) {
  const m = t.match(/-(\d{2})([A-Z]{3})(\d{2})/);
  if (!m) return null;
  const mo = MON[m[2]];
  return mo ? `20${m[1]}-${String(mo).padStart(2,'0')}-${String(+m[3]).padStart(2,'0')}` : null;
}
function kPrice(m) {
  const p = parseFloat(m.last_price_dollars || 0);
  if (p > 0) return Math.round(p * 1000) / 10;
  const na = parseFloat(m.no_ask_dollars || 1);
  return Math.round(Math.max(1, Math.min(99, (1 - na) * 100)) * 10) / 10;
}

async function kalshiGames() {
  const hoy = day(0), cut = day(14);

  const nested = await Promise.all(
    [...new Map(GAME_SERIES.map(x => [x.s, x])).values()].map(({ sport, s }) =>
      get(`${KALSHI}/events?status=open&series_ticker=${s}&limit=200`)
        .then(d => (d?.events || [])
          .map(e => ({ ...e, _sport: sport, _d: kDate(e.event_ticker || '') }))
          .filter(e => e._d && e._d >= hoy && e._d <= cut)
        )
    )
  );
  const events = nested.flat();
  if (!events.length) return [];

  const details = await Promise.all(
    events.map(e => get(`${KALSHI}/events/${e.event_ticker}`))
  );

  const out = [];
  details.forEach((d, j) => {
    if (!d) return;
    const ev = d.event || {}, mkts = d.markets || [];
    if (!mkts.length) return;
    const equipos = [];
    let empate = null;
    for (const m of mkts) {
      const n = m.yes_sub_title || '';
      if (!n) continue;
      const p = kPrice(m);
      if (['tie','draw','empate'].includes(n.toLowerCase())) empate = p;
      else equipos.push({ nombre: n, precio: p, ticker: m.ticker || '' });
    }
    if (equipos.length < 2) return;
    out.push({
      sport:        events[j]._sport,
      titulo:       ev.title || events[j].title || '',
      subtitulo:    ev.sub_title || '',
      fecha:        events[j]._d,
      equipos, empate,
      fuente:       'kalshi',
      event_ticker: events[j].event_ticker,
      url:          `https://kalshi.com/markets/${events[j].event_ticker}`,
    });
  });
  return out;
}

// ─── POLYMARKET futures — by sport tag ───────────────────────────────────────

const POLY_TAGS = ['soccer', 'basketball', 'baseball', 'football'];

const MUST_HAVE = [
  'nba','nfl','mlb','nhl','ucl','epl','liga','copa','cup','champion',
  'basketball','baseball','football','hockey','tennis','golf',
  'boxing','ufc','mma','wimbledon','masters','open','series',
  'super bowl','world series','mvp','premier','bundesliga',
  'libertadores','serie a','world cup','la liga','laliga',
];
const MUST_NOT = [
  'trump','biden','election','congress','senate','ukraine','russia',
  'ceasefire','gta','marvel','oscar','grammy','emmy','crypto','bitcoin',
  'ethereum','arrest','indicted','impeach','nuclear',
];

function isSportsMarket(q) {
  const ql = (q || '').toLowerCase();
  if (MUST_NOT.some(t => ql.includes(t))) return false;
  return MUST_HAVE.some(t => ql.includes(t));
}

async function polyFutures() {
  const today = day(0);
  const results = await Promise.all(
    POLY_TAGS.map(tag =>
      get(`${POLY}/events?limit=50&tag_slug=${tag}&active=true&closed=false`)
    )
  );

  const seen = new Set();
  const out  = [];

  for (const events of results) {
    if (!Array.isArray(events)) continue;
    for (const ev of events) {
      if (!ev?.markets?.length) continue;
      const endDate = (ev.endDate || '').slice(0, 10);
      if (endDate && endDate < today) continue;

      for (const m of ev.markets) {
        if (m.closed || !m.active || m.archived || !m.outcomePrices) continue;
        const mEnd = (m.endDate || '').slice(0, 10);
        if (mEnd && mEnd <= today) continue;
        if (seen.has(m.id)) continue;
        if (!isSportsMarket(m.question)) continue;

        let prob = null;
        try {
          const px = JSON.parse(m.outcomePrices);
          if (px[0]) prob = Math.round(parseFloat(px[0]) * 1000) / 10;
        } catch {}
        if (prob == null || prob <= 1 || prob >= 99) continue;

        seen.add(m.id);
        const slug = m.slug || '';
        out.push({
          group_id:             `poly-${m.id}`,
          event_id:             m.id,
          event_name:           ev.title || m.question || '—',
          titulo:               m.question || '—',
          tipo:                 'sports',
          fecha_evento:         (m.endDate || ev.endDate || '—').slice(0, 10),
          status:               'active',
          fuentes:              ['polymarket'],
          platform_count:       1,
          probabilidad:         prob,
          url_polymarket:       slug ? `https://polymarket.com/event/${slug}` : '',
          ingesta_ts:           new Date().toISOString(),
        });
      }
    }
  }
  return out;
}

// ─── Batch translate EN→ES via unofficial Google endpoint ────────────────────

async function translateES(texts) {
  if (!texts.length) return texts;
  const SIZE = 25;
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

// ─── Merge & sort ─────────────────────────────────────────────────────────────

const SPORT_ORDER = ['Liga MX','UCL','LaLiga','EPL','NBA','MLB','MLS','NHL','NFL','Bundesliga','Serie A','Copa America','Copa Libertadores'];

function mergeGames(arr) {
  const seen = new Set();
  const norm = s => s.toLowerCase().replace(/[^a-z0-9]/g,'').slice(0,28);
  return arr
    .filter(g => {
      if (!g.equipos?.length || g.equipos.length < 2) return false;
      const k = `${g.sport}|${g.fecha}|${norm(g.titulo)}`;
      if (seen.has(k)) return false; seen.add(k); return true;
    })
    .sort((a, b) => {
      const dc = (a.fecha||'').localeCompare(b.fecha||'');
      if (dc) return dc;
      const ai = SPORT_ORDER.indexOf(a.sport), bi = SPORT_ORDER.indexOf(b.sport);
      return (ai<0?99:ai)-(bi<0?99:bi);
    });
}

function mergeFutures(polyFut) {
  const norm = s => s.toLowerCase().replace(/[^a-z0-9]/g,'').slice(0,36);
  const seen = new Set();
  return polyFut
    .filter(f => {
      const k = norm(f.titulo);
      if (seen.has(k)) return false; seen.add(k); return true;
    })
    .sort((a, b) => relevance(a) - relevance(b))
    .slice(0, 150);
}

// ─── Handler ──────────────────────────────────────────────────────────────────

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Cache-Control', 's-maxage=900, stale-while-revalidate=3600');

  const [rKG, rPoly] = await Promise.allSettled([
    kalshiGames(),
    polyFutures(),
  ]);

  const kGames = rKG.status    === 'fulfilled' ? rKG.value    : [];
  const polyF  = rPoly.status  === 'fulfilled' ? rPoly.value  : [];

  const partidos_kalshi = mergeGames(kGames);
  const eventos_latam   = mergeFutures(polyF);

  // Translate all market titles and event names to Spanish
  try {
    const titulos    = eventos_latam.map(e => e.titulo);
    const uniqueEN   = [...new Set(eventos_latam.map(e => e.event_name).filter(Boolean))];
    const [titES, enES] = await Promise.all([
      translateES(titulos),
      translateES(uniqueEN),
    ]);
    const enMap = Object.fromEntries(uniqueEN.map((k, i) => [k, enES[i] || k]));
    eventos_latam.forEach((e, i) => {
      e.titulo     = titES[i]        || e.titulo;
      e.event_name = enMap[e.event_name] || e.event_name;
    });
  } catch {}

  res.json({
    generado:        new Date().toISOString(),
    total_eventos:   eventos_latam.length,
    con_precio:      eventos_latam.filter(e => e.probabilidad != null).length,
    eventos_latam,
    partidos_kalshi,
    _debug: {
      kalshi_games: kGames.length,
      poly_futures: polyF.length,
    },
  });
};
