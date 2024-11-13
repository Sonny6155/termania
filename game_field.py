import threading

from judgement import Judgement
from note import Note  #, TapNote, HoldNote


class GameField:
    # Thread-safe storage of game progress and simplified scoreboard
    # By forcing events to pass through this common interface, we can track
    # the current note to hit and better synchronise note states
    # That said, other threads are free to read the underlying notes whenever
    
    # TODO: Should have a way of resetting every note type, so that we can replay from any position?
    # Not sure about the use case yet, but def possible

    def __init__(self, note_columns: list[list[Note]]):
        # A single lock should be enough for these inter-related vars
        self.__game_lock = threading.Lock()

        # Mark head note as "next to operate on"
        self.__note_cursors = [0] * len(note_columns)

        # Clone, sort, and drop unhittable notes
        # We only clone the view, so that notes remain direct refs
        self.__note_columns = [
            sorted(column, key=lambda x: x.timing)
            for column in note_columns
        ]

        # Scoring stuff
        self.__judgement_counts = {
            Judgement.MARVELOUS: 0,
            Judgement.PERFECT: 0,
            Judgement.GREAT: 0,
            Judgement.GOOD: 0,
            Judgement.BOO: 0,
            Judgement.MISS: 0,
            Judgement.OK: 0,
            Judgement.NG: 0,
        }

        self.__last_judgement = None
        # TODO: May consider a "last_judgement_time" for renderer to decay visual?

        # NOTE: Wont do DP scoring just yet (will probably require some injected callback magic)
        # Might also wanna keep a list of last hits for more powerful user feedback?

    # Game event handlers (may be triggered by user input or game polling)
    # Notes handle their own statefulness (e.g. hold decay) and when/how to score
    # We only control synchronisation of the triggerable actions

    def press_key(self, key_index: int, song_time: float) -> None:
        # Attempt to press the head note, then score + iter if ready
        with self.__game_lock:
            column = self.__note_columns[key_index]
            note_index = self.__note_cursors[key_index]

            if note_index < len(column):
                judgement = column[note_index].press(song_time)
                if judgement is not None:
                    self.__judgement_counts[judgement] += 1
                    self.__last_judgement = judgement
                    self.__note_cursors[key_index] += 1
                    
    def release_key(self, key_index: int, song_time: float) -> None:
        # Attempt to release the head note, then score + iter if ready
        with self.__game_lock:
            column = self.__note_columns[key_index]
            note_index = self.__note_cursors[key_index]

            if note_index < len(column):
                judgement = column[note_index].release(song_time)
                if judgement is not None:
                    self.__judgement_counts[judgement] += 1
                    self.__last_judgement = judgement
                    self.__note_cursors[key_index] += 1

    def poll(self, song_time: float, held: list[bool]) -> bool:
        # Trigger an update on head notes, scoring any done notes
        # Also returns if chart is fully complete
        with self.__game_lock:
            chart_complete = True

            for i in range(len(self.__note_columns)):
                checking_column = True
                while checking_column and self.__note_cursors[i] < len(self.__note_columns[i]):
                    note = self.__note_columns[i][self.__note_cursors[i]]
                    judgement = note.poll(song_time, held[i])
                    chart_complete = False

                    if judgement is None:
                        # Done assessing this column
                        checking_column = False
                    else:
                        # Update scoreboard and check next
                        self.__judgement_counts[judgement] += 1
                        self.__last_judgement = judgement
                        self.__note_cursors[i] += 1

            return chart_complete

    def get_game_state(self) -> tuple[dict[Judgement, int], Judgement | None]:
        # Take a snapshot of the current game state for rendering
        # Currently just the summary info, because post-game analysis uses notes themselves
        with self.__game_lock:
            return self.__judgement_counts.copy(), self.__last_judgement

