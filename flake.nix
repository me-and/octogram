{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      self,
    }@inputs:
    {
      nixosModules = {
        octogram = import ./module.nix { inherit inputs; };
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
    }
    // flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        packages = {
          octogram = import ./. {
            pkgs = pkgs;
            lib = nixpkgs.lib;
          };
          default = self.packages."${system}".octogram;
        };
        formatter = pkgs.nixfmt-tree;
      }
    );
}
