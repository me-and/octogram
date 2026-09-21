{
  lib,
  config,
  pkgs,
  ...
}:
let
  cfg = config.services.octogram;

  octogram = import ./. { inherit pkgs; };
in
{
  options.services.octogram = {
    enable = lib.mkEnableOption "Octogram Octopus Agile -> Telegram price notifications";
    configFile = lib.mkOption {
      description = ''
        Path to the Octogram configuration file.  This will contain both
        Octopus and Telegram API keys, so almost certainly shouldn't be added
        to the Nix store.
      '';
      example = lib.mdLiteral "config.sops.templates.octogram-conf.path";
    };
    onCalendar = lib.mkOption {
      description = "Systemd timer OnCalendar value to run Octogram.";
      default = "hourly";
      type = lib.types.singleLineStr;
    };
    randomizedOffset = lib.mkOption {
      description = "Systemd timer RandomizedOffsetSec value to run Octogram.";
      default = "1h";
      type = lib.types.nullOr lib.types.singleLineStr;
    };
    accuracy = lib.mkOption {
      description = "Systemd timer AccuracySec value to run Octogram.";
      default = "15min";
      type = lib.types.nullOr lib.types.singleLineStr;
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.octogram = {
      description = "Report upcoming cheap Octopus Agile prices";
      wants = [ "network-online.service" ];
      after = [ "network-online.service" ];
      script = ''${octogram}/bin/octogram --config "$CREDENTIALS_DIRECTORY"/octogram.conf --cache-file "$STATE_DIRECTORY"/last_reported.json'';
      serviceConfig = {
        Type = "oneshot";
        DynamicUser = true;
        StateDirectory = "octogram";
        CapabilityBoundingSet = "";
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        ProtectControlGroups = true;
        ProtectHome = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectSystem = "strict";
        # AF_UNIX is required for DNS resolution via systemd-resolved
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [ "@system-service" ];
        LoadCredential = "octogram.conf:${cfg.configFile}";
      };
    };
    systemd.timers.octogram = {
      description = "Daily report of upcoming cheap Octopus Agile prices";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.onCalendar;
        Persistent = true;
      }
      // lib.optionalAttrs (cfg.accuracy != null) {
        AccuracySec = cfg.accuracy;
      }
      // lib.optionalAttrs (cfg.randomizedOffset != null) {
        RandomizedOffsetSec = cfg.randomizedOffset;
      };
    };
  };
}
