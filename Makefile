deb-deps:
	apt-get install \
		fluidsynth \
		libjack-jackd2-dev

fluidsynth:
	fluidsynth -a alsa
