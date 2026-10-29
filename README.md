# Buckleys UCP1D — BlueOS Extension

Live monitoring and CSV logging for the **Buckleys UCP1D Digital Contact Probe** (underwater cathodic-protection potential), connected to a BlueOS vehicle through a USB-to-RS-485 adapter.

- Streams potential in millivolts using the probe's continuous-output mode (`M3`) at 2 Hz by default (`IN500`).
- Shows a live line graph with 1, 5, 15 and 60 min windows, plus min/max/mean for the window.
- Logs to CSV with timestamps from the BlueOS system clock (UTC). A new file starts each time the probe connects or logging is started.
- Lets you download and delete log files from the web UI.
- Reads the MCU temperature (`TE`), and has a raw ASCII command console for the other commands (`SW`, `ME`, `M0`–`M3`, …).

## Wiring

| UCP1D wire | Connect to |
|---|---|
| Red | Supply + |
| Black | Supply − |
| Orange | RS-485 **A** |
| Brown | RS-485 **B** |

Plug the USB-RS485 adapter into the vehicle computer. With the port set to **Auto-detect**, the extension picks the first `/dev/serial/by-id/*` device, or failing that a recognised USB-serial chip (FTDI, CH340, CP210x, PL2303).

## Settings

| Setting | Default | Notes |
|---|---|---|
| Serial port | Auto-detect | Choose a specific `/dev/serial/by-id/...` path if more than one adapter is attached |
| Baud rate | 9600 | This is the *host* port rate and must match the probe. 115200 is also listed under Common |
| Output interval | 500 ms | Sent as `IN<ms>`; range 100–60000 |

**Advanced → Change probe baud** sends `BD<rate>` to the probe, then reconnects at the new rate. If the probe doesn't accept the change, select its real baud rate under Settings to get it back.

### Protocol notes

The manual doesn't give the line terminator or reply format. The extension assumes:

- Commands are ASCII and end with `\r\n`, with no space between command and argument (`IN500`, `BD9600`).
- Replies are split on CR or LF. The first number found in a line is taken as the value.
- For one-off queries (`SW` at connect time and `TE`), streaming is paused with `M1`, the query is sent, and `M3` resumes streaming.

If the real probe behaves differently, change the constants at the top of `app/probe.py` (`EOL`, `DEFAULT_BAUD`, …).

## Data

CSV files are written to `/usr/blueos/extensions/buckleys-ucp1d/` on the vehicle, which is `/app/data` inside the container:

```
timestamp_utc,epoch_s,potential_mV
2026-09-28T21:40:07.099+00:00,1790631607.099,-1012.3
```

Settings are saved in `settings.json` in the same folder.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt
cd app
UCP_SIMULATE=1 UCP_DATA_DIR=../data ../.venv/bin/uvicorn main:app --port 8000
# open http://localhost:8000
```

`UCP_SIMULATE=1` swaps the serial port for a built-in fake probe that outputs about −1010 mV plus noise.

## Building / deploying

`.github/workflows/deploy.yml` uses [BlueOS-community/Deploy-BlueOS-Extension](https://github.com/BlueOS-community/Deploy-BlueOS-Extension). It builds `linux/arm/v7`, `linux/arm64/v8` and `linux/amd64` images and pushes `<DOCKER_USERNAME>/blueos-buckleys-ucp1d`. It runs on pushes to `main`, on tags (a SemVer tag such as `0.1.0` also pushes `latest`), and manually.

Repository **secrets** (required):

- `DOCKER_USERNAME`: Docker Hub username (lowercase)
- `DOCKER_PASSWORD`: Docker Hub access token

Repository **variables** (optional):

- `IMAGE_NAME`: defaults to `buckleys-ucp1d`
- `MY_NAME`, `MY_EMAIL`: the author
- `ORG_NAME`, `ORG_EMAIL`: the maintainer

Create the Docker Hub repository `blueos-buckleys-ucp1d` before the first run.

### Manual install on BlueOS

In BlueOS go to **Extensions → Installed → +** and enter:

- Extension identifier: `bluerobotics.buckleys-ucp1d`
- Extension name: `Buckleys UCP1D`
- Docker image: `<DOCKER_USERNAME>/blueos-buckleys-ucp1d`
- Docker tag: `main` (or a release tag)
- Custom settings: paste the JSON below (this is the Dockerfile's `permissions` label with `$IMAGE_NAME` filled in)

```json
{
  "ExposedPorts": {
    "8000/tcp": {}
  },
  "HostConfig": {
    "Privileged": true,
    "Binds": [
      "/usr/blueos/extensions/buckleys-ucp1d:/app/data",
      "/dev:/dev"
    ],
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "PortBindings": {
      "8000/tcp": [
        {
          "HostPort": ""
        }
      ]
    }
  }
}
```

If you set a different `IMAGE_NAME` variable, change `buckleys-ucp1d` in the first bind path to match.

The container runs privileged with `/dev` bind-mounted, so an adapter plugged in after the extension starts is still found.
