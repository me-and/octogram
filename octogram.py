#!/usr/bin/env python3
"""
octogram.py — Octopus Agile price checker & Telegram notifier.

Fetches upcoming half-hour electricity unit rates for your Agile tariff,
then sends a Telegram message listing any slots where the price is at or
below a configured threshold (default: 0p/kWh, i.e. free or negative).

Usage:
    python3 octogram.py [--dry-run] [--config /path/to/octogram.conf]

Config is read from (first match wins):
    1. Path given via --config (fails if the file does not exist)
    2. $XDG_CONFIG_HOME/octogram.conf or $XDG_CONFIG_HOME/octogram/octogram.conf
       (XDG_CONFIG_HOME defaults to ~/.config)
    3. For each dir in $XDG_CONFIG_DIRS: $dir/octogram.conf or $dir/octogram/octogram.conf
"""

import argparse
import configparser
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OCTOPUS_API_BASE = "https://api.octopus.energy/v1"
OCTOPUS_GRAPHQL_MAIN_URL = "https://api.octopus.energy/v1/graphql/"
OCTOPUS_GRAPHQL_BACKEND_URL = "https://api.backend.octopus.energy/v1/graphql/"
TELEGRAM_API_BASE = "https://api.telegram.org"

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def find_config(explicit: str | None) -> Path:
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {explicit}")
        return p

    xdg_config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", "") or (Path.home() / ".config")
    )
    xdg_config_dirs_raw = os.environ.get("XDG_CONFIG_DIRS", "")
    xdg_config_dirs = (
        [Path(d) for d in xdg_config_dirs_raw.split(":") if d]
        if xdg_config_dirs_raw
        else []
    )

    def _candidates_for(base: Path):
        yield base / "octogram.conf"
        yield base / "octogram" / "octogram.conf"

    for p in _candidates_for(xdg_config_home):
        if p.is_file():
            return p

    for d in xdg_config_dirs:
        for p in _candidates_for(d):
            if p.is_file():
                return p

    raise FileNotFoundError(
        "Config file not found. Place octogram.conf in $XDG_CONFIG_HOME (default: ~/.config) "
        "or one of the $XDG_CONFIG_DIRS directories, or specify a path with --config."
    )


