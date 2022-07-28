import argparse
import logging
import time
from typing import Optional

import mido
import requests
import music21
import yaml


QUERY_TEMPLATE = "http://{prometheus_host}/api/v1/query?query={query}"


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


class QueryPlayer:
    def __init__(
        self,
        port: mido.ports.BaseOutput,
        name: str,
        instruments: [Instrument],
        prometheus_host: str,
        replacements: [(str, str)],
        *,
        instrument: str,
        query: str,
        channel: int = 0,
    ):
        self._port = port
        self._name = name
        self._channel = channel
        self._instrument = instruments[instrument]

        self._log = logging.getLogger("{}:{}".format(self._name, self._instrument.name))

        for var, value in replacements:
            query = query.replace(f"${var}", value)
        self._log.info("Calculated query: %s", query.strip())
        self._query = QUERY_TEMPLATE.format(
            prometheus_host=prometheus_host, query=query
        )

        self._log.info(
            "Setting program on channel %s to %s",
            self._channel,
            self._instrument.program_number,
        )
        self._port.send(
            mido.Message(
                "program_change",
                channel=self._channel,
                program=self._instrument.program_number,
            )
        )

        self._last_note = None
        self._next_messages = []

    def _get_messages(self, note: music21.pitch.Pitch) -> [mido.Message]:
        if note != self._last_note:
            return self._off_message() + [
                mido.Message(
                    "note_on", channel=self._channel, note=note.midi, velocity=127
                )
            ]
        return []

    def _off_message(self) -> [mido.Message]:
        if self._last_note is not None:
            return [
                mido.Message(
                    "note_off", channel=self._channel, note=self._last_note.midi
                )
            ]
        return []


class LiveQueryPlayer(QueryPlayer):

    def _get_note(self) -> float:
        json = requests.get(self._query).json()
        self._log.debug("Prometheus JSON: %s", json)
        result = json["data"]["result"]
        if len(result) > 1:
            self._log.warning("More than 1 result in Prometheus JSON (%d)", len(result))
        _timestamp, value = result[0]["value"]
        self._log.info("Metric value: %s", value)
        note = self._instrument.clamp(float(value))
        self._log.info("Note: %s (%d)", note, note.midi)
        return note

    def off(self):
        for msg in self._off_message():
            self._port.send(msg)

    def prep(self):
        note = self._get_note()
        self._next_messages = self._get_messages(note)
        self._last_note = note

    def tick(self):
        for msg in self._next_messages:
            self._port.send(msg)
        self._next_messages = []


def get_players_from_config(
    config_file: str,
    port: mido.ports.BaseOutput,
    prometheus_host: str,
    raw_replacements: [str],
) -> [QueryPlayer]:
    with open(config_file) as fp:
        loaded = yaml.safe_load(fp)
    scale_cls = getattr(music21.scale, loaded["scale"]["class"])
    scale = scale_cls(loaded["scale"]["tonic"])
    logging.info("Selected scale: %s", scale.name)
    instruments = {
        name: Instrument(name, scale=scale, **config)
        for name, config in loaded["instruments"].items()
    }
    replacements = [replacement.split("=", 1) for replacement in raw_replacements]
    return [
        LiveQueryPlayer(
            port,
            name,
            instruments,
            prometheus_host=prometheus_host,
            replacements=replacements,
            channel=channel,
            **player_config,
        )
        for channel, (name, player_config) in enumerate(loaded["queries"].items())
    ]


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


def parse_live_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--midi-output")
    parser.add_argument("--config-file", default="config.yml")
    parser.add_argument("--replacement", action="append", default=[])
    parser.add_argument("prometheus_host", metavar="PROMETHEUS-HOST")
    return parser.parse_args()


def live_main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s"
    )
    args = parse_live_args()

    port = open_midi_output(args.midi_output)

    players = get_players_from_config(
        args.config_file, port, args.prometheus_host, args.replacement
    )

    while True:
        logging.info("Starting loop...")
        start = time.time()
        for player in players:
            try:
                player.prep()
            except (IndexError, requests.exceptions.ConnectionError) as exc:
                # Ignore these errors by falling through to the sleep logic
                logging.exception("Ignoring occasional error")

        for player in players:
            player.tick()

        # Attempt to reduce drift
        delta = (start + 5) - time.time()
        logging.info("Loop complete; sleeping for %ss", delta)
        time.sleep(delta)


if __name__ == "__main__":
    live_main()
