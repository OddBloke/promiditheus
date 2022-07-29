import argparse
import logging
import shutil
import textwrap
import time
from datetime import datetime
from functools import partial
from importlib import resources
from typing import Any, Optional

import confuse
import mido
import requests
import music21
import yaml


WIDTH = min(100, shutil.get_terminal_size().columns - 2)
FORMATTER_CLASS = partial(argparse.RawDescriptionHelpFormatter, width=WIDTH)


class Instrument:
    def __init__(
        self,
        name: str,
        program_number: int,
        pitch_range: (str, str),
        scale: music21.scale.ConcreteScale,
    ):
        self.name = name
        self.program_number = program_number

        self.available_pitches = [pitch for pitch in scale.getPitches(*pitch_range)]

    def clamp(self, value: float) -> music21.pitch.Pitch:
        idx = round((len(self.available_pitches) - 1) * value)
        return self.available_pitches[idx]

    @classmethod
    def from_config(
        cls,
        config: confuse.Configuration,
        instrument_name: str,
        scale: music21.scale.Scale,
    ) -> "Instrument":
        return cls(
            instrument_name, scale=scale, **config["instruments"][instrument_name].get()
        )


class QueryPlayer:
    def __init__(
        self,
        name: str,
        prometheus_host: str,
        replacements: [(str, str)],
        *,
        instrument: Instrument,
        query: str,
        channel: int = 0,
    ):
        self._name = name
        self._channel = channel
        self._instrument = instrument

        self._log = logging.getLogger("{}:{}".format(self._name, self._instrument.name))

        for var, value in replacements:
            query = query.replace(f"${var}", value)
        self._log.info("Calculated query: %s", query.strip())
        self._query = self.QUERY_TEMPLATE.format(
            prometheus_host=prometheus_host, query=query
        )

        self._last_note = None

    def _do_query(self, url: str) -> Any:
        json = requests.get(url, verify=False).json()
        self._log.debug("Prometheus JSON: %s", json)
        result = json["data"]["result"]
        if len(result) > 1:
            self._log.warning("More than 1 result in Prometheus JSON (%d)", len(result))
        return result

    def _get_note_for_value(self, value: float) -> music21.pitch.Pitch:
        self._log.info("Metric value: %s", value)
        note = self._instrument.clamp(float(value))
        self._log.info("Note: %s (%d)", note, note.midi)
        return note

    def _get_messages(
        self, note: music21.pitch.Pitch, *, msg_time: int = 0
    ) -> [mido.Message]:
        msgs = []
        if note != self._last_note:
            off_msg = self._off_message(msg_time=msg_time)
            # delta from last message: 0 if we're sending an off, else msg_time
            on_msg_time = 0 if off_msg else msg_time
            msgs = off_msg + [
                mido.Message(
                    "note_on",
                    channel=self._channel,
                    note=note.midi,
                    velocity=127,
                    time=on_msg_time,
                )
            ]
        self._last_note = note
        return msgs

    def _off_message(self, *, msg_time: int = 0) -> [mido.Message]:
        if self._last_note is not None:
            return [
                mido.Message(
                    "note_off",
                    channel=self._channel,
                    note=self._last_note.midi,
                    time=msg_time,
                )
            ]
        return []

    def _program_change_message(self) -> mido.Message:
        return mido.Message(
            "program_change",
            channel=self._channel,
            program=self._instrument.program_number,
        )


class LiveQueryPlayer(QueryPlayer):
    QUERY_TEMPLATE = "http://{prometheus_host}/api/v1/query?query={query}"

    def __init__(self, port: mido.ports.BaseOutput, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._port = port

        self._log.info(
            "Setting program on channel %s to %s",
            self._channel,
            self._instrument.program_number,
        )
        self._port.send(self._program_change_message())
        self._next_messages = []

    def _get_note(self) -> float:
        result = self._do_query(self._query)
        _timestamp, value = result[0]["value"]
        return self._get_note_for_value(value)

    def off(self):
        for msg in self._off_message():
            self._port.send(msg)

    def prep(self):
        note = self._get_note()
        self._next_messages = self._get_messages(note)

    def tick(self):
        for msg in self._next_messages:
            self._port.send(msg)
        self._next_messages = []


class GenerateQueryPlayer(QueryPlayer):
    QUERY_TEMPLATE = "http://{prometheus_host}/api/v1/query_range?query={query}"

    def generate_track_for_range(
        self, start: int, end: int, step: int, *, factor: int, ticks_per_beat: int
    ) -> mido.MidiTrack:
        query = self._query.strip().replace(
            "/query_range?", f"/query_range?start={start}&end={end}&step={step}&"
        )
        result = self._do_query(query)

        track = mido.MidiTrack()
        track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60)))
        track.append(self._program_change_message())

        def scale_delta(delta: int) -> int:
            return int((delta / factor) * ticks_per_beat)

        last_timestamp = start
        for timestamp, value in result[0]["values"]:
            delta = timestamp - last_timestamp
            note = self._get_note_for_value(value)
            msgs = self._get_messages(note, msg_time=scale_delta(delta))
            if msgs:
                track.extend(msgs)
                last_timestamp = timestamp

        end_delta = end - last_timestamp
        track.extend(self._off_message(msg_time=scale_delta(end_delta)))
        return track