def load_config(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    required = [
        ("octopus", "api_key"),
        ("octopus", "account_number"),
        ("telegram", "bot_token"),
        ("telegram", "chat_id"),
    ]
    for section, key in required:
        if not cfg.has_option(section, key) or not cfg.get(section, key).strip():
            raise ValueError(f"Missing required config: [{section}] {key}")
    return cfg


# ---------------------------------------------------------------------------
# Cache handling
# ---------------------------------------------------------------------------


def default_cache_file() -> Path:
    xdg_state_home = Path(
        os.environ.get("XDG_STATE_HOME", "") or (Path.home() / ".local" / "state")
    )
    return xdg_state_home / "octogram" / "last_reported.json"


def load_cache(path: Path) -> dict:
    """
    Return the full cache document (as a dict), or an empty dict if there's
    no usable cache (e.g. first run, missing file, or unreadable contents).
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read cache file %s: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        log.warning("Cache file %s did not contain a JSON object; ignoring", path)
        return {}
    return data


def save_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
        f.write("\n")


def get_last_reported(cache: dict) -> datetime | None:
    """
    Return the valid_to timestamp of the latest slot reported on a previous
    run, or None if there's no usable cache entry (e.g. first run).
    """
    last_reported = cache.get("last_reported")
    if not last_reported:
        return None
    try:
        return _parse_dt(last_reported)
    except ValueError as exc:
        log.warning("Could not parse cached last_reported value: %s", exc)
        return None


def get_joined_saving_session_ids(cache: dict) -> set[str]:
    """
    Return the set of saving session event IDs we've previously joined,
    according to the local cache.
    """
    ids = cache.get("joined_saving_sessions", [])
    if not isinstance(ids, list):
        return set()
    return {str(i) for i in ids}


# ---------------------------------------------------------------------------
# Octopus API helpers
# ---------------------------------------------------------------------------


def octopus_get(path: str, api_key: str, params: dict | None = None) -> dict:
    url = f"{OCTOPUS_API_BASE}{path}"
    resp = requests.get(url, auth=(api_key, ""), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_active_tariff_code(api_key: str, account_number: str) -> str:
    """
    Walk the account's electricity meter agreements to find the currently
    active tariff code (e.g. 'E-1R-AGILE-24-10-01-C').
    """
    data = octopus_get(f"/accounts/{account_number}/", api_key)
    now = datetime.now(timezone.utc)

    for prop in data.get("properties", []):
        for emp in prop.get("electricity_meter_points", []):
            for agreement in emp.get("agreements", []):
                valid_from = _parse_dt(agreement.get("valid_from"))
                valid_to = _parse_dt(agreement.get("valid_to"))
                # Active if: valid_from <= now AND (valid_to is None OR valid_to > now)
                if valid_from <= now and valid_to > now:
                    tariff_code = agreement.get("tariff_code", "")
                    if tariff_code:
                        log.info("Active tariff code: %s", tariff_code)
                        return tariff_code

    raise RuntimeError(
        "Could not find an active electricity tariff in your Octopus account. "
        "Verify your account number and that you have an active Agile agreement."
    )


def _parse_dt(value: str) -> datetime:
    # Octopus returns ISO 8601 strings; handle both Z and +00:00
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def tariff_code_to_product_code(tariff_code: str) -> str:
    """
    Extract product code from tariff code.
    Tariff codes look like:  E-1R-AGILE-24-10-01-C
    Product codes look like: AGILE-24-10-01
    The product code is everything between the 3rd and last dash-separated segment.
    """
    # Format: {fuel}-{payment_type}-{product_code}-{region_char}
    # Strip leading fuel+payment prefix (e.g. "E-1R-") and trailing region char (e.g. "-C")
    parts = tariff_code.split("-")
    # parts[0] = fuel (E/G), parts[1] = payment type (1R/2R), parts[-1] = region
    product_parts = parts[2:-1]
    return "-".join(product_parts)


def fetch_unit_rates(
    api_key: str,
    product_code: str,
    tariff_code: str,
    period_from: datetime,
) -> list[dict]:
    """Fetch all published future unit rate slots from period_from onwards, handling pagination."""
    path = f"/products/{product_code}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params = {
        "period_from": period_from.isoformat(),
        "page_size": 100,
    }
    results = []
    while True:
        data = octopus_get(path, api_key, params)
        results.extend(data.get("results", []))
        next_url = data.get("next")
        if not next_url:
            break
        # Extract page param from next URL for subsequent calls
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(next_url).query)
        params = {**params, "page": qs["page"][0]}

    # Sort chronologically
    results.sort(key=lambda r: r.get("valid_from", ""))
    return results


# ---------------------------------------------------------------------------
# Octopus Saving Sessions (GraphQL) helpers
# ---------------------------------------------------------------------------


def graphql_request(url: str, query: str, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = token
    resp = requests.post(url, json={"query": query}, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors:
        raise RuntimeError(f"GraphQL errors: {errors}")
    return data["data"]


def obtain_kraken_token(api_key: str) -> str:
    """Exchange an Octopus API key for a short-lived Kraken JWT token."""
    query = f"""mutation {{
      obtainKrakenToken(input: {{ APIKey: "{api_key}" }}) {{
        token
      }}
    }}"""
    data = graphql_request(OCTOPUS_GRAPHQL_MAIN_URL, query)
    token = data.get("obtainKrakenToken", {}).get("token")
    if not token:
        raise RuntimeError("No token returned from obtainKrakenToken")
    return token


def fetch_saving_sessions(token: str, account_number: str) -> dict:
    """
    Fetch Saving Sessions ("Power Down" challenge) data: all known events plus
    this account's campaign membership and already-joined events.
    """
    query = f"""query {{
      savingSessions {{
        events(includeDev: false) {{
          id
          code
          rewardPerKwhInOctoPoints
          startAt
          endAt
          devEvent
          targetRegion {{
            regionId
          }}
        }}
        account(accountNumber: "{account_number}") {{
          signedUpMeterPoint {{
            regionId
          }}
          hasJoinedCampaign
          joinedEvents {{
            eventId
          }}
        }}
      }}
    }}"""
    data = graphql_request(OCTOPUS_GRAPHQL_BACKEND_URL, query, token=token)
    return data["savingSessions"]


def join_saving_session_event(token: str, account_number: str, event_code: str) -> list[str]:
    """Join a Saving Session event; returns the list of event codes the account is now joined to."""
    query = f"""mutation {{
      joinSavingSessionsEvent(input: {{
        accountNumber: "{account_number}"
        eventCode: "{event_code}"
      }}) {{
        joinedEventCodes
      }}
    }}"""
    data = graphql_request(OCTOPUS_GRAPHQL_BACKEND_URL, query, token=token)
    return data["joinSavingSessionsEvent"]["joinedEventCodes"]


def find_joinable_saving_session_events(
    saving_sessions: dict,
    already_joined_ids: set[str],
) -> list[dict]:
    """
    Filter the events returned by fetch_saving_sessions() down to those that
    are worth joining: not a dev/test event, carrying a positive reward, not
    yet finished, eligible for our region, and not already joined (per either
    the Octopus API's own record or our local cache).
    """
    account = saving_sessions.get("account", {})
    api_joined_ids = {str(e["eventId"]) for e in account.get("joinedEvents", [])}
    joined_ids = api_joined_ids | already_joined_ids

    signed_up_region = (account.get("signedUpMeterPoint") or {}).get("regionId")
    now = datetime.now(timezone.utc)

    joinable = []
    for event in saving_sessions.get("events", []):
        if event.get("devEvent"):
            continue
        if event.get("rewardPerKwhInOctoPoints", 0) <= 0:
            continue
        if _parse_dt(event["endAt"]) <= now:
            continue
        if str(event["id"]) in joined_ids:
            continue
        target_regions = {r["regionId"] for r in event.get("targetRegion", [])}
        if target_regions and signed_up_region not in target_regions:
            continue
        joinable.append(event)

    return joinable


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


def build_message(slots: list[dict]) -> str:
    lines = ["⚡ <b>Octopus Agile: Free/Negative slots found!</b>", ""]
    consecutive_times: list[tuple[datetime, datetime]] = []
    consecutive_from: datetime | None = None
    consecutive_to: datetime | None = None
    for slot in slots:
        price = slot["value_inc_vat"]
        dt_from = _parse_dt(slot["valid_from"]).astimezone()
        dt_to = _parse_dt(slot["valid_to"]).astimezone()
        if consecutive_to == dt_from:
            consecutive_to = dt_to
        else:
            if consecutive_from is not None:
                assert consecutive_to is not None
                consecutive_times.append((consecutive_from, consecutive_to))
            consecutive_from = dt_from
            consecutive_to = dt_to
        lines.append(f"• {dt_from:%a %-d %b %H:%M}-{dt_to:%H:%M} {price:.2f}p/kWh")
    assert consecutive_from is not None
    assert consecutive_to is not None
    consecutive_times.append((consecutive_from, consecutive_to))
    lines.extend(("", "⏰ <b>Consecutive slots:</b>", ""))
    for dt_from, dt_to in consecutive_times:
        lines.append(f"• {dt_from:%H:%M}-{dt_to:%H:%M}")
    lines.append("")
    total_minutes = len(slots) * 30
    lines.append(f"{len(slots)} slot(s) | {total_minutes} minutes total")
    return "\n".join(lines)


def build_saving_session_message(events: list[dict]) -> str:
    lines = ["🔋 <b>Octopus Saving Session: Joined!</b>", ""]
    for event in events:
        dt_from = _parse_dt(event["startAt"]).astimezone()
        dt_to = _parse_dt(event["endAt"]).astimezone()
        reward = event["rewardPerKwhInOctoPoints"]
        lines.append(
            f"• {dt_from:%a %-d %b %H:%M}-{dt_to:%H:%M} — {reward} OctoPoints/kWh"
        )
    lines.append("")
    lines.append(f"Signed up for {len(events)} session(s).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agile price checking
# ---------------------------------------------------------------------------


def check_agile_prices(
    api_key: str,
    account_number: str,
    bot_token: str,
    chat_id: str,
    threshold: float,
    cache: dict,
    dry_run: bool,
) -> int:
    """
    Check for upcoming free/negative Agile price slots and notify via
    Telegram. Updates `cache["last_reported"]` in place on success. Returns 0
    on success (including "nothing to report"), 1 on failure.
    """
    try:
        tariff_code = get_active_tariff_code(api_key, account_number)
    except Exception as exc:
        log.error("Failed to fetch tariff from Octopus API: %s", exc)
        return 1

    if "AGILE" not in tariff_code.upper():
        log.warning(
            "Active tariff '%s' does not appear to be an Agile tariff.", tariff_code
        )

    product_code = tariff_code_to_product_code(tariff_code)
    log.info("Product code: %s", product_code)

    now = datetime.now(timezone.utc)

    try:
        rates = fetch_unit_rates(api_key, product_code, tariff_code, now)
    except Exception as exc:
        log.error("Failed to fetch unit rates: %s", exc)
        return 1

    log.info("Fetched %d rate slot(s)", len(rates))

    # Reject if next-day prices haven't been published yet.
    # Octopus Agile publishes next-day rates each afternoon; the first slot for
    # tomorrow starts at local midnight, so we check that at least one rate
    # falls on or after that boundary.
    tomorrow_local = datetime.now().date() + timedelta(days=1)
    tomorrow_midnight_local = datetime(
        tomorrow_local.year,
        tomorrow_local.month,
        tomorrow_local.day,
        tzinfo=datetime.now(timezone.utc).astimezone().tzinfo,
    )
    if not any(
        _parse_dt(r.get("valid_from", "")) >= tomorrow_midnight_local for r in rates
    ):
        log.error(
            "Next day's prices are not yet available. "
            "Octopus Agile rates for %s have not been published yet.",
            tomorrow_local.strftime("%-d %b %Y"),
        )
        return 1

    qualifying = [r for r in rates if r.get("value_inc_vat", 999) <= threshold]
    log.info("%d slot(s) at or below %.2fp/kWh", len(qualifying), threshold)

    last_reported = get_last_reported(cache)
    if last_reported is not None:
        log.info("Last reported slot ended: %s", last_reported.isoformat())
        qualifying = [
            r for r in qualifying if _parse_dt(r["valid_from"]) >= last_reported
        ]
        log.info("%d slot(s) new since last report", len(qualifying))

    if not qualifying:
        log.info("No new qualifying slots — suppressing notification.")
        return 0

    message = build_message(qualifying)
    newest_valid_to = max(_parse_dt(r["valid_to"]) for r in qualifying)

    if dry_run:
        print(message)
        return 0

    try:
        send_telegram(bot_token, chat_id, message)
        log.info("Telegram message sent successfully.")
    except Exception as exc:
        log.error("Failed to send Telegram message: %s", exc)
        return 1

    cache["last_reported"] = newest_valid_to.isoformat()
    return 0


# ---------------------------------------------------------------------------
# Saving Sessions ("Power Down" challenge) checking
# ---------------------------------------------------------------------------


def check_saving_sessions(
    api_key: str,
    account_number: str,
    bot_token: str,
    chat_id: str,
    cache: dict,
    dry_run: bool,
) -> int:
    """
    Check for available Saving Session events (Octopus's "Power Down"-style
    challenges), join any we're eligible for and haven't already joined, and
    notify via Telegram. Updates `cache["joined_saving_sessions"]` in place
    on success. Returns 0 on success (including "nothing to join"), 1 if
    fetching failed or any join attempt failed.
    """
    try:
        token = obtain_kraken_token(api_key)
        saving_sessions = fetch_saving_sessions(token, account_number)
    except Exception as exc:
        log.error("Failed to fetch Saving Sessions from Octopus API: %s", exc)
        return 1

    if not saving_sessions.get("account", {}).get("hasJoinedCampaign"):
        log.warning(
            "Account has not opted into the Octopus Saving Sessions campaign; "
            "skipping event join check."
        )
        return 0

    already_joined_ids = get_joined_saving_session_ids(cache)
    joinable = find_joinable_saving_session_events(saving_sessions, already_joined_ids)
    log.info("%d Saving Session event(s) available to join", len(joinable))

    if not joinable:
        return 0

    if dry_run:
        print(build_saving_session_message(joinable))
        return 0

    joined_events = []
    for event in joinable:
        try:
            joined_codes = join_saving_session_event(
                token, account_number, event["code"]
            )
        except Exception as exc:
            log.error("Failed to join Saving Session event %s: %s", event["code"], exc)
            continue
        if event["code"] not in joined_codes:
            log.error(
                "Join request for event %s did not confirm membership (got: %s)",
                event["code"],
                joined_codes,
            )
            continue
        log.info(
            "Joined Saving Session event %s (%s - %s)",
            event["code"],
            event["startAt"],
            event["endAt"],
        )
        joined_events.append(event)
        cache.setdefault("joined_saving_sessions", [])
        if str(event["id"]) not in cache["joined_saving_sessions"]:
            cache["joined_saving_sessions"].append(str(event["id"]))

    if not joined_events:
        return 1

    message = build_saving_session_message(joined_events)
    try:
        send_telegram(bot_token, chat_id, message)
        log.info("Telegram message sent successfully for joined Saving Session(s).")
    except Exception as exc:
        log.error("Failed to send Telegram message: %s", exc)
        return 1

    return 0 if len(joined_events) == len(joinable) else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print message to stdout instead of sending to Telegram",
    )
    parser.add_argument("--config", metavar="PATH", help="Path to config file")
    parser.add_argument(
        "--cache-file",
        metavar="PATH",
        help=(
            "Path to the cache file recording the latest reported slot "
            "(default: $XDG_STATE_HOME/octogram/last_reported.json)"
        ),
    )
    parser.add_argument(
        "--discard-cache",
        action="store_true",
        help=(
            "Ignore the cache: report all qualifying upcoming slots (not just "
            "those new since the last report), and re-check joinability of "
            "all Saving Session events (already-joined ones are still "
            "skipped based on the Octopus API's own records)"
        ),
    )
    args = parser.parse_args()

    try:
        config_path = find_config(args.config)
        log.info("Using config: %s", config_path)
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1

    api_key = cfg.get("octopus", "api_key")
    account_number = cfg.get("octopus", "account_number")
    bot_token = cfg.get("telegram", "bot_token")
    chat_id = cfg.get("telegram", "chat_id")
    threshold = cfg.getfloat("settings", "price_threshold_p", fallback=0.0)

    cache_path = Path(args.cache_file) if args.cache_file else default_cache_file()
    cache = {} if args.discard_cache else load_cache(cache_path)

    exit_code = 0
    exit_code |= check_agile_prices(
        api_key, account_number, bot_token, chat_id, threshold, cache, args.dry_run
    )
    exit_code |= check_saving_sessions(
        api_key, account_number, bot_token, chat_id, cache, args.dry_run
    )

    if not args.dry_run:
        try:
            save_cache(cache_path, cache)
        except OSError as exc:
            log.warning("Could not write cache file %s: %s", cache_path, exc)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
