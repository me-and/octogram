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
      default = "17:00 UTC";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.octogram = {
      description = "Report upcoming cheap Octopus Agile prices";
      wants = [ "network-online.service" ];
      after = [ "network-online.service" ];
      script = ''
        ${octogram}/bin/octogram --config ${lib.escapeShellArg cfg.configFile}
      '';
      serviceConfig.Type = "oneshot";
    };
    systemd.timers.octogram = {
      description = "Daily report of upcoming cheap Octopus Agile prices";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.onCalendar;
        Persistent = true;
        AccuracySec = "1h";
        RandomizedDelaySec = "1h";
      };
    };
  };
}
