import threading
from abc import ABC, abstractmethod

from judgement import Judgement


# Notes will store their own judged state (though gamefield handles runtime metrics etc)
# This allows post-game analysis, as well as various rendering decisions
# Though these enums might be atomic and infrequently access, mutex just in case

# There's also no way to reset or quickly copy notes atm...


class Note(ABC):
    # Interface for all note types
    # Helps decoupling (except for rendering etc, which must peek type/details)

    # Instant events
    # Accuracy can be set at anytime, but judgement indicates note completion
    @abstractmethod
    def press(self, song_time: float) -> Judgement | None:
        pass

    @abstractmethod
    def release(self, song_time: float) -> Judgement | None:
        # Only used on lift notes in the future
        # May even remove
        pass

    # Lossy polled events
    @abstractmethod
    def poll(self, song_time: float, held: bool) -> Judgement | None:
        # Triggered each game tick
        # Used for stuff like hold decay or miss detection, etc
        pass

    # Immutable properties
    @property
    @abstractmethod
    def key(self) -> int:
        # Key as index
        pass

    @property
    @abstractmethod
    def timing(self) -> float:
        # True timing in seconds, relative to the song
        pass

    @property
    @abstractmethod
    def beat(self) -> float:
        # Beat relative to the song, assuming constant 4/4
        pass

    @property
    @abstractmethod
    def measure_pos(self) -> int:
        # 0-based, nth beat relative to its measure
        # NOTE: Technically computable, but would require rounding work
        pass

    @property
    @abstractmethod
    def measure_fraction(self) -> int:
        # Denominator of a measure's signature (measure of quavers = 8, etc)
        # NOTE: This can be arbitrary, but SM technically rounds to certain fractions
        pass

    # NOTE: Measure can be computed from beat // 4

    @property
    @abstractmethod
    def judgement(self) -> Judgement | None:
        # Mutexed judgement on note scoring
        pass

    @property
    @abstractmethod
    def accuracy(self) -> float | None:
        # Mutexed judged timing of the head note relative to the song
        # -ve is early, +ve is late, 0 is exact, None is inapplicable or miss
        pass


class TapNote(Note):
    def __init__(
        self,
        key: int,
        timing: float,
        beat: float,
        measure_pos: int,
        measure_fraction: int,
    ):
        self.__key = key
        self.__timing = timing
        self.__beat = beat
        self.__measure_pos = measure_pos
        self.__measure_fraction = measure_fraction

        # Stateful info
        self.__judgement = None
        self.__accuracy = None
        self.__lock = threading.Lock()

    def press(self, song_time: float) -> Judgement | None:
        # For now, hardcode the timing windows to match SM Judge 4
        # Won't return scoring info etc yet, because that gets complex
        # TODO: May want to return exact delta time, because that can be helpful in review
        with self.__lock:
            abs_delta_time = abs(song_time - self.__timing)
            self.__accuracy = (song_time - self.__timing)
            if abs_delta_time <= 0.0225:
                self.__judgement = Judgement.MARVELOUS
            elif abs_delta_time <= 0.045:
                self.__judgement = Judgement.PERFECT
            elif abs_delta_time <= 0.09:
                self.__judgement = Judgement.GREAT
            elif abs_delta_time <= 0.135:
                self.__judgement = Judgement.GOOD
            elif abs_delta_time <= 0.18:
                self.__judgement = Judgement.BOO
            elif song_time > self.__timing:
                self.__accuracy = None
                self.__judgement = Judgement.MISS  # >180ms
            else:
                self.__accuracy = None  # No action if -180ms

            return self.__judgement

    def release(self, song_time: float) -> Judgement | None:
        return None

    def poll(self, song_time: float, held: bool) -> Judgement | None:
        # Ignore hold state. Looking only for key down
        if (song_time - self.__timing) > 0.18:
            with self.__lock:
                self.__judgement = Judgement.MISS  # >180ms
                return Judgement.MISS
        else:
            return None  # No action otherwise

    @property
    def key(self) -> int:
        return self.__key

    @property
    def timing(self) -> float:
        return self.__timing

    @property
    def beat(self) -> float:
        return self.__beat

    @property
    def measure_pos(self) -> int:
        return self.__measure_pos

    @property
    def measure_fraction(self) -> int:
        return self.__measure_fraction

    @property
    def judgement(self) -> Judgement | None:
        with self.__lock:
            return self.__judgement

    @property
    def accuracy(self) -> float | None:
        with self.__lock:
            return self.__accuracy


