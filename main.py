import logging
import time

import mido
import requests
from dataclasses import dataclass


QUERY_TEMPLATE = "http://<redacted>:9090/api/v1/query?query={query}"
QUERY = """100 -
(
  avg(node_memory_MemAvailable_bytes{{job="node", instance="{instance}"}})
/
  avg(node_memory_MemTotal_bytes{{job="node", instance="{instance}"}})
* 100
)"""
QUERY = """
(
  (1 - rate(node_cpu_seconds_total{{job="node", mode="idle", instance="{instance}"}}[30s]))
)"""

MIDI_OUTPUT_NAME = "FLUID Synth (62185):Synth input port (62185:0) 128:0"


@dataclass
class Instrument:
    name: str
    program_number: int
    midi_range: (int, int)

    def clamp(self, value):
        lower_bound, upper_bound = self.midi_range
        return round(lower_bound + (upper_bound - lower_bound) * value)


INSTRUMENTS = {
    "cello": Instrument("cello", 42, (36, 81)),
}


class QueryPlayer:
    def __init__(self, name: str, instrument: Instrument, query: str):
        self._name = name
        self._instrument = instrument
        self._query = query

        self._last_note = None

        logging.info("Opening MIDI output: %s", MIDI_OUTPUT_NAME)
        self._port = mido.open_output(MIDI_OUTPUT_NAME)

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
        logging.info("Note: %s", note)

        if note != self._last_note:
            self.off()
            self._port.send(mido.Message("note_on", note=note, velocity=127))
        self._last_note = note
        return note

    def off(self):
        if self._last_note is not None:
            self._port.send(mido.Message("note_off", note=self._last_note))

    def tick(self):
        self._handle_value(self._get_value())


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    player = QueryPlayer("CPU", INSTRUMENTS["cello"], QUERY)

    try:
        while True:
            logging.info("Starting loop...")
            start = time.time()
            try:
                player.tick()
            except (IndexError, requests.exceptions.ConnectionError) as exc:
                # Ignore these errors by falling through to the sleep logic
                logging.exception("Ignoring occasional error")

            # Attempt to reduce drift
            delta = (start + 5) - time.time()
            logging.info("Loop complete; sleeping for %ss", delta)
            time.sleep(delta)
    finally:
        player.off()


if __name__ == "__main__":
    main()
