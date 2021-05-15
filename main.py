import logging
import time

import mido
import requests
from dataclasses import dataclass
import music21
from music21 import scale


QUERY_TEMPLATE = "http://<redacted>:9090/api/v1/query?query={query}"
MEM_QUERY = """1 -
(
  avg(node_memory_MemAvailable_bytes{{job="node", instance="{instance}"}})
/
  avg(node_memory_MemTotal_bytes{{job="node", instance="{instance}"}})
)"""
CPU_QUERY = """
(
  (1 - rate(node_cpu_seconds_total{{job="node", mode="idle", instance="{instance}"}}[30s]))
)"""
PROCS_QUERY = """
avg_over_time(node_procs_running{{job="node", instance="{instance}"}}[30s])
/
max_over_time(node_procs_running{{job="node", instance="{instance}"}}[10m])
"""

MIDI_OUTPUT_NAME = "FLUID Synth (62185):Synth input port (62185:0) 128:0"


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
        self._instrument = instrument
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

    def _get_value(self) -> float:
        json = requests.get(
            QUERY_TEMPLATE.format(query=self._query.format(instance="<redacted>:9100"))
        ).json()
        logging.info("Prometheus JSON: %s", json)
        timestamp, value = json["data"]["result"][0]["value"]
        logging.info("Metric value: %s", value)
        return float(value)

    def _handle_value(self, value: float) -> None:
        note = self._instrument.clamp(value)
        logging.info("Note: %s (%d)", note, note.midi)

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


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    logging.info("Opening MIDI output: %s", MIDI_OUTPUT_NAME)
    port = mido.open_output(MIDI_OUTPUT_NAME, autoreset=True)

    players = [
        QueryPlayer(port, "RAM", INSTRUMENTS["contrabass"], MEM_QUERY, channel=0),
        QueryPlayer(port, "CPU", INSTRUMENTS["cello"], CPU_QUERY, channel=1),
        QueryPlayer(port, "PROCS", INSTRUMENTS["english_horn"], PROCS_QUERY, channel=2),
    ]

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