class HoldNote(Note):
    def __init__(
        self,
        key: int,
        timing: float,
        beat: float,
        measure_pos: int,
        measure_fraction: int,
        tail_timing: float,
        tail_beat: float,
        tail_measure_pos: int,
        tail_measure_fraction: int,
    ):
        self.__key = key
        self.__timing = timing
        self.__beat = beat
        self.__measure_pos = measure_pos
        self.__measure_fraction = measure_fraction

        # Hold/roll-only attributes
        self.__tail_timing = tail_timing
        self.__tail_beat = tail_beat 
        self.__tail_measure_pos = tail_measure_pos
        self.__tail_measure_fraction = tail_measure_fraction

        # Stateful info
        self.__last_held = -1  # Tracks hold decay
        self.__judgement = None
        self.__accuracy = None
        self.__lock = threading.Lock()

        # Derivable states (assuming unscored):
        # - Was held: Last held >= 0
        # - Currently held: Last held no more than 0.25s ago

    def press(self, song_time: float) -> Judgement | None:
        # Each press keeps the note alive
        # Assume context checks/knows if it is already dropped/scored
        # NOTE: For ANSI version, we probably want to check that the key isn't already being held
        # so maybe guard the <-300ms zone?
        with self.__lock:
            delta_time = (song_time - self.__timing)
            if delta_time >= -0.18:
                if self.__accuracy is None:
                    self.__accuracy = delta_time
                self.__last_held = song_time

    def release(self, song_time: float) -> Judgement | None:
        # Since we are currently using decay-based scoring, this does nothing
        return None

    def poll(self, song_time: float, held: bool) -> Judgement | None:
        # Again, context should know if it is already scored
        with self.__lock:
            if self.__last_held >= 0:
                # Held to the end, score immediately
                if song_time >= self.__tail_timing:
                    self.__judgement = Judgement.OK

                # Dropped midway
                elif (song_time - 0.25) > self.__last_held:
                    self.__judgement = Judgement.NG

                # Still being held, so reset unpressed decay timer
                elif held and (song_time - self.__timing) >= -0.18:
                    # Enforces "RequireStepOnHoldHeads"
                    self.__last_held = song_time

            # Missed head entirely
            elif (song_time - self.__timing) > 0.18:
                self.__judgement = Judgement.MISS

            # Potentially no action
            return self.__judgement

    @property
    def key(self) -> int:
        return self.__key

    @property
    def timing(self) -> float:
        return self.__timing

    @property
    def beat(self) -> float:
        return self.__beat

    @property
    def measure_pos(self) -> int:
        return self.__measure_pos

    @property
    def measure_fraction(self) -> int:
        return self.__measure_fraction

    # Hold-only properties
    @property
    def tail_timing(self) -> float:
        return self.__tail_timing

    @property
    def tail_beat(self) -> float:
        return self.__tail_beat

    @property
    def tail_measure_pos(self) -> int:
        return self.__tail_measure_pos

    @property
    def tail_measure_fraction(self) -> int:
        return self.__tail_measure_fraction

    @property
    def last_held(self) -> float:
        with self.__lock:
            return self.__last_held

    @property
    def judgement(self) -> Judgement | None:
        with self.__lock:
            return self.__judgement

    @property
    def accuracy(self) -> float | None:
        with self.__lock:
            return self.__accuracy


