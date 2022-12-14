# promiditheus

Promiditheus is a tool to generate MIDI from Prometheus data.  It consists of two commands:

* `promiditheus` fetches live Prometheus data and emits corresponding MIDI in a loop
* `promiditheus-generate` generates a MIDI file for Prometheus data in a given range

## Installation

This repository is pip-installable: `pip install git+https://github.com/OddBloke/promiditheus`

## Quickstart (using ALSA)

(This presupposes you have a Prometheus instance running which is actively scraping node_exporter
data for a host: you will need the Prometheus hostname and the target host instance name.)

* Run a fluidsynth instance: `fluidsynth -a alsa /usr/share/sounds/sf2/default-GM.sf2`, find its
  ALSA midi port using `aconnect -l` (it's often "128:0")
* Write this config file to `lead-sheet.yml`:

```yml
queries:
  cpu:
    query: |
      1 - avg(rate(node_cpu_seconds_total{mode="idle", instance="$instance"}[30s]))
    instrument: cello
```

* Run:

```sh
promiditheus \
    --replacement instance=<scraped instance name> \
    --midi-output <fluidsynth port> \
    lead-sheet.yml \
    <prometheus-host>
```

This should result in Promiditheus running and Fluidsynth playing a cello corresponding to the CPU
usage which `<prometheus-host>` is scraping from `<scraped instance name>`.

## Configuration

### Lead Sheets

Each Promiditheus instance is invoked with a lead sheet, which defines the Prometheus queries to be
played and the instruments that will play them.  This is an example of a simple lead sheet:

```yaml
queries:
  cpu:
    query: |
      1 - avg(rate(node_cpu_seconds_total{mode="idle", instance="my-server:9100"}[30s]))
    instrument: cello
```

This instructs Promiditheus to:

* send a `program_change` message (once) to set channel 0 to Program Number 42: the cello in the
  General MIDI spec
* query Prometheus for the average CPU usage of the `my-server:9100` instance (as a float between 0
  and 1),
* convert that value to the corresponding note in the C Major scale within the cello's range (i.e.
  0.0 would be the lowest note a cello can play, 1.0 the highest), and
* emit a `note_on` MIDI event (as well as a preceding `note_off` event if necessary) on channel 0.

If `promiditheus` is invoked, these MIDI events will be generated in a loop and written to the
output MIDI port.  If `promiditheus-generate` is invoked, these MIDI events will be generated for
every step in the specified range and written to the output MIDI file.

### Replacements

To enable reuse of lead sheets, Promiditheus supports replacements.  If we modify the above example
to use a `$instance` variable:

```yaml
queries:
  cpu:
    query: |
      1 - avg(rate(node_cpu_seconds_total{mode="idle", instance="$instance"}[30s]))
    instrument: cello
```

And invoke `promiditheus` or `promiditheus-generate` with `--replacement instance=my-server:9100`,
it will behave the same as the original example.

### Queries

Lead sheets can contain multiple queries, each of which can use a different instrument:

```yaml
queries:
  cpu:
    query: |
      1 - avg(rate(node_cpu_seconds_total{mode="idle", instance="$instance"}[30s]))
    instrument: cello
  ram:
    query: |
      1 - (
        avg(node_memory_MemAvailable_bytes{instance="$instance"})
        /
        avg(node_memory_MemTotal_bytes{instance="$instance"})
      )
    instrument: contrabass
```

All queries must return a value between 0 and 1: this is used to index into the available notes for
a given instrument.  Any values outside of that range will be discarded.

The MIDI events generated for each query will be emitted on separate channels, based on the order
in which they are defined in the lead sheet.  (In this example, `cpu` events will be emitted on
channel 0 and `ram` events on channel 1.)  When `promiditheus-generate` is used, they will each be
written as separate tracks.

### Instruments

Promiditheus ships with (currently incomplete) instrument definitions corresponding to the [General
MIDI instruments](https://en.wikipedia.org/wiki/General_MIDI#Program_change_events).  Lead sheets
can also define their own instruments:

```yaml
instruments:
  cello:
    program_number: 42
    pitch_range: ["c3", "a5"]
  my-synth-bass:
    program_number: 12
    pitch_range: ["c2", "c3"]
queries:
  # Temperature: assume range of 15 to 30C
  bedroom_temp:
    query: |
      (dht_temperature_celsius{instance="192.168.1.23:9099"} - 15) / (30 - 15)
    instrument: cello
  basement_temp:
    query: |
      (dht_temperature_celsius{instance="192.168.1.22:9099"} - 15) / (30 - 15)
    instrument: my-synth-bass
  # Humidity: values are 0-100: assume range of 15% to 50%
  bedroom_humidity:
    query: |
      (dht_humidity{instance="192.168.1.23:9099"} - 15) / (50 - 15)
    instrument: viola
  basement_humidity:
    query: |
      (dht_humidity{instance="192.168.1.22:9099"} - 15) / (50 - 15)
    instrument: violin
```

In this example:

* the built-in `cello` instrument definition is replaced (to raise the bottom of its range from C2
  to C3),
* a new `my-synth-bass` instrument is defined with a custom range,
* the built-in `viola` and `violin` definitions are used, because the lead sheet definitions are
  merged over the built-in ones

### Scale

By default, Promiditheus will generate MIDI events using notes in the C Major scale.  A lead sheet
can specify a different scale for note selection:

```yaml
scale:
  class: MinorScale
  tonic: d
queries:
  cpu:
    query: |
      1 - avg(rate(node_cpu_seconds_total{mode="idle", instance="$instance"}[30s]))
    instrument: cello
```

This example will select notes from D Minor instead of C Major.  `class` can specify the name of
any of [the `music21.scale`
classes](https://web.mit.edu/music21/doc/moduleReference/moduleScale.html#), which will be invoked
with `tonic` as its argument.

## Creating MIDI Files

`prometheus-generate` is the command used to generate MIDI files from a lead sheet and a time
range.

If you execute this command after following the Quickstart instructions, a MIDI file containing a
cello playing the last three minutes of CPU usage will be generated in `output.mid`:

```sh
promiditheus-generate \
    --replacement instance=<scraped instance name> \
    --midi-output <fluidsynth port> \
    lead-sheet.yml \
    <prometheus-host> \
    output.mid
```

You can play that generated file using FluidSynth:

```sh
fluidsynth -a alsa output.mid
```

## Examples

See [./examples/](./examples/) for some examples of how Promiditheus can be used.

## Credits

Thanks to my employer, DigitalOcean, for letting me spend hack-week time on this ultimately
somewhat-silly project.
