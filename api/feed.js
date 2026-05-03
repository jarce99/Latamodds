/**
 * LATAMODDS — Vercel Serverless Function
 * Proxy del latamodds_feed.json + traducción EN→ES + horarios ESPN
 * Cachea 15 min en Vercel edge.
 */

const FEED_URL = 'https://raw.githubusercontent.com/jarce99/Latamodds/main/latamodds_feed.json';
const ESPN     = 'https://site.api.espn.com/apis/site/v2/sports';

async function get(url, ms = 10000) {
  try {
    const r = await fetch(url, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(ms),
    });
    return r.ok ? r.json() : null;
  } catch { return null; }
}

// ─── Traducción EN→ES ─────────────────────────────────────────────────────────

async function translateES(texts) {
  if (!texts.length) return texts;
  const SIZE = 40;
  const chunks = [];
  for (let i = 0; i < texts.length; i += SIZE) chunks.push(texts.slice(i, i + SIZE));

  const results = await Promise.all(chunks.map(async chunk => {
    const q    = chunk.join('\n');
    const url  = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=es&dt=t&q=${encodeURIComponent(q)}`;
    const data = await get(url, 8000);
    if (!data?.[0]) return chunk;
    const full  = data[0].map(t => t[0]).join('');
    const parts = full.split('\n');
    return parts.length === chunk.length ? parts : chunk;
  }));

  return results.flat();
}

// ─── Horarios ESPN ────────────────────────────────────────────────────────────

const ESPN_LEAGUES = [
  { sport:'basketball', league:'nba',             label:'NBA'        },
  { sport:'soccer',     league:'eng.1',            label:'EPL'        },
  { sport:'soccer',     league:'esp.1',            label:'LaLiga'     },
  { sport:'soccer',     league:'uefa.champions',   label:'UCL'        },
  { sport:'soccer',     league:'mex.1',            label:'Liga MX'    },
  { sport:'soccer',     league:'ger.1',            label:'Bundesliga' },
  { sport:'soccer',     league:'usa.1',            label:'MLS'        },
  { sport:'baseball',   league:'mlb',              label:'MLB'        },
  { sport:'football',   league:'nfl',              label:'NFL'        },
];

function normName(s) {
  return (s || '').toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]/g, ' ').trim()
    .split(' ')[0]; // primera palabra es suficiente para match
}

async function fetchEspnSchedule() {
  const now    = new Date();
  const from   = now.toISOString().slice(0, 10).replace(/-/g, '');
  const to     = new Date(now.getTime() + 14 * 86400000).toISOString().slice(0, 10).replace(/-/g, '');
  const dates  = `${from}-${to}`;

  const responses = await Promise.all(
    ESPN_LEAGUES.map(({ sport, league }) =>
      get(`${ESPN}/${sport}/${league}/scoreboard?dates=${dates}&limit=100`, 8000)
    )
  );

  // key → hora_utc: "YYYY-MM-DD|equipo1norm|equipo2norm" → ISO string
  const schedule = {};
  responses.forEach((d, i) => {
    if (!d?.events) return;
    for (const ev of d.events) {
      const startUtc = ev.date; // "2026-05-05T00:00Z"
      if (!startUtc) continue;
      const fecha = startUtc.slice(0, 10);
      for (const comp of (ev.competitions || [])) {
        const teams = (comp.competitors || [])
          .map(c => normName(c.team?.displayName || c.team?.name || ''))
          .filter(Boolean)
          .sort();
        if (teams.length >= 2) {
          schedule[`${fecha}|${teams[0]}|${teams[1]}`] = startUtc;
        }
      }
    }
  });
  return schedule;
}

function enrichWithTimes(partidos, schedule) {
  for (const p of partidos) {
    const fecha   = p.fecha || '';
    const equipos = p.equipos || [];
    if (!fecha || equipos.length < 2) continue;
    const teams = equipos.map(e => normName(e.nombre)).sort();
    const key   = `${fecha}|${teams[0]}|${teams[1]}`;
    if (schedule[key]) p.hora_utc = schedule[key];
  }
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
  const partidos = [...(data.partidos || []), ...(data.partidos_kalshi || [])];

  // Traducción + horarios ESPN en paralelo
  const [,, schedule] = await Promise.allSettled([
    // Traducir titulos
    (async () => {
      const titulos  = eventos.map(e => e.titulo || '');
      const uniqueEN = [...new Set(eventos.map(e => e.event_name).filter(Boolean))];
      const [titES, enES] = await Promise.all([translateES(titulos), translateES(uniqueEN)]);
      const enMap = Object.fromEntries(uniqueEN.map((k, i) => [k, enES[i] || k]));
      eventos.forEach((e, i) => {
        e.titulo     = titES[i]            || e.titulo;
        e.event_name = enMap[e.event_name] || e.event_name;
      });
    })(),
    Promise.resolve(), // placeholder
    fetchEspnSchedule(),
  ]);

  if (schedule.status === 'fulfilled') {
    enrichWithTimes(data.partidos        || [], schedule.value);
    enrichWithTimes(data.partidos_kalshi || [], schedule.value);
  }

  res.json({ ...data, eventos_latam: eventos });
};
