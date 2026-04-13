# Octogram

Checks upcoming [Octopus Agile](https://octopus.energy/agile/) electricity prices and sends you a [Telegram](https://telegram.org/) message whenever there are upcoming half-hour slots where the price is **zero or negative** (i.e. the grid is paying you to use electricity).

## How it works

1. Fetches your active tariff code from the Octopus Energy API using your account number.
2. Retrieves upcoming half-hour unit rates for the next 24 hours.
3. Filters slots at or below a configurable price threshold (default: 0p/kWh).
4. If qualifying slots exist, sends a Telegram message. If none exist, exits silently.

Example notification:

```
⚡ Octopus Agile: Free/Negative slots found!

• Sat 12 Apr 02:00–02:30  −2.50p/kWh
• Sat 12 Apr 02:30–03:00  −1.20p/kWh
• Sat 12 Apr 03:00–03:30   0.00p/kWh

3 slot(s) | 90 minutes total
```

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
