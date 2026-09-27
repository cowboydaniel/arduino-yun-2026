# Backup and recovery

The update never writes U-Boot, so a Yún that doesn't boot after flashing can
always be put back from U-Boot's prompt over the serial console and TFTP. Do
the backup and the rehearsal below **before** the first flash.

Everything here was checked against a stock Yún: U-Boot 1.1.4-linino, console
at 250000 baud, autoboot stopped with `lin`, `bootcmd=bootm 0x9fea0000` (see
[`uboot-boot-capture.txt`](uboot-boot-capture.txt) and
[`uboot-env.txt`](uboot-env.txt)).

## 1. Back up the flash (from stock Linux)

Over SSH on the Yún, one partition at a time (RAM is tight):

```sh
cat /proc/mtd                         # mtd0 u-boot ... mtd7 firmware
for n in 0 1 4 5 6; do cat /dev/mtd$n > /tmp/mtd$n.bin; done
```

Copy those off, delete them from `/tmp`, then do the big one on its own:

```sh
cat /dev/mtd7 > /tmp/mtd7.bin         # 15936 KB: rootfs + kernel, what a restore writes back
```

On the PC:

```sh
scp root@arduino.local:/tmp/mtd\*.bin .
sha256sum mtd*.bin > SHA256SUMS
```

Keep these on the PC only, not in the repository: `mtd6` (art) holds the
board's MAC addresses and Wi-Fi calibration, and `mtd7` holds your settings
and passwords. The sizes must be: mtd0 262144, mtd1 65536, mtd4 1310720,
mtd5 65536, mtd6 65536, mtd7 16318464 bytes.

## 2. Get to the U-Boot prompt

1. Upload **File → Examples → Bridge → YunSerialTerminal** to the Yún over USB.
2. Open the Serial Monitor at **115200** baud with **Newline** line endings. The
   sketch talks to the AR9331 at 250000 baud, which is also what U-Boot uses.
3. Restart Linux (`reboot` over SSH, or press YÚN RST).
4. When `autoboot in 4 seconds (stop with 'lin')...` appears, type `lin` and
   press Enter. You get U-Boot's command prompt.

Don't run `saveenv` at any point. Nothing here needs it.

## 3. Rehearse TFTP (changes nothing)

Put `mtd4.bin` (the stock kernel) on a TFTP server on the PC, and connect the
Yún's Ethernet port to the same network. U-Boot's saved settings expect the
PC at 192.168.1.2 and use 192.168.1.146 itself; change them for this session
only if your network differs:

```
setenv serverip 192.168.1.2
setenv ipaddr 192.168.1.146
tftp 0x81000000 mtd4.bin
bootm 0x81000000
```

`tftp` should report `Bytes transferred = 1310720`, and `bootm` should boot
stock Linino as usual (the kernel comes from RAM, the rest from flash). If
this works, recovery works.

## 4. Restore stock (if the new firmware doesn't boot)

At the U-Boot prompt, with `mtd7.bin` on the TFTP server:

```
tftp 0x80060000 mtd7.bin
```

Check that it says `Bytes transferred = 16318464 (f90000 hex)`. Then:

```
erase 0x9f050000 +0xf90000
cp.b 0x80060000 0x9f050000 0xf90000
reset
```

Erasing and writing 15.6 MB takes several minutes; let it finish. The board
then boots stock Linino with all its old settings.

**Type the addresses exactly.** The flash is mapped at 0x9f000000. The only
range these commands may touch is 0x9f050000-0x9ffdffff. Erasing below
0x9f050000 destroys U-Boot, after which only an external SPI flash programmer
can revive the board; erasing 0x9fff0000 destroys the Wi-Fi calibration,
which only your `mtd6.bin` backup can restore.
