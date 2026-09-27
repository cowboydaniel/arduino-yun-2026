// Rehearses U-Boot TFTP recovery (docs/recovery.md, step 4) from the 32U4,
// so it can be done over Wi-Fi without a USB serial console.
//
// On the next reboot of the Linux side it stops U-Boot ("lin"), sets the
// TFTP addresses, loads FILE into RAM (nothing is written to flash), prints
// its CRC32, then types "boot". It marks itself done in EEPROM before typing
// anything, so it only ever interrupts one boot: if something goes wrong, a
// power cycle boots normally.
//
// Everything U-Boot prints (without the '#' progress marks) is saved to
// EEPROM from byte 16; byte 1 is the state (1 = done), bytes 4..5 the length.
//
// Set the three values below for your network, then on stock Linino:
//
//   (upload sketch.ino.with_bootloader.hex to /tmp/sketch.hex)
//   run-avrdude /tmp/sketch.hex
//   reboot
//
// and after it's back, read the result:
//
//   echo 1 > /sys/class/gpio/gpio21/value
//   avrdude -c linuxspi_yun -P /dev/spidev1.0 -p m32u4 -U eeprom:r:/tmp/ee.bin:r
//   echo 0 > /sys/class/gpio/gpio21/value
//   dd if=/tmp/ee.bin bs=1 skip=16 2>/dev/null | tr -d '\377'
//
// Look for "Bytes transferred" with the file's full size, and a CRC32 that
// matches the file's.

#define IPADDR   "192.168.1.146"    // the Yun's address in U-Boot (must be free)
#define SERVERIP "192.168.1.2"      // the PC running the TFTP server
#define FILE     "yun-rootfs.bin"   // on the TFTP server

#include <EEPROM.h>

const uint16_t DATA_START = 16, DATA_LEN = 1024 - 16;
const char MARK[] = "autoboot in";
const char END_MARK[] = "Starting kernel";
const char TFTP_DONE[] = "Bytes transferred";
const char TFTP_FAIL[] = "Retry count exceeded";
uint8_t donePos = 0, failPos = 0;
bool tftpOver = false;

const char *const CMDS[] = {
	"setenv ipaddr " IPADDR "\r",
	"setenv serverip " SERVERIP "\r",
	"tftp 0x80060000 " FILE "\r",
};
const uint8_t NCMDS = 3;

uint8_t buf[DATA_LEN];
uint16_t len = 0;
uint8_t markPos = 0, endPos = 0;
enum { WAIT, RUN, DONE } st = WAIT;
uint8_t step = 0;           // commands sent so far; then crc32, then boot
unsigned long t0 = 0, bootSent = 0;
uint8_t boots = 0;

static bool match(const char *m, uint8_t &p, char c) {
	if (c == m[p]) { if (!m[++p]) { p = 0; return true; } }
	else p = (c == m[0]) ? 1 : 0;
	return false;
}

static void slowPrint(const char *s) {
	for (; *s; s++) { Serial1.write(*s); delay(3); }
}

static void finish() {
	for (uint16_t i = 0; i < len; i++) EEPROM.update(DATA_START + i, buf[i]);
	EEPROM.update(4, len & 0xff);
	EEPROM.update(5, len >> 8);
	st = DONE;
	digitalWrite(LED_BUILTIN, LOW);
}

void setup() {
	pinMode(LED_BUILTIN, OUTPUT);
	Serial1.begin(250000);
	if (EEPROM.read(0) != 0x5a) {
		EEPROM.update(0, 0x5a);
		for (uint8_t i = 1; i < 6; i++) EEPROM.update(i, 0);
	}
	st = EEPROM.read(1) ? DONE : WAIT;
	digitalWrite(LED_BUILTIN, st == WAIT);
}

void loop() {
	while (Serial1.available()) {
		char c = Serial1.read();
		if (st == WAIT) {
			if (match(MARK, markPos, c)) {
				EEPROM.update(1, 1);        // done before touching U-Boot
				slowPrint("lin");
				st = RUN; step = 0; t0 = millis();
			}
		} else if (st == RUN) {
			if (c != '#' && len < DATA_LEN) buf[len++] = c;
			if (match(TFTP_DONE, donePos, c) || match(TFTP_FAIL, failPos, c)) tftpOver = true;
			if (match(END_MARK, endPos, c) || len >= DATA_LEN) { finish(); return; }
		}
	}
	if (st != RUN) return;

	unsigned long now = millis();
	// Commands: 1.5 s apart; the tftp gets up to 25 s.
	if (step < NCMDS && now - t0 > 1500) {
		slowPrint(CMDS[step]);
		step++; t0 = now;
	} else if (step == NCMDS && ((tftpOver && now - t0 > 1000) || now - t0 > 300000UL)) {
		slowPrint("crc32 $fileaddr $filesize\r");
		step++; t0 = now;
	} else if (step == NCMDS + 1 && now - t0 > 3000) {
		slowPrint("boot\r");
		step++; bootSent = now; boots = 1;
	} else if (step > NCMDS + 1 && now - bootSent > 15000) {
		// Still no kernel: try again, then give up (a power cycle boots
		// normally, because this sketch is already marked done).
		if (boots < 3) { slowPrint("\rboot\r"); boots++; bootSent = now; }
		else finish();
	}
}
