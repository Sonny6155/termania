import curses
import math
import os
import sys
import threading

#import numpy as np
import pynput
from just_playback import Playback

from bps_lines import BPSLines
from game_field import GameField
from judgement import Judgement
from note import Note, TapNote, HoldNote, RollNote, MineNote  # Renderer needs to break encapsulation
from sm_reader import SMReader


# Launches the threads and
# This variant uses pynput to capture hold/release for a slightly better
# mine/hold experience


class Flag:
    # Hack to pass a bool flag by ref, used for threads
    def __init__(self, init_state: bool):
        self.state = init_state


class GameKeys:
    # Mini thread-safe key state manager
    # Stores which keys are used for the current chart, and which are held down
    # TODO: Probably won't need in ANSI input version, hence kept it separate from game obj
    def __init__(self, keys: list[str]):
        self.__keys = [x for x in keys]
        self.__held_keys = [False] * len(keys)
        self.__held_keys_lock = threading.Lock()

    def char_exists(self, char: str) -> bool:
        return char in self.__keys

    def index_of(self, char: str) -> int:
        return self.__keys.index(char)  # May ValueError

    def is_held(self, index: int) -> bool:
        # Shame there's no overloading by default in python
        # This could be cleaner tbh
        with self.__held_keys_lock:
            return self.__held_keys[index]  # May IndexError

    def press(self, index: int) -> None:
        with self.__held_keys_lock:
            self.__held_keys[index] = True  # May IndexError

    def release(self, index: int) -> None:
        with self.__held_keys_lock:
            self.__held_keys[index] = False  # May IndexError

    @property
    def keys(self) -> list[str]:
        return [x for x in self.__keys]

    @property
    def held_keys(self) -> list[bool]:
        with self.__held_keys_lock:
            return [x for x in self.__held_keys]


# Event handlers (wrapped to avoid globals)
# This should also detach input from game state polling rate for precise scoring?
# TODO: If these block input for too long (unlikely) make it call an unawaited async task instead
def init_on_press(
    keep_running: Flag,
    playback: Playback,
    offset: float,
    game_field: GameField,  # Thread-safe game obj
    game_keys: GameKeys,
):  # TODO: typehint callable
    def on_press(key) -> None:
        # Inherit shared refs from init
        nonlocal keep_running
        nonlocal playback
        nonlocal game_field
        nonlocal game_keys

        # On backspace, end program safely
        if key == pynput.keyboard.Key.backspace:
            keep_running.state = False
            
        # On ESC, toggle pause
        elif key == pynput.keyboard.Key.esc:
            if playback.active:
                if playback.playing:
                    playback.pause()
                else:
                    playback.resume()

        # On game key (ignoring autorepeats), trigger note handlers
        elif playback.playing and hasattr(key, "char") and game_keys.char_exists(key.char):
            key_index = game_keys.index_of(key.char)
            if not game_keys.is_held(key_index):
                game_keys.press(key_index)

                # Pass press event to game obj
                song_time = playback.curr_pos + offset if playback.active else offset
                game_field.press_key(key_index, song_time)

    return on_press


def init_on_release(
    playback: Playback,
    offset: float, 
    game_field: GameField,  # Thread-safe game obj
    game_keys: GameKeys,
):
    def on_release(key) -> None:
        # Inherit shared refs from init
        nonlocal playback
        nonlocal game_field
        nonlocal game_keys

        # Reallow key press events on previously held
        if playback.playing and hasattr(key, "char") and game_keys.char_exists(key.char):
            key_index = game_keys.index_of(key.char)
            game_keys.release(key_index)

            # Pass release event to game obj
            song_time = playback.curr_pos + offset if playback.active else offset
            game_field.release_key(key_index, song_time)

    return on_release


# Core thread routines
def game_logic(
    keep_running: Flag,
    playback: Playback,
    offset: float,
    game_field: GameField,  # Thread-safe game state polling
    game_keys: GameKeys,
    min_tick_rate: float = 1.0/30.0,  # This can be slow-ish, because the input handlers are fast
):
    # The job of this thread will be to regularly poll non-user game events

    # Start song
    playback.play()
    # Input should already be able to pause, but not kill the song
    
    # Fire game ticks until it detects the chart is done
    chart_complete = False
    event = threading.Event()
    while keep_running.state and not chart_complete:
        if playback.playing:
            song_time = playback.curr_pos + offset if playback.active else offset
            chart_complete = game_field.poll(song_time, game_keys.held_keys)
        event.wait(min_tick_rate)

    # End all threads safely
    keep_running.state = False