def get_players_from_config(
    config: confuse.Configuration, port: Optional[mido.ports.BaseOutput]
) -> [QueryPlayer]:
    scale_cls = getattr(music21.scale, config["scale"]["class"].get())
    scale = scale_cls(config["scale"]["tonic"].get())
    logging.info("Selected scale: %s", scale.name)
    replacements = [
        replacement.split("=", 1) for replacement in config["cli"]["replacement"].get()
    ]
    players = []
    for channel, (name, player_config) in enumerate(config["queries"].get().items()):
        if port is not None:
            partial_cls = partial(LiveQueryPlayer, port, name)
        else:
            partial_cls = partial(GenerateQueryPlayer, name)
        player_config["instrument"] = Instrument.from_config(
            config, player_config["instrument"], scale
        )
        players.append(
            partial_cls(
                prometheus_host=config["cli"]["prometheus_host"].get(),
                replacements=replacements,
                channel=channel,
                **player_config,
            )
        )
    return players


def open_midi_output(midi_output: Optional[str]) -> mido.ports.BasePort:
    def open_output(name: str, *, virtual: bool):
        logging.info("Opening MIDI output (virtual=%s): %s", virtual, name)
        return mido.open_output(name, virtual=virtual, autoreset=True)

    if midi_output is None:
        # No port specified, create a virtual port for `aconnect` usage
        return open_output("promiditheus", virtual=True)
    try:
        return open_output(midi_output, virtual=False)
    except OSError:
        logging.info("Failed to open output; treating as aconnect ID")
        output_name = None
        for potential_output_name in mido.get_output_names():
            if potential_output_name.endswith(midi_output):
                output_name = potential_output_name
                break
        if output_name is not None:
            return open_output(output_name, virtual=False)
        raise


def instantiate_config(args: argparse.Namespace) -> confuse.Configuration:
    config = confuse.Configuration("promiditheus", __name__)

    # Import builtin configuration
    for fname in ["instruments.yml", "scale.yml"]:
        with resources.path("promiditheus", fname) as config_path:
            config.set_file(config_path)

    # Import specified lead sheet
    config.set_file(args.lead_sheet)

    # Import CLI arguments into cli namespace
    config["cli"].set_args(args)

    return config


class CommonArgs:
    @staticmethod
    def lead_sheet(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "lead_sheet",
            metavar="LEAD-SHEET",
            help=(
                "A YAML file specifying the queries to play, the instruments to use,"
                " and the scale to play in."
            ),
        )

    @staticmethod
    def prometheus_host(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "prometheus_host",
            metavar="PROMETHEUS-HOST",
            help="The Prometheus host to query.",
        )

    @staticmethod
    def replacement(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--replacement",
            action="append",
            default=[],
            help=(
                "Specify key=value replacements for $variables in queries: e.g."
                " 'instance=my-host:9100' will replace '$instance' with 'my-host:9100'."
                " Can be passed multiple times."
            ),
        )


def parse_live_args():
    description = "\n".join(
        textwrap.wrap(
            textwrap.dedent(
                """\
        Connect to PROMETHEUS-HOST and use the queries and instruments in
        LEAD-SHEET to emit corresponding MIDI events to MIDI-OUTPUT (or a
        virtual MIDI port if no output is specified) in real-time.
        """
            ),
            WIDTH,
        )
    )
    parser = argparse.ArgumentParser(
        description=description, formatter_class=FORMATTER_CLASS
    )
    parser.add_argument(
        "--midi-output",
        help=(
            "The MIDI output to connect to: this should be in the format "
            "'<client>:<port>' (e.g. as emitted by `aconnect --list`).  If not given, a"
            " virtual MIDI port will be published, which can then be connected to other"
            " MIDI ports using, e.g., `aconnect`."
        ),
        metavar="MIDI-OUTPUT",
    )
    CommonArgs.replacement(parser)
    CommonArgs.lead_sheet(parser)
    CommonArgs.prometheus_host(parser)
    return parser.parse_args()


