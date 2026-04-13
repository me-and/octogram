{
  lib,
  runCommand,
  python3,
}:
let
  python = python3.withPackages (pp: [ pp.requests ]);
in
runCommand "octogram"
  {
    buildInputs = [ python ];

    meta = {
      description = "Notify cheap/free Octopus Agile prices through Telegram";
      maintainers = [ lib.maintainers.me-and ];
      license = lib.licenses.mit;
    };
  }
  ''
    install -Dm755 ${./octogram.py} "$out"/bin/octogram
    patchShebangs "$out"
  ''
