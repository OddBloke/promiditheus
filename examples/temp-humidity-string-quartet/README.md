# Temperature/Humidity String Quartet

This example uses Prometheus metrics for temperature/humidity widgets I have in my house to
generate four channels of MIDI output, corresponding to the instruments in a string quartet.

(The below invocations are simply examples: you won't be able to run them unless you're in my
house!)

## Realtime

To have the string quartet play in realtime (after following the setup instructions in the
quickstart), use:

```sh
promiditheus \
    --midi-output <fluidsynth port> \
    temp-humidity-string-quartet.yml \
    <prometheus-host>
```

## Generate MIDI File

To generate a MIDI file containing four tracks of MIDI for the last 4 hours of temperature/humidity
data sped up to fit into 3 minutes in `string-quartet.mid`, use:

```sh
promiditheus-generate \
    --range $(($(date +%s) - 4*60*60)):$(date +%s) \
    --speed-up-factor 80 \
    --prometheus-step 5 \
    temp-humidity-string-quartet.yml \
    <prometheus-host> \
    string-quartet.mid
```

An example of the generated output is available in `./string-quartet.mid`.
