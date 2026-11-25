#!/usr/bin/env python3
"""
studio_manifest.py - turn an Android Studio manifest (resource references, symbolic values, no
package/version) into the flat form axml_encode.py takes, for building without aapt2.

Usage: studio_manifest.py <in.xml> <out.xml> <package> <versionCode> <versionName> <minSdk> <targetSdk> <label>
Resolves: @string/app_name -> label, @mipmap/ic_launcher(_round) -> the IDs arsc_encode.py assigns,
@style/... -> the framework theme its parent names (Theme.Material.NoActionBar.Fullscreen),
enum and flag attributes -> numbers, ".Class" -> package + ".Class".
"""
import sys
import xml.etree.ElementTree as ET

NS = "http://schemas.android.com/apk/res/android"
A = "{" + NS + "}"
ENUMS = {
    "launchMode": {"standard": "0", "singleTop": "1", "singleTask": "2", "singleInstance": "3"},
    "screenOrientation": {"unspecified": "-1", "landscape": "0", "portrait": "1", "user": "2", "behind": "3",
                          "sensor": "4", "nosensor": "5", "sensorLandscape": "6", "sensorPortrait": "7",
                          "reverseLandscape": "8", "reversePortrait": "9", "fullSensor": "10"},
}
FLAGS = {
    "configChanges": {"mcc": 0x1, "mnc": 0x2, "locale": 0x4, "touchscreen": 0x8, "keyboard": 0x10,
                      "keyboardHidden": 0x20, "navigation": 0x40, "orientation": 0x80, "screenLayout": 0x100,
                      "uiMode": 0x200, "screenSize": 0x400, "smallestScreenSize": 0x800, "density": 0x1000,
                      "layoutDirection": 0x2000, "fontScale": 0x40000000},
    "foregroundServiceType": {"dataSync": 0x1, "mediaPlayback": 0x2, "phoneCall": 0x4, "location": 0x8,
                              "connectedDevice": 0x10, "mediaProjection": 0x20, "camera": 0x40, "microphone": 0x80},
}
REFS = {
    "@mipmap/ic_launcher": "@0x7f010000",       # arsc_encode.py: first entry
    "@mipmap/ic_launcher_round": "@0x7f010001",  # second entry
    "@style/Theme.PindadUgvUav": "@0x0103022f",  # android:style/Theme.Material.NoActionBar.Fullscreen
}


def main():
    src, out, package, vcode, vname, min_sdk, target_sdk, label = sys.argv[1:9]
    ET.register_namespace("android", NS)
    tree = ET.parse(src)
    root = tree.getroot()
    root.set("package", package)
    root.set(A + "versionCode", vcode)
    root.set(A + "versionName", vname)
    root.insert(0, ET.Element("uses-sdk", {A + "minSdkVersion": min_sdk, A + "targetSdkVersion": target_sdk}))
    refs = dict(REFS, **{"@string/app_name": label})
    for el in root.iter():
        for key, value in list(el.attrib.items()):
            name = key.replace(A, "")
            if value in refs:
                el.set(key, refs[value])
            elif name in ENUMS and value in ENUMS[name]:
                el.set(key, ENUMS[name][value])
            elif name in FLAGS:
                el.set(key, hex(sum(FLAGS[name][f] for f in value.split("|"))))
            elif name == "name" and value.startswith("."):
                el.set(key, package + value)
            elif value.startswith("@") and not value.startswith("@0x"):
                sys.exit(f"studio_manifest.py: cannot resolve {value} ({name})")
    tree.write(out, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
