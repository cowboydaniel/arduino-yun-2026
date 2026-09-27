# Backup and recovery

The update never writes U-Boot. So a Yún that doesn't boot after flashing can always be put back from U-Boot's prompt, over the serial console and TFTP. Do the backup and the rehearsal below **before** the first flash.

Everything here was checked on a stock Yún (OpenWrtYun 1.6.2): U-Boot 1.1.4-linino, a console at 250000 baud, autoboot stopped with `lin`, and `bootcmd=bootm 0x9fea0000`. See [`uboot-boot-capture.txt`](uboot-boot-capture.txt) and [`uboot-env.txt`](uboot-env.txt). On that board:
- the backup
- U-Boot taking typed commands from the 32U4
- a TFTP transfer of the full 14.3 MB rootfs into RAM

were all tested for real.

## 1. Back up the flash (from stock Linux)

On the PC, read each partition straight over SSH. Nothing is stored on the Yún, so RAM doesn't matter:

```sh
mkdir yun-backup && cd yun-backup
for p in 0:u-boot 1:u-boot-env 2:rootfs 4:kernel 5:nvram 6:art; do
    ssh root@arduino.local "cat /dev/mtd${p%%:*}" > "mtd${p%%:*}-${p#*:}.bin"
done
```

Check the copies against the board, which has `md5sum` but not `sha256sum`:

```sh
ssh root@arduino.local 'md5sum /dev/mtd0 /dev/mtd1 /dev/mtd2 /dev/mtd4 /dev/mtd5 /dev/mtd6'
md5sum mtd*.bin
```

The two lists must match, and the sizes must be:

| File | Size (bytes) |
| --- | --- |
| mtd0-u-boot | 262144 |
| mtd1-u-boot-env | 65536 |
| mtd2-rootfs | 15007744 |
| mtd4-kernel | 1310720 |
| mtd5-nvram | 65536 |
| mtd6-art | 65536 |

`mtd2` and `mtd4` together are the whole stock firmware, and they're what a restore writes back.

For a copy of the whole 16 MB chip, for an SPI flash programmer in the worst case, join them in flash order:

```sh
cat mtd0-u-boot.bin mtd1-u-boot-env.bin mtd2-rootfs.bin mtd4-kernel.bin mtd5-nvram.bin mtd6-art.bin > yun-full-flash-16M.bin
```

Keep all of this on the PC only, never in a repository. `mtd6` (art) holds the board's MAC addresses and Wi-Fi calibration, which can't be replaced. `mtd2` holds your settings.

The stock firmware's SSH server is old. Recent OpenSSH clients need `-o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa`, or the same lines in `~/.ssh/config`.

## 2. Set up a TFTP server on the PC

On Debian or Ubuntu:

```sh
sudo apt install tftpd-hpa
sudo install -m 644 mtd4-kernel.bin /srv/tftp/yun-kernel.bin
sudo install -m 644 mtd2-rootfs.bin /srv/tftp/yun-rootfs.bin
```

TFTP has no password, so stop the server when you don't need it (`sudo systemctl stop tftpd-hpa`), and start it again for a recovery.

Note the PC's address (`ip -4 addr`). The Yún's Ethernet port must be on the same network. U-Boot's saved settings expect the PC at 192.168.1.2 and use 192.168.1.146 for itself. Change them for one session with `setenv` if your network differs, and make sure the Yún's address isn't in use.

## 3. Get to the U-Boot prompt

1. Upload **File → Examples → Bridge → YunSerialTerminal** to the Yún over USB. It can also go over Wi-Fi while the stock firmware still runs, so the console is ready later.
2. Open a serial monitor at **115200** baud with **Newline** line endings, for example `picocom -b 115200 /dev/ttyACM0`. The sketch talks to the AR9331 at 250000 baud, which is also what U-Boot uses.
3. Restart Linux: `reboot` over SSH, or press YÚN RST.
4. When `autoboot in 4 seconds (stop with 'lin')...` appears, type `lin` and press Enter. You get the `linino>` prompt.

Don't run `saveenv` at any point. Nothing here needs it.

Other ways to reach the same console:
- A USB-to-TTL adapter on D0/D1, with the 32U4 held in reset.
- An Arduino as ISP, to restore the 32U4's bootloader if that ever breaks.

## 4. Rehearse (changes nothing)

At the U-Boot prompt, load the rootfs backup into RAM, check it, and boot as usual:

```
setenv serverip 192.168.1.2
setenv ipaddr 192.168.1.146
tftp 0x80060000 yun-rootfs.bin
crc32 $fileaddr $filesize
boot
```

`tftp` must end with `Bytes transferred = 15007744 (e50000 hex)`. The CRC32 it prints must match the file's, which you can get on the PC with `python3 -c "import zlib,sys; print('%08x' % zlib.crc32(open(sys.argv[1],'rb').read()))" mtd2-rootfs.bin`. A transfer can take a minute or two, and an occasional `T` (timeout) or `len bad` message is harmless: TFTP asks again for anything it missed.

If this works, recovery works.

**No USB?** [`tools/uboot-rehearsal`](../tools/uboot-rehearsal) is a sketch that runs this same rehearsal from the 32U4 on the next reboot, and records U-Boot's output in EEPROM. Upload it over Wi-Fi with `run-avrdude`, then read the result back with avrdude. The sketch explains how.

## 5. Restore stock (if the new firmware doesn't boot)

At the U-Boot prompt, with both files on the TFTP server, do the kernel and then the rootfs. After each `tftp`, check `Bytes transferred` and the CRC32 **before** you erase anything. If either is wrong, nothing has been erased yet, so fix the network and run `tftp` again.

```
setenv serverip 192.168.1.2
setenv ipaddr 192.168.1.146

tftp 0x80060000 yun-kernel.bin
crc32 $fileaddr $filesize
erase 0x9fea0000 +0x140000
cp.b $fileaddr 0x9fea0000 $filesize
cmp.b $fileaddr 0x9fea0000 $filesize

tftp 0x80060000 yun-rootfs.bin
crc32 $fileaddr $filesize
erase 0x9f050000 +0xe50000
cp.b $fileaddr 0x9f050000 $filesize
cmp.b $fileaddr 0x9f050000 $filesize

reset
```

- The kernel must be `1310720` bytes and the rootfs `15007744` bytes.
- Each `cmp.b` must report that the whole range is the same.
- Erasing and writing the rootfs takes several minutes, so let it finish.

The board then boots stock Linino with all its old settings. The kernel slot at 0x9fea0000 is also where Yun 2026 keeps its loader, so this puts the stock kernel back in its place.

**Type the addresses exactly.** The flash is mapped at 0x9f000000, and these commands must only touch 0x9f050000-0x9ffdffff:
- **Erasing below 0x9f050000 destroys U-Boot.** After that, only an external SPI flash programmer can revive the board.
- **Erasing 0x9fff0000 destroys the Wi-Fi calibration.** Only your `mtd6` backup can restore it.
