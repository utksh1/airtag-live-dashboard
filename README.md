# AirTag Live Dashboard

Web dashboard and tracker for OpenHaystack and ESP32 Find My devices.

## Features

- Real-time location reporting from Apple Find My network.
- Interactive map interface with Leaflet.
- Support for multiple tracking devices.
- Reverse geocoding for address resolution.
- Trip metrics (distance, stops, and duration).
- Route animation playback with time controls.
- Configurable geofencing with perimeter breach notifications.
- Integrated Anisette v3 service.

## Configuration

Set the environment variables below in your hosting environment:

| Variable | Description |
|---|---|
| `PORT` | Web server port (Render sets this automatically). |
| `ACCOUNT_JSON_B64` | Base64-encoded content of `account.json`. |
| `TAG_JSON_B64` | Base64-encoded content of primary tag file (`mytag.json`). |
| `<NAME>_TAG_B64` | Base64-encoded content for additional tag files. |

## How To Add Other Devices

1. Generate keys for the new device:
   ```bash
   ./haystack keys -o dev2.keys
   ```
2. Flash the new ESP32 board:
   ```bash
   ./haystack flash -p <PORT> -k dev2.keys -m esp32c3-generic
   ```
3. Generate the tag definition JSON:
   ```bash
   ./haystack keys -o dev2.json
   ```
4. Base64-encode the new JSON file:
   ```bash
   base64 -i dev2.json
   ```
5. Add an environment variable in Render:
   - Key: `DEV2_TAG_B64`
   - Value: `<base64-string>`
6. The dashboard will automatically discover and show the new device on the map.

