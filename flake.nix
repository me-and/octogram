{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs =
    {
      nixpkgs,
      self,
    }:
    {
      nixosModules = {
        octogram = import ./module.nix;
        default = self.nixosModules.octogram;
      };

      overlays = {
        octogram = final: prev: {
          octogram = import ./. {
            pkgs = final;
            lib = nixpkgs.lib;
          };
        };
        default = self.overlays.octogram;
      };

      packages = builtins.mapAttrs (system: pkgs: {
        octogram = import ./. { inherit pkgs; };
        default = self.packages."${system}".octogram;
      }) nixpkgs.legacyPackages;

      formatter = builtins.mapAttrs (system: pkgs: pkgs.nixfmt-tree) nixpkgs.legacyPackages;
    };
}
