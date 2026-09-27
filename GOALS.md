# Arduino Yún 2026: Project Goals

## What this is

Arduino Yún 2026 is an up-to-date OpenWrt firmware for the Arduino Yún. It brings the Yún's Linux side onto current OpenWrt and keeps the Arduino features that made the board useful.

## Why

The Yún has two processors. An ATmega32U4 runs your sketches, and an Atheros AR9331 runs Linux.

The Linux side shipped with Linino, an OpenWrt-based firmware that hasn't been updated in years. Its kernel, SSL libraries and SSH server are badly out of date, and most of its package mirrors are gone.

## Goals

- Run a current OpenWrt release with an up-to-date kernel and security fixes
- Install packages from the official OpenWrt feeds with `apk`
- Keep the Bridge library working, so existing sketches run unchanged
- Upload sketches over Wi-Fi from the Arduino IDE
- Set up Wi-Fi from the Yún web panel
- Keep YunSerialTerminal, the SD card, the LEDs and the reset buttons working

## Supported hardware

Only the original Arduino Yún (Rev1) is supported. The Yún Rev2 and Yún Mini are not.

## Status

The project is at an early stage, and there's no firmware to download yet.