class RollNote(Note):
    def __init__(
        self,
        key: int,
        timing: float,
        beat: float,
        measure_pos: int,
        measure_fraction: int,
        tail_timing: float,
        tail_beat: float,
        tail_measure_pos: int,
        tail_measure_fraction: int,
    ):
        self.__key = key
        self.__timing = timing
        self.__beat = beat
        self.__measure_pos = measure_pos
        self.__measure_fraction = measure_fraction

        # Hold/roll-only attributes
        self.__tail_timing = tail_timing
        self.__tail_beat = tail_beat 
        self.__tail_measure_pos = tail_measure_pos
        self.__tail_measure_fraction = tail_measure_fraction

        # Stateful info
        self.__last_held = -1  # Tracks hold decay
        self.__judgement = None
        self.__accuracy = None
        self.__lock = threading.Lock()

        # Similar to holds, but "held" decay only resets on press
        # Additionally, the grace window is larger at 0.5

    def press(self, song_time: float) -> Judgement | None:
        # Each press keeps the note alive
        # Assume context checks/knows if it is already dropped/scored
        # NOTE: For ANSI version, we probably want to check that the key isn't already being held
        # so maybe guard the <-300ms zone?
        with self.__lock:
            delta_time = (song_time - self.__timing)
            if delta_time >= -0.18:
                if self.__accuracy is None:
                    self.__accuracy = delta_time
                self.__last_held = song_time

    def release(self, song_time: float) -> Judgement | None:
        # Since we are currently using decay-based scoring, this does nothing
        return None

    def poll(self, song_time: float, held: bool) -> Judgement | None:
        # Again, context should know if it is already scored
        with self.__lock:
            if self.__last_held >= 0:
                # Held to the end, score immediately
                if song_time >= self.__tail_timing:
                    self.__judgement = Judgement.OK

                # Dropped midway
                elif (song_time - 0.5) > self.__last_held:
                    self.__judgement = Judgement.NG

            # Missed head entirely
            elif (song_time - self.__timing) > 0.18:
                self.__judgement = Judgement.MISS

            # Potentially no action
            return self.__judgement

    @property
    def key(self) -> int:
        return self.__key

    @property
    def timing(self) -> float:
        return self.__timing

    @property
    def beat(self) -> float:
        return self.__beat

    @property
    def measure_pos(self) -> int:
        return self.__measure_pos

    @property
    def measure_fraction(self) -> int:
        return self.__measure_fraction

    # Hold-only properties
    @property
    def tail_timing(self) -> float:
        return self.__tail_timing

    @property
    def tail_beat(self) -> float:
        return self.__tail_beat

    @property
    def tail_measure_pos(self) -> int:
        return self.__tail_measure_pos

    @property
    def tail_measure_fraction(self) -> int:
        return self.__tail_measure_fraction

    @property
    def last_held(self) -> float:
        with self.__lock:
            return self.__last_held

    @property
    def judgement(self) -> Judgement | None:
        with self.__lock:
            return self.__judgement

    @property
    def accuracy(self) -> float | None:
        with self.__lock:
            return self.__accuracy


class MineNote(Note):
    def __init__(
        self,
        key: int,
        timing: float,
        beat: float,
        measure_pos: int,
        measure_fraction: int,
    ):
        self.__key = key
        self.__timing = timing
        self.__beat = beat
        self.__measure_pos = measure_pos
        self.__measure_fraction = measure_fraction

        # Stateful info
        self.__judgement = None
        self.__accuracy = None
        self.__lock = threading.Lock()

    def press(self, song_time: float) -> Judgement | None:
        # Following some tips and testing, 50ms is fine for slower songs
        if abs(song_time - self.__timing) <= 0.05:
            with self.__lock:
                self.__judgement = Judgement.NG
                return Judgement.NG

    def release(self, song_time: float) -> Judgement | None:
        return None

    def poll(self, song_time: float, held: bool) -> Judgement | None:
        if (song_time - self.__timing) > 0.05:
            with self.__lock:
                self.__judgement = Judgement.OK
                return Judgement.OK  # >50ms late
        elif held and abs(song_time - self.__timing) <= 0.05:
            with self.__lock:
                self.__judgement = Judgement.NG
                return Judgement.NG 
        else:
            return None  # No action if <-50mss

    @property
    def key(self) -> int:
        return self.__key

    @property
    def timing(self) -> float:
        return self.__timing

    @property
    def beat(self) -> float:
        return self.__beat

    @property
    def measure_pos(self) -> int:
        return self.__measure_pos

    @property
    def measure_fraction(self) -> int:
        return self.__measure_fraction

    @property
    def judgement(self) -> Judgement | None:
        with self.__lock:
            return self.__judgement

    @property
    def accuracy(self) -> float | None:
        with self.__lock:
            return self.__accuracy