def live_main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s"
    )
    args = parse_live_args()
    config = instantiate_config(args)

    port = open_midi_output(config["cli"]["midi_output"].get(None))

    players = get_players_from_config(config, port)

    while True:
        logging.info("Starting loop...")
        start = time.time()
        for player in players:
            try:
                player.prep()
            except (IndexError, requests.exceptions.ConnectionError) as exc:
                # Ignore these errors by falling through to the sleep logic
                player._log.exception("Ignoring occasional error")

        for player in players:
            player.tick()

        # Attempt to reduce drift
        delta = (start + 5) - time.time()
        logging.info("Loop complete; sleeping for %ss", delta)
        time.sleep(delta)


def parse_generate_args():
    description_parts = [
        "\n".join(textwrap.wrap(textwrap.dedent(part).replace("\n", " "), WIDTH))
        for part in [
            """\
        Connect to PROMETHEUS-HOST and use the queries and instruments in
        LEAD-SHEET to write a multi-track MIDI file to OUTPUT-FILE for the
        range of time specified by RANGE.""",
            """\
        By default, this will generate a MIDI file of the same length as the
        given RANGE: specifying `--speed-up-factor` will reduce the length of
        the output file by SPEED-UP-FACTOR, scaling the times of the MIDI
        events accordingly.
        """,
            """\
        Prometheus has a limit on the number of datapoints it will return for a
        query: you will hit this for larger ranges.  Specifying a higher
        PROMETHEUS-STEP will reduce the number of datapoints Prometheus
        generates, avoiding this issue.  For large SPEED-UP-FACTORs, you may
        find that MIDI events are generated too close together, resulting in
        noisy/bouncy MIDI output: specifying a higher PROMETHEUS-STEP will
        increase the time between MIDI events in the output.
        """,
        ]
    ]
    description = "\n\n".join(description_parts)
    parser = argparse.ArgumentParser(
        description=description, formatter_class=FORMATTER_CLASS
    )

    def parse_range(value: str) -> (int, int):
        range_parts = value.split(":")
        if len(range_parts) != 2:
            parser.error(
                "Invalid range specified; must be <start timestamp>:<end timestamp>"
            )
        try:
            return (int(range_parts[0]), int(range_parts[1]))
        except ValueError as exc:
            parser.error(f"Invalid range specified: {exc}")

    now = int(datetime.now().timestamp())

    CommonArgs.replacement(parser)
    parser.add_argument(
        "--range",
        default=(now - 180, now),
        type=parse_range,
        help=(
            "The range to generate MIDI output for, specified as two colon-separated"
            " UNIX timestamps (e.g. '1659101350:1659101410'). Defaults to the last 3"
            " minutes."
        ),
    )
    parser.add_argument(
        "--speed-up-factor",
        type=int,
        default=1,
        help=(
            "The factor by which MIDI events should be sped up relative to real-time:"
            " defaults to 1 (i.e. a 60s range will generate a 60s MIDI file)."
        ),
        metavar="SPEED-UP-FACTOR",
    )
    parser.add_argument(
        "--prometheus-step",
        type=int,
        default=1,
        help=(
            "The value passed to Prometheus as the step parameter of queries.  Use this"
            " to reduce the frequency of MIDI events (particularly useful with high"
            " speed-up factors), or if your queries are requesting too many datapoints."
            "  Defaults to 1."
        ),
        metavar="PROMETHEUS-STEP",
    )
    CommonArgs.lead_sheet(parser)
    CommonArgs.prometheus_host(parser)
    parser.add_argument(
        "output_file",
        metavar="OUTPUT-FILE",
        help="The path to write the output MIDI file to.",
    )
    args = parser.parse_args()
    args.start, args.end = args.range
    return args


def generate_main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s"
    )
    args = parse_generate_args()
    config = instantiate_config(args)

    players = get_players_from_config(config, None)
    midifile = mido.MidiFile()
    for player in players:
        midifile.tracks.append(
            player.generate_track_for_range(
                args.start,
                args.end,
                args.prometheus_step,
                factor=args.speed_up_factor,
                ticks_per_beat=midifile.ticks_per_beat,
            )
        )
    midifile.save(args.output_file)
    logging.info("MIDI file length: %ss", midifile.length)


if __name__ == "__main__":
    live_main()
