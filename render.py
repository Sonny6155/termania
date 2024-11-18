import curses
import math
import threading

from just_playback import Playback

from bps_lines import BPSLines
from flag import Flag
from game_field import GameField
from judgement import Judgement
from note import Note, TapNote, HoldNote, RollNote, MineNote  # Renderer needs to break encapsulation


# Each build func returns the y, x, text, attr (bitfield, where 0 = no attr)


def build_hud(
    judgement_counts: dict[Judgement, int],
    nps: float,
    accuracy: float,
    hud_colour: int,
    judgement_colours: dict[Judgement, int],
) -> list[tuple[int, int, str, int]]:
    # Returns draw data for the side HUD
    return [
        (0, 0, f"MARVELOUS: {judgement_counts[Judgement.MARVELOUS]}", judgement_colours[Judgement.MARVELOUS]),
        (1, 2, f"PERFECT: {judgement_counts[Judgement.PERFECT]}", judgement_colours[Judgement.PERFECT]),
        (2, 4, f"GREAT: {judgement_counts[Judgement.GREAT]}", judgement_colours[Judgement.GREAT]),
        (3, 5, f"GOOD: {judgement_counts[Judgement.GOOD]}", judgement_colours[Judgement.GOOD]),
        (4, 6, f"BOO: {judgement_counts[Judgement.BOO]}", judgement_colours[Judgement.BOO]),
        (5, 5, f"MISS: {judgement_counts[Judgement.MISS]}", judgement_colours[Judgement.MISS]),
        (6, 7, f"OK: {judgement_counts[Judgement.OK]}", judgement_colours[Judgement.OK]),
        (7, 7, f"NG: {judgement_counts[Judgement.NG]}", judgement_colours[Judgement.NG]),

        (9, 6, f"NPS: {nps}", hud_colour),
        (10, 1, f"Accuracy: {accuracy*1000:.2f}ms", hud_colour),
    ]


def build_field_xmod(
    col_count: int,
    hit_line_y: int,
    max_y: int,
    spacing: float,
    song_beat: float,
    colours: tuple[int, int],
) -> list[tuple[int, int, str, int]]:
    # Returns draw data for the hit line and measure lines
    patches = []

    # Render measure/beat lines, noting SM's 4/4 assumption
    i = 0
    strong_beat = (4 - song_beat % 4) // 1
    beat_offset = 1 - song_beat % 1
    line_y = round(spacing * beat_offset) + hit_line_y
    while line_y < max_y:
        if i % 4 == strong_beat:
            patches.append((line_y, 0, "-----" * col_count, colours[0]))
        else:
            patches.append((line_y, 0, "  -  " * col_count, colours[1]))
        i += 1
        line_y = round(spacing * (beat_offset + i)) + hit_line_y

    # Render hit line in front, regardless of rounding
    patches.append((hit_line_y, 0, "-----" * col_count, colours[0]))

    return patches


