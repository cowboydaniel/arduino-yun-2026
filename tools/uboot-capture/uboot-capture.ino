// Records the AR9331's U-Boot power-on output from the 32U4 side of the Yun,
// so it can be read back over Wi-Fi without a USB serial connection.
//
// The 32U4 keeps running while Linux reboots. Once armed, this sketch waits
// for the U-Boot banner on Serial1, records everything up to "Starting kernel"
// (or until the buffer is full) and saves it to EEPROM. It only ever listens:
// it never sends anything to U-Boot.
//
// EEPROM layout:
//   0     magic 0x5a
//   1     state: 0 = armed, 1 = done
//   2..3  number of bytes skipped after the banner before recording (LE)
//   4..5  number of bytes recorded (LE)
//   16..  recorded text
//
// Read it back from Linux with avrdude over SPI, e.g.
//   avrdude -c linuxspi_yun -P /dev/spidev1.0 -p m32u4 -U eeprom:r:/tmp/ee.bin:r
// To record the next part of a long banner, set the skip count to the number
// of bytes already recorded and the state back to 0.

#include <EEPROM.h>

const uint16_t DATA_START = 16;
const uint16_t DATA_LEN = 1024 - DATA_START;
const unsigned long IDLE_TIMEOUT_MS = 20000;

const char START_MARK[] = "U-Boot 1.";
const char END_MARK[] = "Starting kernel";

uint8_t buf[DATA_LEN];
uint16_t len = 0;
uint16_t skip = 0;
uint16_t seen = 0;
uint8_t startPos = 0;
uint8_t endPos = 0;
bool recording = false;
bool done = false;
unsigned long lastByte = 0;

static void finish() {
	for (uint16_t i = 0; i < len; i++)
		EEPROM.update(DATA_START + i, buf[i]);
	EEPROM.update(4, len & 0xff);
	EEPROM.update(5, len >> 8);
	EEPROM.update(1, 1);
	done = true;
	digitalWrite(LED_BUILTIN, LOW);
}

// Advances a simple substring matcher and returns true on a full match.
static bool match(const char *mark, uint8_t &pos, char c) {
	if (c == mark[pos]) {
		pos++;
		if (mark[pos] == '\0') {
			pos = 0;
			return true;
		}
	} else {
		pos = (c == mark[0]) ? 1 : 0;
	}
	return false;
}

void setup() {
	pinMode(LED_BUILTIN, OUTPUT);
	Serial1.begin(250000);

	if (EEPROM.read(0) != 0x5a) {
		// First run after upload: arm with no skip.
		EEPROM.update(0, 0x5a);
		EEPROM.update(1, 0);
		EEPROM.update(2, 0);
		EEPROM.update(3, 0);
		EEPROM.update(4, 0);
		EEPROM.update(5, 0);
	}
	done = EEPROM.read(1) != 0;
	skip = EEPROM.read(2) | (EEPROM.read(3) << 8);
	digitalWrite(LED_BUILTIN, done ? LOW : HIGH);
}

void loop() {
	if (done) {
		// Drain and ignore everything once finished.
		while (Serial1.available())
			Serial1.read();
		return;
	}

	while (Serial1.available()) {
		char c = Serial1.read();
		lastByte = millis();

		if (!recording) {
			if (match(START_MARK, startPos, c)) {
				recording = true;
				// Put the banner text we matched back at the front.
				for (uint8_t i = 0; START_MARK[i]; i++) {
					if (seen++ >= skip && len < DATA_LEN)
						buf[len++] = START_MARK[i];
				}
			}
			continue;
		}

		if (seen++ >= skip)
			buf[len++] = c;

		if (match(END_MARK, endPos, c) || len >= DATA_LEN) {
			finish();
			return;
		}
	}

	// Save whatever we have if the line goes quiet partway through.
	if (recording && millis() - lastByte > IDLE_TIMEOUT_MS)
		finish();
}
