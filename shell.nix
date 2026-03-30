# Compatibility wrapper for users without flakes enabled.
# Prefer `nix develop` if you have flakes; this file calls into the flake.
(import
  (
    fetchTarball {
      url = "https://github.com/edolstra/flake-compat/archive/refs/tags/v1.0.1.tar.gz";
      sha256 = "0m9grvfsbvoz4wqjq5bmmjlag7a3bbakpc3s5rpc7ho1cy7308qv";
    }
  )
  { src = ./.; }
).shellNix