# This is actually handled by main to ensure safe shutdown
def render(
    keep_running: Flag,
    playback: Playback,
    offset: float,
    game_field: GameField,  # Thread-safe game state retrieval
    bps_lines: BPSLines,  # Read-only note position lookup
    note_columns: list[list[Note]],  # To be cloned raw notes
    cmod_override: float | None = None,  # Swaps to cmod at a specific BPS
    scroll: float = 1,  # Governs spacing (recommended no less than 1 on terminals)
    min_tick_rate: float = 1.0/120.0,  # Some terminals are frame capped, so 60-120hz maybe
):
    # Handles all stdout writing
    # Technically, this is not the ideal orientation for stutterless terminal writing (without sixels)...

    # NOTE:
    # - game_field and raw notes (not the lists view) are live objs, so don't mutate
    #     - We will however be reading them a lot to render prettily
    #     - We will also break encapsulation a bit to analyse note type/props
    # - bps_lines is also a ref, but it should be readonly at game time
    # - TODO: cmod override changes sort and render algorthm, taking in a bps if so?

    # First, clone and sort our local notes view
    # In xmod, we always reflect chart order when rendering, rather than true hit time
    if cmod_override is None:
        render_columns = [
            sorted(column, key=lambda x: x.beat)
            for column in note_columns
        ]

    # But in cmod, we want to reflect the actual game logic by rendering at
    # constant time and explicitly drop unhittable notes
    else:
        raise RuntimeError("Not implemented yet")  # TODO: Remove when ready
        render_columns = [
            sorted(
                (x for x in column if not math.isinf(x.timing)
            ), key=lambda x: x.timing)
            for column in note_columns
        ]

    # We cache our sorted column positions to reduce search to avg O(1) per frame
    # We will move up our cursors when the note is fully offscreen, and search the on-screen window
    render_cursors = [0] * len(note_columns)

    # Similarly, we can also cache our BPS lookups because of this linear search direction
    bps_cursor = 0

    # Other static vars
    hit_line_y = 4  # Offset from screen edge
    spacing = 8 * scroll  # Tuned to create a 8 char beat spacing at scroll 1
    # Due to the limited resolution of a (vertical) terminal, I would not suggest <8 per full beat

    # NOTE: No plans yet to implement dynamic scroll factor
    # Though, it would look like this in frame loop:
    #spacing = 8 * scroll_lines.scroll_at_time(song_time)
    
    # TODO: Set up colours
    # grey for dropped/missed holds
    # orange for 4ths, blue for 8ths, green for all others (for now)
    # red and white for hit displays
    # Consider doing a slight shade for gentle beat flashes
    # Mines could use red on a small sprite

    # For the lack of a threaded curses wrapper, teardown errors manually
    try:
        # Prep terminal
        stdscr = curses.initscr()
        curses.noecho()  # Prevent stdin affecting console
        #stdscr.nodelay(True)  # Non-blocking stdin on getch()  # NOTE: Will only use in ANSI version
        stdscr.keypad(True)  # Consume arrows to prevent weird behaviours
        #print("\x1b[?7l")  # Disable line wrap (truncates)  # TODO: check if this even works for curses

        # TODO: Ensure all threads are fully loaded before starting
        stdscr.addstr(0, 0, "loading")
        stdscr.refresh()
        event = threading.Event()
        #event.wait(1)

        while keep_running.state:
            # If the user resizes, prefer to drop the frame rather than crash
            try:
                # Clear last frame
                stdscr.erase()  # Presumably an optimised but maybe imperfect clear?

                # Grab latest game states (other than notes)
                judgement_counts, last_judgement = game_field.get_game_state()  # TODO: Will render summary counts later
                song_time = playback.curr_pos + offset if playback.active else offset

                # Calculate frame-specific vars
                song_beat, bps_cursor = bps_lines.beat_at_time(
                    song_time,
                    last_line_index=bps_cursor,
                    allow_stop=True,
                    allow_warp=True,
                )
                r, c = stdscr.getmaxyx()

                # Render hit line
                stdscr.addstr(hit_line_y, 0, "-" * c)
                
                # NOTE: Assuming upscroll, 5 char width columns, no col spacing

                # Render measure/beat lines
                i = 0
                measure_beat = (4 - song_beat % 4) // 1
                beat_offset = 1 - song_beat % 1
                beat_y = round(spacing * (beat_offset + i)) + hit_line_y
                while beat_y < r - 1:
                    if i % 4 == measure_beat:
                        stdscr.addstr(beat_y, 0, "-----" * len(render_columns))
                    else:
                        stdscr.addstr(beat_y, 0, "  -  " * len(render_columns))
                    i += 1
                    beat_y = round(spacing * (beat_offset + i)) + hit_line_y

                # Render all on-screen notes
                for col_i, column in enumerate(note_columns):
                    # Notes now store their own hit state
                    # gamefield here is only required from the summary info (which we will leave until later)

                    # Now render every note in the column
                    # A note column is fully rendered once it goes offscreen (due to sort) or exhausted
                    note_x = col_i * 5
                    note_i = render_cursors[col_i]  # Start from last cache
                    first_renderable = None  # Determines where to move the cache to next loop, accounting for long notes
                    on_screen = True
                    
                    while on_screen and note_i < len(column):
                        note = column[note_i]
                        note_y = -round(spacing * (song_beat - note.beat)) + hit_line_y
                        note_judgement = note.judgement  # Snapshot

                        stdscr.addstr(12, 40, f"{last_judgement}"[10:])
                        
                        # Note hasn't arrived yet, so column is fully rendered
                        if note_y >= r-1:
                            on_screen = False

                        # Special case: Keep long notes rendered until fully past
                        elif isinstance(note, HoldNote):
                            tail_y = -round(spacing * (song_beat - note.tail_beat)) + hit_line_y

                            # Still on field, so keep cursor at most here
                            if note_y < r-1 and tail_y >= 0 and note_judgement != Judgement.OK:
                                if first_renderable is None:
                                    first_renderable = note_i

                                # Render the body first (with user feedback)
                                body_i = max(note_y, 0)
                                max_tail_y = min(tail_y, r-1)
                                if note_judgement is not None:
                                    note_str = " - "  # Drop/miss
                                elif (song_time - note.last_held) < 0.05:
                                    # 50ms should work fine unless game polls roughly <1/20?
                                    note_str = "|||"
                                else:
                                    note_str = " | "

                                while body_i < max_tail_y:
                                    stdscr.addstr(body_i, note_x + 1, note_str)
                                    body_i += 1

                                # Then render the head and body if on screen
                                if max_tail_y == tail_y:
                                    stdscr.addstr(max_tail_y, note_x, "[===]")

                                if note_y >= 0:
                                    stdscr.addstr(note_y, note_x, "[===]")

                        elif isinstance(note, RollNote):
                            tail_y = -round(spacing * (song_beat - note.tail_beat)) + hit_line_y

                            # Still on field, so keep cursor at most here
                            if note_y < r-1 and tail_y >= 0 and note_judgement != Judgement.OK:
                                if first_renderable is None:
                                    first_renderable = note_i

                                # Render the body first (with user feedback)
                                body_i = max(note_y, 0)
                                max_tail_y = min(tail_y, r-1)
                                if note_judgement is not None:
                                    note_str = " - "  # Drop/miss
                                elif (song_time - note.last_held) <= 0.15:
                                    note_str = "<W>"
                                else:
                                    note_str = " W "

                                while body_i < max_tail_y:
                                    stdscr.addstr(body_i, note_x + 1, note_str)
                                    body_i += 1

                                # Then render the head and body if on screen
                                if max_tail_y == tail_y:
                                    stdscr.addstr(max_tail_y, note_x, "<<->>")

                                if note_y >= 0:
                                    stdscr.addstr(note_y, note_x, "<<->>")

                        # On-field and still pending scoring or a miss
                        elif note_y >= 0 and (
                            note_judgement is None or
                            note_judgement in (Judgement.MISS, Judgement.NG)
                        ):
                            if first_renderable is None:
                                first_renderable = note_i

                            #note_miss = note_judgement is not None
                            # TODO: grey out misses later
                            # Or maybe grey out purely relative to hitline?
                            # Not sure if BOO will be included?

                            # TODO: need to render a sprite based on type and column
                            # Some sort of get_note_skin(note) np char patch cropped and pasted to a canvas
                            # as a mini factory injected with a noteskin, this will analyse the note's key/subtype/states to render the whole note
                            # Presumably, it will also need to return the new coords for corner-wise pasting

                            # For now, just render the head/tail as single rows
                            # TODO: Paint notes
                            note_str = "  X  " if isinstance(note, MineNote) else "[===]"
                            stdscr.addstr(note_y, note_x, note_str)

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

                # TODO: Once we implement complex note skins, move to this buffered array approach
                # Paint to a temporary canvas to minimise bounds checking before every write
                # To be on the safe side, reserve the last col for newline char
                #c, r = curses.getmaxyx()
                #canvas = np.full((r, c), " ", dtype="|S1")  # ASCII char array
                #canvas[c-1]
                # np nd-slicing makes cropping marginally easier than list[str] and pasting much easier

                # Finally, crop to terminal
                # try needed because interruptable, skipping frame update

                stdscr.move(r-1, c-1)
                stdscr.refresh()
                event.wait(min_tick_rate)
            except curses.error:
                # Window overflow due to user resize or tiny window
                pass  # Drop the frame and continue

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


