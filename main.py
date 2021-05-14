import time

import mido
import requests


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


def get_value() -> float:
    json = requests.get(
        QUERY_TEMPLATE.format(query=QUERY.format(instance="<redacted>:9100"))
    ).json()
    timestamp, value = json["data"]["result"][0]["value"]
    return float(value)


def normalise_value(value: float) -> int:
    """Convert a percentile float to a MIDI note number"""
    lower_bound = 36
    upper_bound = 81
    return round(lower_bound + (upper_bound - lower_bound) * value)


def main():
    port = mido.open_output(MIDI_OUTPUT_NAME)

    last_note = None
    while True:
        start = time.time()
        value = get_value()
        print(value)
        note = normalise_value(value)
        print(note)

        if note != last_note:
            if last_note is not None:
                port.send(mido.Message("note_off", note=last_note))
            port.send(mido.Message("note_on", note=note, velocity=127))
        last_note = note

        # Attempt to reduce drift
        delta = (start + 20) - time.time()
        print(delta)
        time.sleep(delta)


if __name__ == "__main__":
    main()
