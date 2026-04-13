{
  pkgs ? import <nixpkgs> { },
  lib ? pkgs.lib,
}:
pkgs.callPackage ./package.nix { }
