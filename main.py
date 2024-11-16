import argparse
import os
import threading

import pynput
from just_playback import Playback

from flag import Flag
from game_field import GameField
from render import render
from sm_reader import SMReader


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
# Being event driven, these are much more precise than our regular game ticks
def init_on_press(
    keep_running: Flag,
    playback: Playback,
    offset: float,
    game_field: GameField,
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

        # On game key, trigger note handlers
        elif playback.playing:
            ch = None
            if hasattr(key, "char"):
                ch = key.char
            elif key == pynput.keyboard.Key.space:
                # Accept space, which is commonly used for odd key charts
                ch = " "
                
            if ch is not None and game_keys.char_exists(ch):
                key_index = game_keys.index_of(ch)
                if not game_keys.is_held(key_index):  # Filter autorepeat
                    game_keys.press(key_index)

                    # Pass press event to game obj
                    song_time = playback.curr_pos + offset if playback.active else offset
                    game_field.press_key(key_index, song_time)
        
    return on_press


def init_on_release(
    playback: Playback,
    offset: float, 
    game_field: GameField,
    game_keys: GameKeys,
):
    def on_release(key) -> None:
        # Inherit shared refs from init
        nonlocal playback
        nonlocal game_field
        nonlocal game_keys

        # Reallow key press events if previously held
        if playback.playing:
            ch = None
            if hasattr(key, "char"):
                ch = key.char
            elif key == pynput.keyboard.Key.space:
                ch = " "

            if ch is not None and game_keys.char_exists(ch):
                key_index = game_keys.index_of(ch)
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


def parse_argv() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "path",
        type=str,
        help="Path to chart folder"
    )  # Positionals are inherently required
    parser.add_argument(
        "index",
        type=int,
        nargs="?",
        default=0,
        help="nth note data in the chart file to read",
    )
    parser.add_argument(
        "--cmod",
        dest="xmod",
        action="store_false",
        help="Switches to constant time rendering (ignores speed changes)"
    )
    parser.add_argument(
        "--scroll",
        type=float,
        default=1.0,
        help="Multiplier affecting note spacing and thus speed",
    )
    parser.add_argument(
        "--keys",
        type=str.lower,
        default="dfjk",
        help="Key mapping, as an ordered string of letters/space",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Adds extra song delay (in seconds)",
    )
    args = parser.parse_args()

    # Extra arg validation
    if not all(x.isalpha() or x.isspace() for x in args.keys):
        raise ValueError("Key mapping must only contain ASCII letters or space.")

    return args


if __name__ == "__main__":
    # Parse positionals/kwargs from sys.argv
    args = parse_argv()  # May early exit on validation errors

    # Parse file
    chart_dir = args.path
    all_files = [
        f
        for f in os.listdir(chart_dir)
        if os.path.isfile(os.path.join(chart_dir, f)) and f.endswith(".sm")
    ]
    if len(all_files) != 1:
        raise FileNotFoundError("Found 0 or 2+ SM files. Note that SCC is not yet supported.")

    file_path = os.path.join(chart_dir, all_files[0])

    # Setup up read-only data
    reader = SMReader()
    bps_lines = reader.read_bps_lines(file_path)
    note_columns = reader.read_notes(file_path, args.index, bps_lines)
    music_file = os.path.join(chart_dir, reader.read_music_path(file_path))
    offset = reader.read_offset(file_path) + args.offset

    if len(note_columns) != len(args.keys):
        raise ValueError(f"Key mapping mismatch. Chart uses {len(note_columns)} keys, but given keys have {len(args.keys)}.")

    # NOTE: Will not support keysounds atm

    # Set up thread-safe objs used for coordinating game state
    keep_running = Flag(True)  # Signaller for safe thread teardown
    playback = Playback(music_file)  # Audio engine (time sync, pausing, etc)
    game_field = GameField(note_columns)  # Manages in-progress game scoring
    game_keys = GameKeys(list(args.keys))  # Various key state management

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
            xmod=args.xmod,
            scroll=args.scroll,
        )
    except KeyboardInterrupt:
        # Seems that Ctrl+C hits the main thread first, hence catching fails in other threads?
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

    # Errors in listener seems to be hard to handle safely?

    # TODO: I assume then it would continue to block input for a moment (somehow)
    # to inform user of game end, then redirect to home to dump results?
    # May have to do this in render thread, which means converting Flag into a multi valued end state?

    # Temporary solution: Dump to main screen
    print("Results:")
    for k, v in game_field.get_metrics()[0].items():
        # Works fine since python dicts are ordered
        print(f"{k}: {v}")

