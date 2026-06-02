"""
Argus Eye — terminal art for the startup banner.

Three sizes: FULL (11 lines, 90+ col terminals),
             COMPACT (7 lines, 70-89 col),
             NONE (< 70 col — text only).
"""

# Full eye: realistic anatomy — curved lids, iris ring, dark pupil, catchlight.
# Uses braille block characters for smooth arcs and Unicode symbols for the pupil.
ARGUS_EYE_FULL = """\
[cyan]        ⢀⣀⣠⣤⣤⣤⣤⣤⣤⣀⡀[/cyan]
[cyan]      ⣠⡾⠟⠋⠉⠁   ⠈⠉⠙⠻⢷⣄[/cyan]
[cyan]    ⢀⣾⠋⠁   ⢀⣤⣶⣶⡄   ⠈⠙⣷⡀[/cyan]
[cyan]   ⣼⡟⠁   ⢀⣾⡿⠛⠛⠿⣷⡄   ⠈⢻⣧[/cyan]
[cyan]  ⢸⣿    ⢀⣾⡟  [bold white]◉[/bold white][cyan]  ⢻⣷⡀   ⢸⣿[/cyan]
[cyan]   ⣿⡇   ⢸⣿⠁  [dim white]·[/dim white][cyan]  ⠈⣿⡇   ⣿⡇[/cyan]
[cyan]   ⢿⣧⡀   ⠻⣷⣄⣀⣀⣠⣾⠟   ⣠⣿[/cyan]
[cyan]    ⠘⣿⣦⡀   ⠈⠛⠿⠿⠛⠁  ⢀⣴⣿⠃[/cyan]
[cyan]      ⠙⢿⣷⣦⣄⣀⣀⣀⣀⣤⣴⣾⡿⠋[/cyan]
[cyan]          ⠉⠛⠻⠿⠿⠿⠛⠋⠁[/cyan]\
"""

# Compact eye for medium-width terminals
ARGUS_EYE_COMPACT = """\
[cyan]     ⢀⣤⣶⣶⣤⡀[/cyan]
[cyan]   ⣠⡿⠛⠁ ⠈⠻⢷⡄[/cyan]
[cyan]  ⣾⡟  [bold white]◉[/bold white][cyan] [dim white]·[/dim white][cyan] ⢻⣷[/cyan]
[cyan]   ⠿⣷⣄⣀⣠⣾⠿[/cyan]
[cyan]     ⠉⠛⠛⠉[/cyan]\
"""


def get_eye(terminal_width: int) -> str | None:
    """Return the appropriate eye art string for the given terminal width, or None."""
    if terminal_width >= 90:
        return ARGUS_EYE_FULL
    if terminal_width >= 70:
        return ARGUS_EYE_COMPACT
    return None
