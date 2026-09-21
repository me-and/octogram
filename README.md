# Octogram

Checks upcoming [Octopus Agile](https://octopus.energy/agile/) electricity prices and sends you a [Telegram](https://telegram.org/) message whenever there are upcoming half-hour slots where the price is **zero or negative** (i.e. the grid is paying you to use electricity). It also watches for Octopus **Saving Sessions** (the "Power Down"-style challenges, e.g. a reward for using less electricity between 6pm and 7pm), automatically joins any you're eligible for and haven't already joined, and notifies you via Telegram when it does.

## How it works

### Agile price checking

1. Fetches your active tariff code from the Octopus Energy API using your account number.
2. Retrieves upcoming half-hour unit rates for the next 24 hours.
3. Filters slots at or below a configurable price threshold (default: 0p/kWh).
4. Compares against a cache of the latest slot reported on a previous run, so only slots that are new since then are considered.
5. If new qualifying slots exist, sends a Telegram message and updates the cache. If none exist, exits silently.

Example notification:

```
⚡ Octopus Agile: Free/Negative slots found!

• Sat 12 Apr 02:00–02:30  −2.50p/kWh
• Sat 12 Apr 02:30–03:00  −1.20p/kWh
• Sat 12 Apr 03:00–03:30   0.00p/kWh

3 slot(s) | 90 minutes total
```

### Saving Sessions ("Power Down" challenges)

1. Exchanges your Octopus API key for a short-lived token, then queries Octopus's Saving Sessions GraphQL API for available events and your account's campaign/join status.
2. Filters out events that are dev/test events, carry no reward, have already finished, aren't valid for your region, or that you've already joined (checked against both the Octopus API's own record and a local cache, so re-running the script doesn't attempt to re-join an event).
3. Joins any remaining eligible events.
4. Sends a Telegram message listing the event(s) just joined, and updates the cache so they aren't joined again.

This is designed to be run on a frequent cadence (e.g. hourly) via cron/systemd timer, so new Saving Sessions get joined automatically and promptly.

Example notification:

```
🔋 Octopus Saving Session: Joined!

• Mon 21 Sep 18:00-19:00 — 75 OctoPoints/kWh

Signed up for 1 session(s).
```

### Cache and `--discard-cache`

Both checks share a single cache file recording the latest reported price slot and the IDs of Saving Session events already joined. Pass `--discard-cache` to ignore the cache for one run: every currently qualifying upcoming price slot is reported, and every currently-available Saving Session event is re-considered for joining (though events already joined per the Octopus API itself are still skipped, so this can't cause a duplicate join). The cache is rebuilt afterwards, so subsequent runs go back to normal behaviour.

## Prerequisites

- Python 3.10+
- `pip` / `pip3`
- A [Telegram](https://telegram.org/) account
- An [Octopus Energy](https://octopus.energy/) account on an Agile tariff

## Installation

```bash
git clone https://github.com/me-and/octogram.git
cd octogram
pip3 install -r requirements.txt
cp octogram.conf.example "${XDG_CONFIG_HOME:-"$HOME"/.config}"/octogram.conf
```

Then edit `octogram.conf` with your credentials (see sections below).

## Octopus API setup

1. Log in to your Octopus dashboard.
2. Go to **Personal details → API access**: https://octopus.energy/dashboard/new/accounts/personal-details/api-access
3. Copy your **API key** (starts with `sk_live_`).
4. Your **account number** is shown on bills and the dashboard (format: `A-XXXXXXXX`).

Add these to `octogram.conf`:

```ini
[octopus]
api_key = sk_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXX
account_number = A-XXXXXXXX
```

The script will automatically discover your active Agile tariff details from your account.

## Telegram bot setup

### 1. Create a bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts to choose a name and username for your bot.
3. BotFather will give you a **bot token** that looks like `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`. Copy it.

### 2. Find your chat ID

You need your personal chat ID so the bot knows where to send messages.

**Option A — using @RawDataBot (easiest):**
1. Search for **@RawDataBot** in Telegram and start a chat with it.
2. It will immediately reply with your full user info. Your chat ID is the `"id"` field inside the `"chat"` object.

**Option B — via the Telegram API:**
1. Search for your new bot by username in Telegram and send it **any message** (e.g. "hello"). This step is essential — `getUpdates` returns nothing until the bot has received at least one message.
2. Open this URL in your browser (replace `<TOKEN>` with your bot token):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":XXXXXXXXX}` in the JSON response. That number is your chat ID.

### 3. Update the config

```ini
[telegram]
bot_token = 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
chat_id = 123456789
```

## Configuration reference

```ini
[octopus]
api_key = sk_live_...          # Required. Your Octopus API key.
account_number = A-XXXXXXXX   # Required. Your Octopus account number.

[telegram]
bot_token = ...                # Required. Token from @BotFather.
chat_id = ...                  # Required. Your Telegram chat ID.

[settings]
price_threshold_p = 0          # Optional. Report slots ≤ this price in p/kWh (default: 0).
                               # Set to e.g. 5 to also catch very cheap slots.
```

## Running manually

```bash
# Normal run — sends a Telegram message if qualifying slots are found
python3 octogram.py

# Dry run — prints the message to stdout, does not send to Telegram
python3 octogram.py --dry-run

# Use a specific config file
python3 octogram.py --config /etc/octogram/octogram.conf

# Discard the cache and report every currently qualifying upcoming slot
python3 octogram.py --discard-cache

# Use a specific cache file (default: $XDG_STATE_HOME/octogram/last_reported.json)
python3 octogram.py --cache-file /var/lib/octogram/last_reported.json
```

## Nixpkgs / NixOS users

With Nixpkgs, `nix-build` and the like in the current directory should build the `octogram` executable.

With NixOS + Nix Flakes, consider adding something like the below to your `flake.nix`:

```nix
{
  inputs = {
    nixpkgs = { };
    flake-utils = { };
    octogram = {
      url = "github:me-and/octogram";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };

  outputs =
    { nixpkgs, octogram, ... }:
    {
      nixosConfigurations.box = nixpkgs.lib.nixosSystem {
        modules = [
          octogram.nixosModules.default
          {
            services.octogram = {
              enable = true;
              configFile = "/path/to/octogram.conf";
            };
          }
        ];
      };
    };
}
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not find an active electricity tariff` | Check your `account_number` and that your Agile agreement is active in the Octopus dashboard |
| `401 Unauthorized` from Octopus | Check your `api_key` |
| Telegram message not delivered | Ensure you have started a chat with your bot (send it `/start`) before the first run |
| `Forbidden` from Telegram | Your `chat_id` may be wrong — re-check using @userinfobot |
| No notification even though prices are free | Prices for the next day are only published from ~16:00–16:30 UTC; run after 17:00 UTC |
