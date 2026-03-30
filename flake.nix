{
  description = "VoxTerm — local offline voice transcription TUI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;

        isDarwin = pkgs.stdenv.isDarwin;
        isLinux = pkgs.stdenv.isLinux;

        # System libraries needed at runtime
        darwinDeps = with pkgs; [
          apple-sdk_15
          swiftPackages.swift
        ];

        linuxDeps = with pkgs; [
          pulseaudio
          alsa-lib
          alsa-plugins
        ];

        commonDeps = with pkgs; [
          portaudio
          ffmpeg
        ];

      in {
        devShells.default = pkgs.mkShell {
          name = "voxterm";

          packages = with pkgs; [
            python
            python.pkgs.pip
          ] ++ commonDeps
            ++ pkgs.lib.optionals isDarwin darwinDeps
            ++ pkgs.lib.optionals isLinux linuxDeps;

          shellHook = ''
            # Create venv if it doesn't exist
            if [ ! -d .venv ]; then
              echo "Creating Python virtual environment..."
              ${python}/bin/python3 -m venv .venv
            fi
            source .venv/bin/activate

            # Ensure pip can find native libs
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath (commonDeps ++ pkgs.lib.optionals isLinux linuxDeps)}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          '' + pkgs.lib.optionalString isDarwin ''
            export DYLD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath commonDeps}''${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
          '';
        };
      }
    );
}
