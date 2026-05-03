"""
LATAMODDS — Feed de Prediction Markets para LATAM
Fuentes: Prediction Hunt API v2 + Kalshi Trade API v2
Pipeline: /v2/events → /v2/prices/bulk → feed completo
"""

import os
import re
import requests
import json
import time
from datetime import datetime, timezone, date, timedelta

API_KEY  = "pmx_aVcVfXStTHiqRqHFsnokE7Cfu9Cv3REuikivcRF7dP8"
BASE_URL = "https://www.predictionhunt.com/api/v2"
HEADERS  = {"Accept": "application/json", "X-API-Key": API_KEY}

# Queries para matching-markets
LATAM_KEYWORDS = [
    "liga mx", "copa america", "world cup", "champions league",
    "la liga", "premier league", "nba", "nfl", "ufc", "baseball",
    "mexico", "argentina", "brazil",
    "stanley cup", "nhl", "bundesliga", "serie a", "mls",
    "mlb", "heisman", "europa league", "fa cup",
    "copa libertadores", "concacaf",
    "nba champion", "nba finals", "super bowl",
    "world cup group", "fifa group",
]

# ─── FETCH EVENTS ─────────────────────────────────────────────────────────────

def fetch_all_events(event_type="sports", limit_total=500):
    """Jala todos los eventos deportivos activos usando paginación."""
    all_events = []
    cursor = None
    page = 1

    while len(all_events) < limit_total:
        params = {
            "event_type": event_type,
            "status": "active",
            "limit": 50,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(
                f"{BASE_URL}/events",
                headers=HEADERS,
                params=params,
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            print(f"  [ERROR] /events página {page}: {e}")
            break

        events = data.get("events", [])
        if not events:
            break

        all_events.extend(events)
        print(f"  Página {page}: {len(events)} eventos ({len(all_events)} total)")

        cursor = data.get("next_cursor")
        if not cursor:
            break

        page += 1
        time.sleep(0.5)

    return all_events


def parse_events(events):
    """Convierte eventos crudos al schema LATAMODDS."""
    results = []
    for event in events:
        event_name = event.get("event_name", "Sin nombre")
        event_date = event.get("event_date", "—")
        event_type = event.get("event_type", "—")
        event_id   = event.get("id")
        status     = event.get("status", "—")

        for group in event.get("groups", []):
            platforms = group.get("platforms", [])
            gid = group.get("group_id")

            # Extraer market IDs directamente si la API los incluye
            market_id_polymarket = None
            market_id_kalshi = None
            url_polymarket = ""
            for m in group.get("markets", []):
                src = m.get("source", "")
                mid = m.get("id", "")
                url = m.get("source_url", "")
                if src == "polymarket" and not market_id_polymarket:
                    market_id_polymarket = f"polymarket:{mid}"
                    url_polymarket = url
                elif src == "kalshi" and not market_id_kalshi:
                    market_id_kalshi = f"kalshi:{mid}"

            results.append({
                "group_id":     gid,
                "event_id":     event_id,
                "event_name":   event_name,
                "titulo":       group.get("title", "—"),
                "tipo":         event_type,
                "fecha_evento": event_date,
                "status":       status,
                "fuentes":      platforms,
                "platform_count": group.get("platform_count", 0),
                "probabilidad": None,
                "yes_bid":      None,
                "yes_ask":      None,
                "market_id_polymarket": market_id_polymarket,
                "market_id_kalshi":     market_id_kalshi,
                "url_polymarket": url_polymarket,
                "ingesta_ts":   datetime.now(timezone.utc).isoformat(),
            })

    return results


# ─── ENRIQUECER CON URLs Y MARKET IDs ────────────────────────────────────────

def enrich_with_matching(groups_list):
    """
    Para cada query LATAM, jala matching-markets y mapea
    group_id → market_ids (polymarket:xxx, kalshi:xxx) y URLs.
    """
    # Índice por group_id
    idx = {g["group_id"]: i for i, g in enumerate(groups_list) if g["group_id"]}

    print(f"\n[2/4] Enriqueciendo con URLs y market IDs ({len(LATAM_KEYWORDS)} queries)...")

    for q in LATAM_KEYWORDS:
        try:
            resp = requests.get(
                f"{BASE_URL}/matching-markets",
                headers=HEADERS,
                params={"q": q},
                timeout=15,
            )
            events = resp.json().get("events", [])
            for event in events:
                for group in event.get("groups", []):
                    gid = group.get("group_id")
                    if gid in idx:
                        i = idx[gid]
                        for m in group.get("markets", []):
                            src = m.get("source", "")
                            mid = m.get("id", "")
                            url = m.get("source_url", "")
                            if src == "polymarket" and not groups_list[i].get("market_id_polymarket"):
                                groups_list[i]["market_id_polymarket"] = f"polymarket:{mid}"
                                groups_list[i]["url_polymarket"] = url
                            elif src == "kalshi" and not groups_list[i].get("market_id_kalshi"):
                                groups_list[i]["market_id_kalshi"] = f"kalshi:{mid}"
            time.sleep(1)
        except Exception as e:
            print(f"  [ERROR] matching '{q}': {e}")

    # Segundo paso: buscar por event_name los grupos que siguen sin URL
    sin_url = [g for g in groups_list if not g.get("market_id_polymarket") and not g.get("market_id_kalshi")]
    if sin_url:
        # Agrupar por event_name único para hacer una query por evento
        event_names = list({g["event_name"] for g in sin_url if g.get("event_name") and g["event_name"] != "Sin nombre"})
        print(f"  Buscando {len(event_names)} event_names sin market_id...")
        for name in event_names[:30]:  # máx 30 queries extra
            try:
                resp = requests.get(
                    f"{BASE_URL}/matching-markets",
                    headers=HEADERS,
                    params={"q": name},
                    timeout=15,
                )
                events = resp.json().get("events", [])
                for event in events:
                    for group in event.get("groups", []):
                        gid = group.get("group_id")
                        if gid in idx:
                            i = idx[gid]
                            for m in group.get("markets", []):
                                src = m.get("source", "")
                                mid = m.get("id", "")
                                url = m.get("source_url", "")
                                if src == "polymarket" and not groups_list[i].get("market_id_polymarket"):
                                    groups_list[i]["market_id_polymarket"] = f"polymarket:{mid}"
                                    groups_list[i]["url_polymarket"] = url
                                elif src == "kalshi" and not groups_list[i].get("market_id_kalshi"):
                                    groups_list[i]["market_id_kalshi"] = f"kalshi:{mid}"
                time.sleep(0.5)
            except Exception as e:
                print(f"  [ERROR] event_name '{name}': {e}")

    return groups_list


# ─── FETCH PRICES ─────────────────────────────────────────────────────────────

def fetch_prices(groups_list):
    """Jala precios para todos los grupos que tienen market_id."""
    # Recolectar IDs únicos
    id_to_group = {}
    for i, g in enumerate(groups_list):
        mid = g.get("market_id_polymarket") or g.get("market_id_kalshi")
        if mid:
            id_to_group[mid] = i

    all_ids = list(id_to_group.keys())
    print(f"\n[3/4] Jalando precios para {len(all_ids)} mercados...")

    prices = {}
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i:i+100]
        try:
            resp = requests.get(
                f"{BASE_URL}/prices/bulk",
                headers=HEADERS,
                params={"ids": ",".join(chunk)},
                timeout=15,
            )
            if resp.status_code == 200:
                prices.update(resp.json().get("prices", {}))
            time.sleep(0.5)
        except Exception as e:
            print(f"  [ERROR] prices chunk {i}: {e}")

    # Asignar precios
    for mid, price_data in prices.items():
        if mid in id_to_group:
            i = id_to_group[mid]
            p = price_data.get("last_price") or price_data.get("yes_bid")
            groups_list[i]["probabilidad"] = round(float(p), 1) if p is not None else None
            groups_list[i]["yes_bid"]  = price_data.get("yes_bid")
            groups_list[i]["yes_ask"]  = price_data.get("yes_ask")
            groups_list[i]["precio_ts"] = price_data.get("timestamp")

    con_precio = sum(1 for g in groups_list if g.get("probabilidad") is not None)
    print(f"  {con_precio} grupos con precio")
    return groups_list


# ─── FILTRAR Y ORDENAR ────────────────────────────────────────────────────────

def filter_and_sort(groups_list):
    hoy = date.today().isoformat()

    def es_valido(g):
        prob = g.get("probabilidad")
        if prob is None:
            return True  # Mantener aunque no tenga precio (se filtra en frontend)
        # Descartar resueltos o sin dato real
        if prob >= 95 or prob <= 5:
            return False
        if prob == 50:
            return False  # default / sin precio real
        # Descartar fechas pasadas
        fecha = g.get("fecha_evento", "")
        if fecha and fecha != "—" and fecha < hoy:
            return False
        return True

    validos = [g for g in groups_list if es_valido(g)]

    # Ordenar: con precio primero (por cercanía a 50%), sin precio al fondo
    con_p  = [g for g in validos if g.get("probabilidad") is not None]
    sin_p  = [g for g in validos if g.get("probabilidad") is None]
    con_p.sort(key=lambda x: abs(x["probabilidad"] - 50))

    return con_p + sin_p


# ─── KALSHI API ───────────────────────────────────────────────────────────────

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_HDRS = {"Accept": "application/json"}

# Solo series de resultado de partido (game-winner), nada de props individuales
KALSHI_GAME_SERIES = {
    "NBA":     "KXNBAGAME",
    "UCL":     "KXUCLGAME",
    "EPL":     "KXEPLGAME",
    "LaLiga":  "KXLALIGAGAME",
    "Liga MX": "KXLIGAMXGAME",
    "MLB":     "KXMLBGAME",
    "MLS":     "KXMLSGAME",
}

_MONTH_MAP = {
    "JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
    "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12,
}

def _kalshi_ticker_date(ticker):
    """Extrae fecha de ticker tipo KXNBAGAME-26MAY05LALOKC → 2026-05-05"""
    m = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not m:
        return None
    yr, mon_str, day = m.group(1), m.group(2), m.group(3)
    mo = _MONTH_MAP.get(mon_str)
    return f"20{yr}-{mo:02d}-{int(day):02d}" if mo else None

def _kalshi_events_for_series(series_ticker, hoy, cutoff):
    """Eventos abiertos de una serie filtrados al rango de fechas."""
    try:
        resp = requests.get(
            f"{KALSHI_BASE}/events",
            headers=KALSHI_HDRS,
            params={"status": "open", "series_ticker": series_ticker, "limit": 100},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        result = []
        for e in resp.json().get("events", []):
            fecha = _kalshi_ticker_date(e.get("event_ticker", ""))
            if fecha and hoy.isoformat() <= fecha <= cutoff.isoformat():
                e["_fecha"] = fecha
                result.append(e)
        return result
    except Exception:
        return []

def _kalshi_event_detail(event_ticker):
    try:
        resp = requests.get(
            f"{KALSHI_BASE}/events/{event_ticker}",
            headers=KALSHI_HDRS,
            timeout=15,
        )
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None

def _kalshi_yes_price(market):
    """Retorna precio YES como float 0-100 o None."""
    raw = market.get("last_price_dollars") or "0"
    try:
        p = float(raw)
    except ValueError:
        p = 0.0
    if p == 0.0:
        no_ask = float(market.get("no_ask_dollars") or "1")
        p = max(0.01, min(0.99, 1.0 - no_ask))
    return round(p * 100, 1)

def fetch_kalshi_partidos():
    """Jala partidos (game-winner) de Kalshi para la semana."""
    print("\n[Kalshi] Jalando partidos de la semana...")
    hoy    = date.today()
    cutoff = hoy + timedelta(days=7)

    # 1. Recolectar todos los eventos por serie
    all_events = []
    for sport, series in KALSHI_GAME_SERIES.items():
        events = _kalshi_events_for_series(series, hoy, cutoff)
        for e in events:
            e["_sport"] = sport
        if events:
            print(f"  {sport} ({series}): {len(events)} partidos")
        all_events.extend(events)
        time.sleep(0.3)

    print(f"  → {len(all_events)} partidos totales")

    # 2. Fetch mercados y construir estructura equipo-vs-equipo
    partidos = []
    for event in all_events:
        ticker = event.get("event_ticker", "")
        detail = _kalshi_event_detail(ticker)
        if not detail:
            continue

        ev      = detail.get("event", {})
        markets = detail.get("markets", [])
        if not markets:
            continue

        equipos = []
        empate  = None
        for m in markets:
            name = m.get("yes_sub_title") or ""
            if not name:
                continue
            precio = _kalshi_yes_price(m)
            if name.lower() in ("tie", "draw", "empate"):
                empate = precio
            else:
                equipos.append({
                    "nombre": name,
                    "precio": precio,
                    "ticker": m.get("ticker", ""),
                })

        if len(equipos) < 2:
            continue

        partidos.append({
            "sport":        event["_sport"],
            "titulo":       ev.get("title") or event.get("title", ""),
            "subtitulo":    ev.get("sub_title") or event.get("sub_title", ""),
            "fecha":        event["_fecha"],
            "equipos":      equipos,
            "empate":       empate,
            "fuente":       "kalshi",
            "event_ticker": ticker,
            "url":          f"https://kalshi.com/markets/{ticker}",
        })

        time.sleep(0.2)

    print(f"  → {len(partidos)} partidos Kalshi con mercados")
    return partidos


# ─── FETCH PARTIDOS DE LA SEMANA ─────────────────────────────────────────────

SPORTS_WEEK = ["epl","ucl","laliga","ligamx","nba","mlb","nfl","bundesliga","seriea"]
SPORT_LABELS = {
    "epl":"Premier League","ucl":"Champions League","laliga":"La Liga",
    "ligamx":"Liga MX","nba":"NBA","mlb":"MLB","nfl":"NFL",
    "bundesliga":"Bundesliga","seriea":"Serie A"
}

def fetch_partidos_semana():
    partidos = []
    hoy = date.today()

    for i in range(7):
        dia = hoy + timedelta(days=i)
        for sport in SPORTS_WEEK:
            try:
                resp = requests.get(
                    f"{BASE_URL}/matching-markets/sports",
                    headers=HEADERS,
                    params={"sport": sport, "date": dia.isoformat()},
                    timeout=10,
                )
                games = resp.json().get("games", [])
                if not games:
                    continue

                # Agrupar por partido (3 outcomes: equipo1, empate, equipo2)
                matches = {}
                for g in games:
                    title = g["game_title"]
                    is_draw = "Draw" in title or "draw" in title
                    nombre = title.replace(str(dia), "").strip()
                    precio = None
                    url = ""
                    for m in g.get("markets", []):
                        p = m.get("last_price") or m.get("yes_bid")
                        if p:
                            precio = round(float(p), 1)
                            url = m.get("source_url", "")
                            break

                    # Usar source_url como key de partido
                    base_url = url.split("?")[0].rsplit("-", 1)[0] if url else f"{sport}_{dia}_{i}"
                    if base_url not in matches:
                        matches[base_url] = {
                            "sport": SPORT_LABELS.get(sport, sport),
                            "sport_key": sport,
                            "fecha": str(dia),
                            "equipos": [],
                            "empate": None,
                            "url": url,
                        }
                    if is_draw:
                        matches[base_url]["empate"] = precio
                    else:
                        matches[base_url]["equipos"].append({
                            "nombre": nombre,
                            "precio": precio,
                            "url": url,
                        })

                for m in matches.values():
                    if len(m["equipos"]) >= 2:
                        partidos.append(m)

                time.sleep(0.3)
            except Exception as e:
                pass

    return partidos

# ─── SUPABASE ─────────────────────────────────────────────────────────────────

def save_to_supabase(eventos):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("  [Supabase] No configurado, saltando.")
        return

    headers = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }

    rows = [{
        "group_id":       e.get("group_id"),
        "event_id":       str(e.get("event_id", "")),
        "event_name":     e.get("event_name"),
        "titulo":         e.get("titulo"),
        "tipo":           e.get("tipo"),
        "fecha_evento":   e.get("fecha_evento"),
        "status":         e.get("status"),
        "fuentes":        json.dumps(e.get("fuentes", [])),
        "platform_count": e.get("platform_count"),
        "probabilidad":   e.get("probabilidad"),
        "url_polymarket": e.get("url_polymarket", ""),
        "url_kalshi":     e.get("url_kalshi", ""),
        "ingesta_ts":     e.get("ingesta_ts"),
    } for e in eventos]

    total = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        resp = requests.post(
            f"{url}/rest/v1/odds_eventos",
            headers=headers,
            json=batch,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            total += len(batch)
        else:
            print(f"  [Supabase] Error {resp.status_code}: {resp.text[:200]}")

    print(f"  [Supabase] {total} eventos guardados.")


# ─── PIPELINE ─────────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("  LATAMODDS — Feed Completo (Dev)")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Fetch todos los eventos deportivos activos
    print(f"\n[1/4] Jalando eventos deportivos activos...")
    raw_events = fetch_all_events(event_type="sports", limit_total=500)
    print(f"  → {len(raw_events)} eventos")

    groups = parse_events(raw_events)
    print(f"  → {len(groups)} grupos/mercados")

    # 2. Enriquecer con URLs y market IDs
    groups = enrich_with_matching(groups)
    con_url = sum(1 for g in groups if g.get("url_polymarket"))
    print(f"  → {con_url} grupos con URL")

    # 3. Precios
    groups = fetch_prices(groups)

    # 4. Filtrar y ordenar
    print(f"\n[4/4] Filtrando y ordenando...")
    groups = filter_and_sort(groups)
    con_precio = sum(1 for g in groups if g.get("probabilidad") is not None)
    print(f"  → {len(groups)} mercados válidos, {con_precio} con precio")

    # ── Display top 25 ────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  TOP MERCADOS LATAM")
    print(f"{'─'*60}")

    for i, g in enumerate(groups[:25], 1):
        prob_str = f"{g['probabilidad']}%" if g.get("probabilidad") else "N/D"
        fuentes  = " + ".join(g["fuentes"]).upper()
        print(f"\n#{i}  {g['titulo']}  —  {g['event_name']}")
        print(f"     Prob: {prob_str}  |  Fecha: {g['fecha_evento']}")
        print(f"     Fuentes: {fuentes}")
        if g.get("url_polymarket"):
            print(f"     → {g['url_polymarket']}")

    # ── Guardar ───────────────────────────────────────────────────────────────
    # Partidos Prediction Hunt
    print(f"\n[+] Jalando partidos de la semana (Prediction Hunt)...")
    partidos = fetch_partidos_semana()
    print(f"    → {len(partidos)} partidos encontrados")

    # Partidos Kalshi
    partidos_kalshi = fetch_kalshi_partidos()

    output = {
        "generado":        datetime.now(timezone.utc).isoformat(),
        "total_eventos":   len(groups),
        "con_precio":      con_precio,
        "eventos_latam":   groups,
        "mercados":        [],
        "partidos":        partidos,
        "partidos_kalshi": partidos_kalshi,
    }
    with open("latamodds_feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  [✓] latamodds_feed.json guardado")
    print(f"      {len(groups)} mercados  |  {con_precio} con precio")
    print(f"{'='*60}\n")

    # Guardar en Supabase para histórico
    print("[Supabase] Guardando snapshot...")
    save_to_supabase(groups)


if __name__ == "__main__":
    import sys
    if "--debug" in sys.argv:
        idx = sys.argv.index("--debug")
        endpoint = sys.argv[idx+1] if idx+1 < len(sys.argv) else "events"
        if endpoint == "events":
            resp = requests.get(f"{BASE_URL}/events", headers=HEADERS,
                params={"event_type":"sports","status":"active","limit":2}, timeout=15)
        else:
            resp = requests.get(f"{BASE_URL}/matching-markets", headers=HEADERS,
                params={"q": endpoint}, timeout=15)
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False)[:4000])
    else:
        run()
