# node-exporter

This example uses common node_exporter Prometheus metrics to generate three channels of MIDI
output.

## Realtime

To run it in realtime (after following the setup instructions in the quickstart), use:

```sh
promiditheus \
    --replacement instance=<scraped instance name> \
    --midi-output <fluidsynth port> \
    node-exporter-lead-sheet.yml \
    <prometheus-host>
```

## Generate MIDI File

To generate a MIDI file containing the three tracks of MIDI in `output.mid`, use:

```sh
promiditheus-generate \
    --replacement instance=<scraped instance name> \
    --speed-up-factor 10 \
    node-exporter-lead-sheet.yml \
    <prometheus-host> \
    node-exporter.mid
```

This uses the default range of 3mins, resulting in an 18s output file due to the speed-up factor of
10: an example of the generated output is available in `./node-exporter.mid`.
