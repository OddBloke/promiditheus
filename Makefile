deb-deps:
	apt-get install \
		fluidsynth \
		libjack-jackd2-dev

fluidsynth:
	fluidsynth -a alsa /usr/share/sounds/sf2/default-GM.sf2
