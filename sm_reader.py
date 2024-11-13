#import math

from msdparser import parse_msd, MSDParameter

from bps_lines import BPSLines
from note import Note, TapNote, HoldNote, RollNote, MineNote


class SMReader:
    # TODO: Inherit from some file reader interface?
    # not sure if all files share the same exact func signatures...
    # SCC is known to store some data separately from notes, etc
    # some formats might store bpms per chart?

    # Collection of SM file parsing/building procedures

    def read_bps_lines(self, file_path: str) -> BPSLines:
        # Extract and clean BPMs and stops
        bpm_changes = None
        stops = None

        # Comma-separated "Beat=BPM" -> list of float tuples
        # Ditto for "Beat=Seconds" stops
        with open(file_path, "r") as f:
            for param in parse_msd(file=f):
                if param.key.strip() == "BPMS":
                    # May raise ValueError if not float parsable
                    bpm_changes = [
                        tuple(float(x) for x in bpm_change.strip().split("=", 1))
                        for bpm_change in param.value.split(",")
                    ]
                elif param.key.strip() == "STOPS":
                    # May raise ValueError if not float parsable
                    stops = [
                        tuple(float(x) for x in stop.strip().split("=", 1))
                        for stop in param.value.split(",")
                    ]
                elif bpm_changes is not None and stops is not None:
                    # Short circuit when completed
                    break

        if bpm_changes is None:
            raise ValueError("BPM(s) not found.")

        # Convert BPMs into beat over seconds function for lookup
        bps_lines = BPSLines()
        bps_lines.init_beat_bpms(bpm_changes)

        # Add stops to bps_lines if found
        if stops is not None:
            bps_lines.add_beat_stops(stops)

        return bps_lines

    def read_notes(self, file_path: str, chart_index: int, bps_lines: BPSLines) -> list[Note]:
        # Extract and clean notes
        note_data = None
        with open(file_path, "r") as f:
            i = 0
            for param in parse_msd(file=f):
                if param.key.strip() == "NOTES":
                    if i < chart_index:
                        i += 1
                    else:
                        note_data = [
                            [row for row in measure.strip().split()]
                            for measure in param.components[-1].split(",")
                        ]
                        break
 
        if note_data is None:
            raise ValueError("Chart index not found in file.")
        elif len(note_data) == 0 or len(note_data[0]) == 0:
            raise ValueError("No notes in chart.")

        # Generate notes row by row
        # Will be much stricter than actual SM logic
        key_count = len(note_data[0][0])  # For validation
        note_columns = [[] for _ in range(key_count)]  # FIX: Was duping ref
        held_params = [None] * key_count  # Buffer held notes
        bps_line_cursor = 0  # Cache for minor asymptotic improvement

        for measure_i, measure in enumerate(note_data):
            for row_i, row in enumerate(measure):
                if len(row) != key_count:
                    raise ValueError("Detected inconsistent key count per beat.")

                # Lookup corresponding time in seconds (and update cached pos)
                # NOTE: Unhittable (warped notes will be marked as inf time
                row_beat = (measure_i * 4) + (row_i * 4 / len(measure))  # May ZeroDivisionError
                row_time, bps_line_cursor = bps_lines.time_at_beat(
                    row_beat,
                    bps_line_cursor,
                    allow_stop=True,
                    allow_warp=False,
                )

                for key_i, key in enumerate(row):
                    if key == "1":  # Tap
                        if held_params[key_i] is not None:
                            raise ValueError("Detected tap overlapping a hold/roll on key {key_i}.")

                        note_columns[key_i].append(TapNote(
                            key_i,
                            row_time,
                            row_beat,
                            row_i,
                            len(measure),
                        ))
                    elif key == "2":  # Hold start
                        if held_params[key_i] is not None:
                            raise ValueError("Detected hold start overlapping a hold/roll on key {key_i}.")

                        held_params[key_i] = (
                            HoldNote,  # Now that's just janky
                            key_i,
                            row_time,
                            row_beat,
                            row_i,
                            len(measure),
                        )
                    elif key == "4":  # Roll start
                        if held_params[key_i] is not None:
                            raise ValueError("Detected roll start overlapping a hold/roll on key {key_i}.")

                        held_params[key_i] = (
                            RollNote,
                            key_i,
                            row_time,
                            row_beat,
                            row_i,
                            len(measure),
                        )
                    elif key == "3":  # Hold/roll end
                        if held_params[key_i] is None:
                            raise ValueError(f"Detected hold/roll end when none started on key {key_i}.")

                        note_columns[key_i].append(held_params[key_i][0](
                            *held_params[key_i][1:],
                            row_time,
                            row_beat,
                            row_i,
                            len(measure),
                        ))
                        held_params[key_i] = None
                    elif key in "ML":  # Mine
                        # For now, treat lifts as mines
                        note_columns[key_i].append(MineNote(
                            key_i,
                            row_time,
                            row_beat,
                            row_i,
                            len(measure),
                        ))
                    elif key != "0":
                        # Reject if parsing is not strict
                        # Also reject auto keysounds, keysounded notes, fakes or multiplayer
                        raise ValueError("Detected unsupported note type.")

        # SMs should already be in beat-sorted order if parsed by row
        # Unhittable, warped notes are identifiable as inf time
        # Most likely, renderer keeps all notes unless cmod, while logic drops unhittable...
        return note_columns

    def read_music_path(self, file_path: str) -> str:
        with open(file_path, "r") as f:
            for param in parse_msd(file=f):
                if param.key.strip() == "MUSIC":
                    return param.value.strip()
        raise ValueError("Music path not found.")

    def read_offset(self, file_path: str) -> float:
        with open(file_path, "r") as f:
            for param in parse_msd(file=f):
                if param.key.strip() == "OFFSET":
                    # May raise ValueError if not float parsable
                    return float(param.value.strip())
        raise ValueError("Offset not found.")


if __name__ == "__main__":
    # Read from real file with stops
    file_path = "Cloudless/Cloudless.sm"
    reader = SMReader()
    bps_lines = reader.read_bps_lines(file_path)
    note_columns = reader.read_notes(file_path, 0, bps_lines)

    print(bps_lines.bps_lines)
    for col, notes in enumerate(note_columns):
        print(f"Col {col}:")
        for note in notes:
            print(f"t: {note.timing:.2f}, b: {note.beat:.2f}, measure_frac: {note.measure_fraction}, type: {type(note)}{', duration: ' + str(note.tail_timing - note.timing) if isinstance(note, HoldNote) else ''}")

        #    print(f"{note.timing:.2f}")
        #for note in notes:
        #    print(f"{note.beat:.2f}")

    print(reader.read_music_path(file_path))
    print(reader.read_offset(file_path))

