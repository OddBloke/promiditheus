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


def get_value() -> float:
    json = requests.get(
        QUERY_TEMPLATE.format(query=QUERY.format(instance="<redacted>:9100"))
    ).json()
    logging.info("Prometheus JSON: %s", json)
    timestamp, value = json["data"]["result"][0]["value"]
    logging.info("Metric value: %s", value)
    return float(value)


def handle_value(port: mido.ports.BaseOutput, value: float, last_note: int) -> None:
    note = INSTRUMENTS["cello"].clamp(value)
    logging.info("Note: %s", note)

    if note != last_note:
        if last_note is not None:
            port.send(mido.Message("note_off", note=last_note))
        port.send(mido.Message("note_on", note=note, velocity=127))
    return note


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-8s %(message)s',
    )

    logging.info("Opening MIDI output: %s", MIDI_OUTPUT_NAME)
    port = mido.open_output(MIDI_OUTPUT_NAME)

    try:
        last_note = None
        while True:
            logging.info("Starting loop...")
            start = time.time()
            try:
                value = get_value()
            except (IndexError, requests.exceptions.ConnectionError) as exc:
                # Ignore these errors by falling through to the sleep logic
                logging.exception("Ignoring occasional error")
            else:
                last_note = handle_value(port, value, last_note)

            # Attempt to reduce drift
            delta = (start + 5) - time.time()
            logging.info("Loop complete; sleeping for %ss", delta)
            time.sleep(delta)
    finally:
        if last_note is not None:
            port.send(mido.Message("note_off", note=last_note))


if __name__ == "__main__":
    main()