def build_field_cmod(
    bps_cursor: int,
    bps_lines: BPSLines,
    col_count: int,
    hit_line_y: int,
    max_y: int,
    spacing: float,
    song_beat: float,
    song_time: float,
    colours: tuple[int, int],
) -> list[tuple[int, int, str, int]]:
    # Returns draw data for the hit line and measure lines
    patches = []

    i = int(song_beat // 1) + 1
    line_time, _ = bps_lines.time_at_beat(
        i,
        last_line_index=bps_cursor,
        allow_stop=True,
        allow_warp=True,
    )  # Still avg O(1), but gimmicky maps may loop more
    # Ensure any out of range is safely ignored
    line_y = float("inf") if math.isinf(line_time) else -round(spacing * (song_time - line_time)) + hit_line_y
    while line_y < max_y:
        if i % 4 == 0:
            patches.append((line_y, 0, "-----" * col_count, colours[0]))
        else:
            patches.append((line_y, 0, "  -  " * col_count, colours[1]))
        i += 1
        line_time, _ = bps_lines.time_at_beat(
            i,
            last_line_index=bps_cursor,
            allow_stop=True,
            allow_warp=True,
        )
        line_y = float("inf") if math.isinf(line_time) else -round(spacing * (song_time - line_time)) + hit_line_y

    # Render hit line above
    patches.append((hit_line_y, 0, "-----" * col_count, colours[0]))

    return patches


def build_notes(
    max_y: int,
    head_y_func,  # Callable
    tail_y_func,  # Callable
    render_columns: list[list[Note]],
    render_cursors: list[int],
    song_time: float,
    colours: tuple[int, int, int, int, int],
) -> list[tuple[int, int, str, int]]:
    # Returns the draw data for notes
    # The closures are a lazy way of swapping out pos funcs for x/cmod
    # Mutates render_cursors

    patches = []

    for col_i, column in enumerate(render_columns):
        # Starting from our cached start point, render notes until off-screen or exhausted
        note_x = col_i * 5
        note_i = render_cursors[col_i]  # Start from last cache
        first_renderable = None  # Determines where to move the cache to next loop, accounting for long notes
        on_screen = True
        
        while on_screen and note_i < len(column):
            note = column[note_i]
            note_y = head_y_func(note)
            note_judgement = note.judgement  # Snapshot

            # Colour palette roughly based on DDR's NOTE system
            if abs(note.measure_pos % (note.measure_fraction / 4)) <= 0.01:
                note_colour = colours[0]
            elif abs(note.measure_pos % (note.measure_fraction / 8)) <= 0.01:
                note_colour = colours[1]
            elif abs(note.measure_pos % (note.measure_fraction / 16)) <= 0.01:
                note_colour = colours[2]
            else:
                note_colour = colours[3]
            # Reserve 5th colour for hold/roll/special notes

            # Note hasn't arrived yet, so column is fully rendered
            if note_y >= max_y:
                on_screen = False

            # Special case: Keep long notes rendered until fully past
            elif isinstance(note, HoldNote):
                tail_y = tail_y_func(note)

                # Still on field, so keep cursor at most here
                if note_y < max_y and tail_y >= 0 and note_judgement != Judgement.OK:
                    if first_renderable is None:
                        first_renderable = note_i

                    # Render the body first (with user feedback)
                    body_i = max(note_y, 0)
                    max_tail_y = min(tail_y, max_y)
                    if note_judgement is not None:
                        note_str = " - "  # Drop/miss
                    elif (song_time - note.last_held) < 0.05:
                        # 50ms should work fine unless game polls roughly <1/20?
                        note_str = "|||"
                    else:
                        note_str = " | "

                    while body_i < max_tail_y:
                        patches.append((body_i, note_x + 1, note_str, note_colour))
                        body_i += 1

                    # Then render the head and tail if on screen
                    if max_tail_y == tail_y:
                        patches.append((max_tail_y, note_x, "[===]", note_colour))

                    if note_y >= 0:
                        patches.append((note_y, note_x, "[===]", note_colour))

            elif isinstance(note, RollNote):
                tail_y = tail_pos_func

                # Still on field, so keep cursor at most here
                if note_y < max_y and tail_y >= 0 and note_judgement != Judgement.OK:
                    if first_renderable is None:
                        first_renderable = note_i

                    # Render the body first (with user feedback)
                    body_i = max(note_y, 0)
                    max_tail_y = min(tail_y, max_y)
                    if note_judgement is not None:
                        note_str = " - "  # Drop/miss
                    elif (song_time - note.last_held) <= 0.15:
                        note_str = "<W>"
                    else:
                        note_str = " W "

                    while body_i < max_tail_y:
                        patches.append((body_i, note_x + 1, note_str, note_colour))
                        body_i += 1

                    # Then render the head and tail if on screen
                    if max_tail_y == tail_y:
                        patches.append((max_tail_y, note_x, "<<->>", note_colour))

                    if note_y >= 0:
                        patches.append((note_y, note_x, "<<->>", note_colour))

            # On-field and still pending scoring or a miss
            elif note_y >= 0 and (
                note_judgement is None or
                note_judgement in (Judgement.MISS, Judgement.NG)
            ):
                if first_renderable is None:
                    first_renderable = note_i

                if isinstance(note, MineNote):
                    patches.append((note_y, note_x, "  X  " , colours[4]))
                else:
                    patches.append((note_y, note_x, "[===]" , note_colour))

            # Do not render successful notes nor those gone past

            note_i += 1
        # End rendering this whole column

        # Update the render cache
        if first_renderable is not None:
            render_cursors[col_i] = first_renderable
        elif note_i >= len(column):
            # Column is exhausted, ensure it always skips future loops
            render_cursors[col_i] = note_i
        # Otherwise there might still be more notes offscreen, so do nothing
            
    # End rendering all note columns
    return patches


def render(
    keep_running: Flag,
    playback: Playback,
    offset: float,
    game_field: GameField,  # Thread-safe game state retrieval
    bps_lines: BPSLines,  # Read-only note position lookup
    note_columns: list[list[Note]],  # To be cloned raw notes
    xmod: bool = True,  # Swaps between dynamic BPS XMod and CMod
    scroll: float = 1,  # Governs spacing (recommended no less than 1 on terminals)
    colour_support: bool = False,
    min_tick_rate: float = 1.0/120.0,  # Some terminals are frame capped, so 60-120hz maybe
):
    # Handles all stdout writing
    # NOTE: Do not mutate game_field, raw notes, or bps_lines from here
    # We will also break encapsulation a bit to analyse notes for detailed rendering

    # First, clone and sort our local notes view
    # In xmod, we always reflect chart order when rendering, rather than true hit time
    if xmod:
        render_columns = [
            sorted(column, key=lambda x: x.beat)
            for column in note_columns
        ]

    # But in cmod, we want to reflect the actual game logic by rendering at
    # constant time and explicitly drop unhittable notes
    else:
        render_columns = [
            sorted(
                (x for x in column if not math.isinf(x.timing)),
                key=lambda x: x.timing,
            )
            for column in note_columns
        ]

    # Cache for O(1) lookups
    render_cursors = [0] * len(note_columns)  # Due to sorted notes, remember pos for next frame
    bps_cursor = 0  # BPS lookups are also forward-only
    # As seen later, we only need to render notes until off-screen, hence tiny window

    # Static values for the game session
    # NOTE: Assuming upscroll, 5 char width columns, no col spacing on game panel
    game_width = len(render_columns) * 5  # Used for dynamic positioning
    hud_width = 20  # Known BB
    hit_line_y = 4  # Offset from screen edge
    spacing = 8 * scroll  # Tuned to create a 8 char beat spacing at scroll 1
    # NOTE: CMod results in 8 chars per second at scroll 1, simulating 60BPM
    # Due to the limited resolution of a (vertical) terminal, I would not suggest <8 per full beat
    
    # For the lack of a threaded curses wrapper, teardown errors manually
    try:
        # Prep terminal
        stdscr = curses.initscr()
        curses.noecho()  # Prevent stdin affecting console
        #stdscr.nodelay(True)  # Non-blocking stdin on getch()  # NOTE: Will only use in ANSI version
        stdscr.keypad(True)  # Consume arrows to prevent weird behaviours

        # Define colour palette
        # TODO: accept a colour on/off flag in main
        if colour_support:
            curses.start_color()  # Inits ANSI colour palette and sets BG to true black
            # int 0 is reserved for white on black
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(5, curses.COLOR_BLUE, curses.COLOR_BLACK)
            curses.init_pair(6, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
            curses.init_pair(7, curses.COLOR_CYAN, curses.COLOR_BLACK)
            # NOTE: Assumes the term is configured for bold == bright for full 4-bits

            note_colours = (
                curses.color_pair(2) | curses.A_BOLD,
                curses.color_pair(7),
                curses.color_pair(4),
                curses.color_pair(3),
                curses.color_pair(0) | curses.A_BOLD,
            )
            judgement_colours = {
                Judgement.MARVELOUS: curses.color_pair(0) | curses.A_BOLD,
                Judgement.PERFECT: curses.color_pair(4) | curses.A_BOLD,
                Judgement.GREAT: curses.color_pair(3) | curses.A_BOLD,
                Judgement.GOOD: curses.color_pair(3),
                Judgement.BOO: curses.color_pair(6),
                Judgement.MISS: curses.color_pair(2),
                Judgement.OK: curses.color_pair(0),
                Judgement.NG: curses.color_pair(2),
            }
            field_colours = (
                curses.color_pair(0),
                curses.color_pair(1) | curses.A_BOLD,
            )
            hud_colour = curses.color_pair(0)
        else:
            note_colours = (0, 0, 0, 0, 0)
            judgement_colours = {
                Judgement.MARVELOUS: 0,
                Judgement.PERFECT: 0,
                Judgement.GREAT: 0,
                Judgement.GOOD: 0,
                Judgement.BOO: 0,
                Judgement.MISS: 0,
                Judgement.OK: 0,
                Judgement.NG: 0,
            }
            field_colours = (0, 0)
            hud_colour = 0

        # TODO: All threads are typically ready in time, though we might want to add a short delay to warn of game start?
        stdscr.addstr(0, 0, "loading")
        stdscr.refresh()
        event = threading.Event()
        #event.wait(1)

        while keep_running.state:
            # If the user resizes, prefer to drop the frame rather than crash
            try:
                # Clear last frame
                stdscr.erase()  # Presumably an optimised but maybe imperfect clear?

                # Snapshot live game states (other than notes)
                judgement_counts, last_judgement, nps, accuracy = game_field.get_metrics()
                song_time = playback.curr_pos + offset if playback.active else offset

                # Calculate frame-specific vars
                song_beat, bps_cursor = bps_lines.beat_at_time(
                    song_time,
                    last_line_index=bps_cursor,
                    allow_stop=True,
                    allow_warp=True,
                )
                r, c = stdscr.getmaxyx()

                # Split out patch building, but assemble and finalise draw here
                # This simplifies toggling features, transparency, position/constraints, etc
                # As a side effect, the interface is clearer and uses way less nesting

                # Will handle panels manually, since curses subwindows can be janky and panels can still curses.error

                # Calculate offsets for panel layout
                if c > game_width + (hud_width * 2):
                    # Try center game area, then center HUD in right gap
                    game_offset_x = int((c-1 - game_width) // 2)
                    hud_offset_x = game_offset_x + game_width + int((game_offset_x - hud_width) // 2)
                elif c > game_width + hud_width:
                    # Otherwise, try with game justified left
                    game_offset_x = 0
                    hud_offset_x = int((c-1 + game_width - hud_width) // 2)
                else:
                    # No space, so hide the HUD via sentinel
                    # Frame just drops if the game itself can't fit
                    game_offset_x = 0
                    hud_offset_x = None

                # Render HUD (fixed y offset)
                if hud_offset_x is not None:
                    hud_data = build_hud(
                        judgement_counts,
                        nps,
                        accuracy,
                        hud_colour,
                        judgement_colours,
                    )
                    for text_y, text_x, text, text_attr in hud_data:
                        if r > 10 + text_y:
                            stdscr.addnstr(10 + text_y, hud_offset_x + text_x, text, hud_width, text_attr)
                
                # Render game underlay
                if xmod:
                    field_data = build_field_xmod(
                        len(render_columns),
                        hit_line_y,
                        r-1,
                        spacing,
                        song_beat,
                        field_colours,
                    )
                else:
                    field_data = build_field_cmod(
                        bps_cursor,
                        bps_lines,
                        len(render_columns),
                        hit_line_y,
                        r-1,
                        spacing,
                        song_beat,
                        song_time,
                        field_colours,
                    )
                for text_y, text_x, text, text_attr in field_data:
                    stdscr.addstr(text_y, game_offset_x + text_x, text, text_attr)

                # Render all on-screen notes
                if xmod:
                    # Inject closures to avoid ~100 lines of duplication
                    note_data = build_notes(
                        r-1,
                        lambda note: -round(spacing * (song_beat - note.beat)) + hit_line_y,
                        lambda note: -round(spacing * (song_beat - note.tail_beat)) + hit_line_y,
                        render_columns,
                        render_cursors,
                        song_time,
                        note_colours,
                    )
                else:
                    note_data = build_notes(
                        r-1,
                        lambda note: -round(spacing * (song_time - note.timing)) + hit_line_y,
                        lambda note: -round(spacing * (song_time - note.tail_timing)) + hit_line_y,
                        render_columns,
                        render_cursors,
                        song_time,
                        note_colours,
                    )
                for text_y, text_x, text, text_attr in note_data:
                    stdscr.addstr(text_y, game_offset_x + text_x, text, text_attr)

                # Render overlay (judgement and pause state)
                # Per tradition, render over game area
                game_mid = int(game_width // 2) + game_offset_x
                if r > 10 and last_judgement is not None:
                    text = str(last_judgement)[10:]  # Exploit enum form
                    stdscr.addstr(
                        10,
                        max(0, game_mid - int(len(text) // 2)),
                        text,
                        judgement_colours[last_judgement]
                    )

                if r > 11 and playback.active and not playback.playing:
                    stdscr.addstr(11, max(0, game_mid - 3), "PAUSED", hud_colour)

                stdscr.move(r-1, c-1)
                stdscr.refresh()
                event.wait(min_tick_rate)
            except curses.error:
                # Window overflow due to user resize or tiny window
                try:
                    stdscr.addstr(0, 0, "Frame dropped:\nResizing or window too small")
                    stdscr.refresh()
                except curses.error:
                    # Too small to even write that? Give up and loop
                    pass

        # End of game loop
        # Manually teardown curses before game exit
        curses.nocbreak()
        stdscr.keypad(False)
        curses.echo()
        curses.flushinp()  # Flush stdin so game exit doesn't dump junk to CLI
        curses.endwin()

    except:
        # Try as best as possible to teardown safely (might not work)
        curses.nocbreak()
        stdscr.keypad(False)
        curses.echo()
        curses.flushinp()
        curses.endwin()
        raise