if __name__ == "__main__":
    if not 2 <= len(sys.argv) <= 3:
        print("Expected 1-2 args, got {len(sys.argv) - 1}.")
        print("Format: <script> <song_dir> [scroll_factor]")
        # TODO: Probably want to name these later via argparse
    else:
        # Parse file
        chart_dir = sys.argv[1]
        all_files = [
            f
            for f in os.listdir(chart_dir)
            if os.path.isfile(os.path.join(chart_dir, f)) and f.endswith(".sm")
        ]
        if len(all_files) != 1:
            raise FileNotFoundError("Found 0 or 2+ SM files. Note that SCC is not yet supported.")

        file_path = os.path.join(chart_dir, all_files[0])

        # Parse scroll factor (if available)
        scroll = float(sys.argv[2]) if len(sys.argv) == 3 else 1  # May ValueError

        # Setup up read-only data
        reader = SMReader()
        bps_lines = reader.read_bps_lines(file_path)
        note_columns = reader.read_notes(file_path, 0, bps_lines)
        music_file = os.path.join(chart_dir, reader.read_music_path(file_path))
        offset = reader.read_offset(file_path)

        # TODO: allow nk depending on what mappings are found in config (or passed in?)
        if len(note_columns) != 4:
            raise ValueError("Only 4K is currently supported.")

        # NOTE: Will not support keysounds atm

        # Set up thread-safe objs used for coordinating game state
        keep_running = Flag(True)  # Signaller for safe thread teardown
        playback = Playback(music_file)  # Audio engine (time sync, pausing, etc)
        game_field = GameField(note_columns)  # Manages in-progress game scoring
        game_keys = GameKeys(["d", "f", "j", "k"])  # Various key state management
        # TODO: Presumably, key mapping gets generated from config based on note_columns size or on load later?

        try:
            # Set up threaded input listeners
            listener = pynput.keyboard.Listener(
                on_press=init_on_press(
                    keep_running,
                    playback,
                    offset,
                    game_field,
                    game_keys,
                ),
                on_release=init_on_release(
                    playback,
                    offset,
                    game_field,
                    game_keys
                ),
            )
            listener.start()

            # Set up game thread
            game_thread = threading.Thread(target=game_logic, args=(
                keep_running,
                playback,
                offset,
                game_field,
                game_keys,
            ))
            game_thread.start()

            # Run render on main thread, so we can shutdown safely
            render(
                keep_running,
                playback,
                offset,
                game_field,
                bps_lines,
                note_columns,
                scroll=scroll,
            )
        except KeyboardInterrupt:
            # Seems that SIGKILL hits the main thread first, hence catching fails in other threads?
            keep_running.state = False
        except:
            # For crashes in hefty render routine, try close other threads gracefully before returning error
            # Well, try anyways
            keep_running.state = False
            game_thread.join()
            listener.stop()
            raise
            

        # Teardown (indirectly triggered by keep_running flag)
        game_thread.join()
        listener.stop()

        # TODO: I assume then it would continue to block input for a moment (somehow)
        # to inform user of game end, then redirect to home to dump results?
        # May have to do this in render thread, which means converting Flag into a multi valued end state?

