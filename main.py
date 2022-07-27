import argparse
import logging
import time
from typing import Optional

import mido
import requests
import music21
import yaml
from music21 import scale


QUERY_TEMPLATE = "http://<redacted>:9090/api/v1/query?query={query}"

SCALE = scale.MajorScale("c")


class Instrument:
    def __init__(self, name: str, program_number: int, pitch_range: (str, str)):
        self.name = name
        self.program_number = program_number

        self.available_pitches = [pitch for pitch in SCALE.getPitches(*pitch_range)]

    def clamp(self, value: float) -> music21.pitch.Pitch:
        idx = round((len(self.available_pitches) - 1) * value)
        return self.available_pitches[idx]


INSTRUMENTS = {
    "cello": Instrument("cello", 42, ("c2", "a5")),
    # Technical top of range is 74, but that is not bassy at all
    "contrabass": Instrument("contrabass", 43, ("e1", "a2")),
    "choir_aahs": Instrument("choir_aahs", 52, ("c4", "c6")),
    "english_horn": Instrument("english_horn", 69, ("e3", "a5")),
}


class QueryPlayer:
    def __init__(
        self,
        port: mido.ports.BaseOutput,
        name: str,
        instrument: Instrument,
        query: str,
        *,
        channel: int = 0
    ):
        self._port = port
        self._name = name
        self._instrument = INSTRUMENTS[instrument]
        self._query = query
        self._channel = channel

        self._port.send(
            mido.Message(
                "program_change",
                channel=self._channel,
                program=self._instrument.program_number,
            )
        )

        self._value = None
        self._last_note = None
        self._log = logging.getLogger("{}:{}".format(self._name, self._instrument.name))

    def _get_value(self) -> float:
        json = requests.get(
            QUERY_TEMPLATE.format(
                query=self._query.replace("$instance", "<redacted>:9100")
            )
        ).json()
        self._log.debug("Prometheus JSON: %s", json)
        timestamp, value = json["data"]["result"][0]["value"]
        self._log.info("Metric value: %s", value)
        return float(value)

    def _handle_value(self, value: float) -> None:
        note = self._instrument.clamp(value)
        self._log.info("Note: %s (%d)", note, note.midi)

        if note != self._last_note:
            self.off()
            self._port.send(
                mido.Message(
                    "note_on", channel=self._channel, note=note.midi, velocity=127
                )
            )
        self._last_note = note
        return note

    def off(self):
        if self._last_note is not None:
            self._port.send(
                mido.Message(
                    "note_off", channel=self._channel, note=self._last_note.midi
                )
            )

    def prep(self):
        self._value = self._get_value()

    def tick(self):
        self._handle_value(self._value)


def get_players_from_config(
    config_file: str, port: mido.ports.BaseOutput
) -> [QueryPlayer]:
    with open(config_file) as fp:
        loaded = yaml.safe_load(fp)
    return [
        QueryPlayer(port, name, channel=channel, **player_config)
        for channel, (name, player_config) in enumerate(loaded.items())
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


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--midi-output")
    parser.add_argument("--config-file", default="config.yml")
    return parser.parse_args()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s"
    )
    args = parse_args()

    port = open_midi_output(args.midi_output)

    players = get_players_from_config(args.config_file, port)

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
    main()
