SKETCH ?= firmware/uno/conqueror_serial
FQBN ?= arduino:avr:uno
PORT ?=
BAUD ?= 115200

.PHONY: compile upload monitor ports

compile:
	arduino-cli compile -b $(FQBN) $(SKETCH)

upload:
	@test -n "$(PORT)" || (echo "Set PORT=/dev/tty..."; exit 1)
	arduino-cli upload -p $(PORT) -b $(FQBN) $(SKETCH)

monitor:
	@test -n "$(PORT)" || (echo "Set PORT=/dev/tty..."; exit 1)
	arduino-cli monitor -p $(PORT) -c baudrate=$(BAUD)

ports:
	arduino-cli board list

