import math


class BPSLines:
    # This stores a piecewise beat over time function and provides various lookup functions
    # The axes are flipped compared to Stepmania's internal handling
    # This enables true negative BPMs for a rollback effect

    # Beats follow SM convention of fixed 4/4, where each measure assumes 4 beats
    # This means "beat" can be fractional if the measure is non-crochet
    # Notes must be rendered relative to beat to enable fancy warping
    # behaviour, though time info could help implement cmod

    def __init__(self):
        # Start uninitialised to allow reading from different formats
        self.__bps_lines = []

    def __regularise_lines(
        self,
        lines: list[tuple[float, float, float]]
    ) -> list[tuple[float, float, float]]:
        # While negative BPM is supported, negative times make no sense
        # SM searches increasing beats instead of time, so -BPM produces -time
        # To regularise with respect to time, crop out dupe times and replace
        # them with a bridging inf BPS line. This simulates SM's time warp.

        # NOTE: Technically might not need this depending how notes are queued

        new_lines = []
        i = 0
        while i < len(lines):
            if i+1 < len(lines) and lines[i+1][0] < lines[i][0]:
                # Skip forward to the line where time would resume, if any
                resume_time, old_beat, _ = lines[i]
                while i+1 < len(lines) and lines[i+1][0] < resume_time:
                    i += 1

                if i < len(lines):  # Only if not exhausted
                    # Find the new start coords from the resuming time
                    # y = m(x - a) + c
                    curr_time, curr_beat, curr_bps = lines[i]
                    new_start_beat = curr_bps * (resume_time - curr_time) + curr_beat

                    # To fix discontinuous jumps to breaking beat-to-time lookup,
                    # bridge gap from old to new with an "infinite bpm" line
                    new_lines.append(
                        (resume_time, old_beat, float("inf"))
                    )

                    # Finally, append real line and continue
                    new_lines.append(
                        (resume_time, new_start_beat, curr_bps)
                    )
                    i += 1
            else:
                new_lines.append(lines[i])
                i += 1

        # TODO: Probably want a procedure here to merge dupe inf BPS lines
        # But out of scope for now since it still works

        return new_lines

    def init_beat_bpms(self, bpm_changes: list[tuple[float, float]]) -> None:
        # Input takes form [(beat, bpm), ...], common to MSD formats
        # These formats interpret negative BPMs as backwards in time, not beat
        # That means that true negative BPMs are not supported
        # Instead, duplicate times should be cropped out to fit new format

        # Fast fail
        if len(bpm_changes) < 0:
            raise ValueError("Must have at least one BPM.")
        elif any(
            bpm_changes[i][0] >= bpm_changes[i+1][0]
            for i in range(len(bpm_changes) - 1)
        ):
            raise ValueError("All beats must be in strictly ascending order.")
        elif any(bpm == 0 for _, bpm in bpm_changes):
            # Would imply infinite runtime in SM handling
            raise ValueError("SM formats cannot support 0 BPMs without duration. Use stops instead.")
            # Will still allow tiny BPMs, though
        elif bpm_changes[0][0] != 0.0:
            # First entry was not (interpreted as) beat 0, time 0
            raise ValueError("First BPM did not start at b0/t0.")

        # Pre-compute and append start times (seconds) of each line
        # Notice that beat and bpm/60 resembles c and m in y = m(x - a) + c
        # Since the next y and m is always known in SM, to solve for next x:
        # - Sub (a,c,m) with the old (x,y,m)
        # - Insert target y to y
        # - Solve for x = (y - c) / m + a
        # - Store (x,y) and next m into (a,c,m)

        raw_bps_lines = []
        raw_bps_lines.append((0.0, 0.0, bpm_changes[0][1] / 60))

        # If multi-BPM chart, try end-to-end stitching in SM-style
        for line_beat, line_bpm in bpm_changes[1:]:
            # Derive start time of this new line from prev line and curr known
            prev_time, prev_beat, prev_bps = raw_bps_lines[-1]

            # Division should be mostly safe due to 0 BPM guard
            raw_bps_lines.append(
                (
                    (line_beat - prev_beat) / prev_bps + prev_time,
                    line_beat,
                    line_bpm / 60,
                )
            )

        bps_lines = self.__regularise_lines(raw_bps_lines)

        # Finally, double-check that chart was not pruned down to 0 seconds
        if len(bps_lines) == 0:
            raise ValueError("No valid BPM line.")

        self.__bps_lines = bps_lines


    #def init_time_bpms(self, bpm_changes: list[tuple[float, float]]) -> None:
        # allows true negative beat rollback
        # don't need negative time handling anymore

        # assume it takes in as time=bpm?
        # stitch each line end-to-end by attaching full xy start info
        # bpms will be done by time
        # stops will just be time=0bpm

    def add_beat_stops(self, stops: list[tuple[float, float]]) -> None:
        # Input takes form [(beat, bpm), ...], common to MSD formats
        # This adds 0 BPM lines, potentially subdividing an existing line
        # Accepts negative stops

        # Validate that all stops can be resolved
        if self.__bps_lines[-1][2] <= 0 and max(x[0] for x in stops) > max(x[1] for x in self.__bps_lines):
            raise ValueError("Stop beats must be between 0 to max BPSLines beat.")

        # Ensure the input is a sorted stack, merging duplicates
        stops_stack = []
        last_beat = -1
        for stop in sorted(stops, key=lambda x: x[0], reverse=True):
            if stop[0] != last_beat:
                stops_stack.append(stop)
                last_beat = stop

        # Start building new line set
        new_lines = []
        running_time_offset = 0
        for line_i in range(len(self.__bps_lines)):
            # Before adding any stops, add this line first
            # Make sure to add collective offsets from previous stops
            line_time, line_beat, line_bps = self.__bps_lines[line_i]
            new_lines.append(
                (
                    line_time + running_time_offset,
                    line_beat,
                    line_bps,
                )
            )

            # Find the endpoint
            if line_i < len(self.__bps_lines) - 1:
                # Not on last line
                line_end_time, line_end_beat, _ = self.__bps_lines[line_i + 1]
            else:
                # Won't need end time for this iter
                line_end_beat = float("inf")  # Works with correct validation

            # Add all stops strictly before the line end via splitting and x-shifting
            while len(stops_stack) > 0 and line_end_beat > stops_stack[-1][0]:
                # Solve for the split point on the original line, then add running offset
                # x = (y - c) / m + a without offset
                new_beat, stop_duration = stops_stack.pop()

                if math.isinf(line_bps):
                    new_time = line_time
                else:
                    new_time = (new_beat - line_beat) / line_bps + line_time

                # Add the bridging line
                new_lines.append(
                    (
                        new_time + running_time_offset,
                        new_beat,
                        0,
                    )
                )

                # Add the new line and update collective offset
                running_time_offset += stop_duration
                new_lines.append(
                    (
                        new_time + running_time_offset,
                        new_beat,
                        line_bps,
                    )
                )

            # Just before next iter, handle stops that are exactly between lines
            # NOTE: Due to the merging step, we don't need to loop
            if len(stops_stack) > 0 and line_end_beat == stops_stack[-1][0]:
                # Only need to add the bridge in this iter
                new_lines.append(
                    (
                        line_end_time + running_time_offset,
                        line_end_beat,
                        0,
                    )
                )

                # Remember to update offset
                running_time_offset += stops_stack.pop()[1]

        # Finally, handle any negative stops
        regular_lines = self.__regularise_lines(new_lines)

        # Finally, double-check that chart was not pruned down to 0 seconds
        if len(regular_lines) == 0:
            raise ValueError("No valid BPM line.")

        self.__bps_lines = regular_lines


    #def add_time_stops(stops: list[tuple[float, float]]) -> None:
        #augment existing bpms with stops by shifting them back in time
        #in each gap, add 0 bpm lines


    def beat_at_time(
        self,
        song_time: float,
        last_line_index: int = 0,
        allow_stop: bool = False,
        allow_warp: bool = False,
    ) -> tuple[float, int]:
        # Practically, this is used for xmod rendering
        # If the context caches the last line to restart from, we can achieve
        # avg O(1) where |notes| >> |bps changes|
        # Can be configured to try find a beat for 0 (stop) or inf (warp) BPS

        # Find the line it sits on
        line_cursor = last_line_index  # If given, make use of cache
        while (
            line_cursor + 1 < len(self.__bps_lines) and
            self.__bps_lines[line_cursor + 1][0] < song_time  # Opt to undershoot if exact
        ):
            line_cursor += 1
        # If exhausted, just use the last which we guarantee to be positive BPS

        # Solve for beat (y) on line
        # NOTE: Soft fail with inf so we can still return line_cursor cache
        line_time, line_beat, bps = self.__bps_lines[line_cursor]
        if bps == 0:
            # A beat is definable, but potentially undesired
            return line_beat if allow_stop else float("inf"), line_cursor
        elif math.isinf(bps):
            # Due to the intentional undershooting, this is only possible if forced with bad cache
            # Multiple solutions, so return first if forced
            return line_beat if allow_warp else float("inf"), line_cursor
        else:
            # Normal case: Solve for y = m(x - a) + c 
            return bps * (song_time - line_time) + line_beat, line_cursor

    def time_at_beat(
        self,
        beat: float,
        last_line_index: int = 0,
        allow_stop: bool = False,
        allow_warp: bool = False,
    ) -> tuple[float, int]:
        # Practically, this primes notes as extra info or for cmod rendering
        # Again, cachable for avg O(1)
        # Will soft fail with inf so we can still return line_cursor cache,
        # but can be configured to try find a beat for 0 (stop) or inf (warp) BPS
        # out of range will always return inf

        # Find the line it sits on
        line_cursor = last_line_index  # If given, make use of cache
        while (
            line_cursor + 1 < len(self.__bps_lines) and
            self.__bps_lines[line_cursor + 1][1] < beat  # Opt to undershoot if exact
        ):
            line_cursor += 1
        # If exhausted, just use the last (we guarantee this to be +BPM)

        # Solve for time (x) on line
        line_time, line_beat, bps = self.__bps_lines[line_cursor]

        if line_cursor >= len(self.__bps_lines) and line_bps <= 0 and beat < line_beat:
            # Beat never goes that high due to ending on a -ve/0 BPM
            return float("inf"), line_cursor
        elif bps == 0:
            # Due to the intentional undershooting, this is only possible if forced with bad cache
            # Multiple solutions, so return first if forced
            return line_time if allow_stop else float("inf"), line_cursor
        elif math.isinf(bps):
            # A time is definable, but potentially undesired
            return line_time if allow_warp else float("inf"), line_cursor
        else:
            # Normal case: Solve for x = (y - c) / m + a
            return (beat - line_beat) / bps + line_time, line_cursor

    @property
    def bps_lines(self) -> list[tuple[int, int, int]]:
        # Return clone
        # TODO: May remove this once debugging is done
        return [t for t in self.__bps_lines]


