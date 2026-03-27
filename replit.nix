{pkgs}: {
  deps = [
    pkgs.chromium
    pkgs.libxkbcommon
    pkgs.cairo
    pkgs.pango
    pkgs.xorg.libxcb
    pkgs.xorg.libXrandr
    pkgs.xorg.libXfixes
    pkgs.xorg.libXext
    pkgs.xorg.libXdamage
    pkgs.xorg.libXcomposite
    pkgs.xorg.libX11
    pkgs.expat
    pkgs.dbus
    pkgs.cups
    pkgs.at-spi2-atk
    pkgs.alsa-lib
    pkgs.mesa
    pkgs.libdrm
    pkgs.gtk3
    pkgs.glib
    pkgs.nss
    pkgs.nspr
  ];
}
