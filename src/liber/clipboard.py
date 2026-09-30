import os
import shutil
import subprocess


def copy_to_clipboard(text: str) -> bool:
    """Copy via wl-copy (Wayland) or xclip (X11). Returns False if neither works."""
    commands = []
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append(["wl-copy"])
    commands.append(["xclip", "-selection", "clipboard"])
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False
