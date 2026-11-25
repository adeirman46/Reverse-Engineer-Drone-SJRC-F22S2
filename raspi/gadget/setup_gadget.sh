#!/usr/bin/env bash
# Creates the USB gadget that lets the SJRC remote plug into this Raspberry Pi as if it were the
# Android phone (Android Open Accessory: 18d1:2d00, FunctionFS). Run as root after every boot
# (the systemd unit does it). Then start the app with --link gadget; it binds the gadget itself.
#
# One-time setup on a Raspberry Pi 5: add to /boot/firmware/config.txt (under [all]):
#     dtoverlay=dwc2,dr_mode=peripheral
# and reboot. The USB-C port then works as a USB device port.
set -euo pipefail
USER_NAME="${1:-${SUDO_USER:-pi}}"
G=/sys/kernel/config/usb_gadget/pindad
FFS=/dev/ffs-pindad

modprobe libcomposite
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
if [ ! -d "$G" ]; then
  mkdir -p "$G"
  echo 0x18d1 > "$G/idVendor"     # Google: Android Open Accessory
  echo 0x2d00 > "$G/idProduct"    # accessory mode
  echo 0x0200 > "$G/bcdUSB"
  echo 0x0100 > "$G/bcdDevice"
  mkdir -p "$G/strings/0x409"
  echo "Raspberry Pi" > "$G/strings/0x409/manufacturer"
  echo "Pindad UGV-UAV ground station" > "$G/strings/0x409/product"
  echo "0001" > "$G/strings/0x409/serialnumber"
  mkdir -p "$G/configs/c.1/strings/0x409"
  echo "AOA" > "$G/configs/c.1/strings/0x409/configuration"
  echo 100 > "$G/configs/c.1/MaxPower"
  mkdir -p "$G/functions/ffs.pindad"
  ln -s "$G/functions/ffs.pindad" "$G/configs/c.1/"
fi
mkdir -p "$FFS"
mountpoint -q "$FFS" || mount -t functionfs -o uid="$(id -u "$USER_NAME")",gid="$(id -g "$USER_NAME")" pindad "$FFS"
chown "$USER_NAME" "$G/UDC"   # lets the app bind and re-bind the gadget without root
echo "USB gadget ready for $USER_NAME. UDCs available: $(ls /sys/class/udc)"
