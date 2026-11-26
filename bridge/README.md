# USB bridge for the remote

The SJRC remote is a USB **host** that expects an Android phone (Android Open Accessory). The DGX
Spark's USB ports are host-only too (checked: no USB device controller), so the remote cannot plug into
it directly. The bridge is a small Linux board with a USB **device** port that poses as the phone
and relays everything over the network, exactly like the Android app's PC link:

```
 remote ──USB──► bridge board ──Ethernet──► switch / Starlink router ──► DGX Spark (ground station)
                 poses as the phone          TCP 9000                        python3 -m pindad_gcs
```

The bridge sends the remote's heartbeats itself and forwards the ground station's flight messages; the
ground station finds it with `--link tcp://auto` (or `tcp://BRIDGE_IP:9000`).

**Status:** the network side is tested end to end with recorded drone data (video, telemetry and
commands both ways). The USB gadget side is written to the kernel and AOA specifications but **not yet
tested with the remote**: run it with the log visible the first time.

## Which board

| Board | USB device port | Network | Power |
| --- | --- | --- | --- |
| **Raspberry Pi 4 / Pi 5** (recommended) | USB-C (also the power input) | **Ethernet** | 5 V from the GPIO pins or a USB-C power + data splitter |
| Raspberry Pi Zero 2 W (bench tests) | micro-USB "USB" | 2.4 GHz Wi-Fi only | from the remote through the same cable |

Prefer Ethernet: the remote flies the drone on 2.4 GHz, and a 2.4 GHz Wi-Fi radio right next to it can
reduce the control range.

## Setup (Raspberry Pi OS Lite 64-bit)

1. In `/boot/firmware/config.txt`, under `[all]`, add and reboot:
   ```
   dtoverlay=dwc2,dr_mode=peripheral
   ```
2. Power (Pi 4/5): the remote cannot power these boards. Feed 5 V through the GPIO pins or a USB-C
   power + data splitter made for gadget mode, and do not connect the remote's 5 V line to the board's
   5 V supply (the splitter or a data-only adapter keeps them apart).
3. Copy this repository to the board (only Python 3 is needed, no extra packages), then:
   ```bash
   sudo ./bridge/setup_gadget.sh $USER                       # after every boot
   cd dgx-spark && python3 -m pindad_gcs.bridge              # the bridge, port 9000
   ```
4. Plug the remote's phone cable into the board's USB device port and switch the remote on. The log
   should show `Remote asked for accessory mode`, `Remote connected`, then `Ground station connected`
   when the DGX Spark connects.

To start at boot, use `pindad-bridge.service` (edit the user and path first):
```bash
sudo cp bridge/pindad-bridge.service /etc/systemd/system/ && sudo systemctl enable --now pindad-bridge
```

## Without the board

The Android app does the same job: phone on the remote, app open and connected, **PC control** on in
its Settings; the DGX Spark connects to the phone with `--link tcp://PHONE_IP:9000` (or `tcp://auto`).