# Temporary test functions
def test_parse_msd_bpms():
    bps_lines = BPSLines()

    print("Test that building in SM-style works")
    bps_lines.init_beat_bpms([
        # (start_beat, bpm) -> (start_time, start_beat, bps)
        (0, 180),  # (0, 0, 3)
        (6, 240),  # (2, 6, 4)
        (14, -180),  # (4, 14, -3), skipped
        (20, 120),  # Original (2, 20, 2), resumes at (4, 24, 2)
    ])
    print(bps_lines.bps_lines)
    print(bps_lines.beat_at_time(0))  # 0
    print(bps_lines.beat_at_time(2))  # 6
    print(bps_lines.beat_at_time(3))  # 10
    print(bps_lines.beat_at_time(4))  # 14, succeeds to undershooting
    print(bps_lines.time_at_beat(6))  # 2
    print(bps_lines.time_at_beat(10))  # 3
    print(bps_lines.time_at_beat(14))  # 4, succeeds to undershooting
    print(bps_lines.time_at_beat(16))  # On skipped line -> inf bpm -> returns fail via inf
    
    print("Test that time is still definable on warped beats if forced")
    print(bps_lines.time_at_beat(16))  # inf
    print(bps_lines.time_at_beat(16, allow_warp=True))  # Picks first, so 4
    # TODO: Test case for beat_at_time on warp should pick first, but only possible with intentional cache

    # TODO: Same tests on stops

    # Can't test negative end with SM files

    # Check that cancelling stops should work but that the regularised stop lines were indeed built
    print("Testing that stops work, can be regularise, and can cancel out")
    print(bps_lines.beat_at_time(10))  # 36
    print(bps_lines.time_at_beat(36))  # 10
    bps_lines.add_beat_stops([
        (24, 2),  # Shifts to (6, 24, 2), exactly on a split
        (26, -2),  # Split and shifts to (5, 26, 2), resumes at (7, 30, 2)
    ])
    print(bps_lines.bps_lines)
    print(bps_lines.beat_at_time(10))  # 36
    print(bps_lines.time_at_beat(36))  # 10

    print("Testing shifting everything with a stop")
    bps_lines.add_beat_stops([
        (0, 2),  # Resumes at (6, 24, 2), exactly on a split
    ])
    print(bps_lines.bps_lines)
    print(bps_lines.beat_at_time(10))  # 32, due to x shift
    print(bps_lines.time_at_beat(36))  # 12

    # Other tests
    print("Other tests")
    bps_lines.init_beat_bpms([
        # (start_beat, bpm) -> (start_time, start_beat, bps)
        (0, 180),  # (0, 0, 3)
        (6, 240),  # (2, 6, 4)
        (14, -180),  # (4, 14, -3), skipped
        (15, -60),  # (3.66667, 15, -1), skipped
        (16, 30),  # (2.66667, 16, 0.5), resumes at (4, 16.66667, 0.5)
        (17, 120),  # (4.66667, 17, 2) 
    ])
    print(bps_lines.bps_lines)

    bps_lines.init_beat_bpms([
        # (start_beat, bpm) -> (start_time, start_beat, bps)
        (0, 180),  # (0, 0, 3)
        (6, 240),  # (2, 6, 4)
        (14, -180),  # (4, 14, -3), skipped
        (15, -30),  # (3.66667, 15, -1), skipped
        (16, 60),  # (2.66667, 16, 0.5), skipped
        (17, 120),  # (4.66667, 17, 2), resumes at (4, 19.66667, 2)
    ])
    print(bps_lines.bps_lines)
    # TODO: Move these out and assert these formally in pytest


if __name__ == "__main__":
    test_parse_msd_bpms()

