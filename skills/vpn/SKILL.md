# VPN

Control a ProtonVPN connection on Linux or macOS.

## Commands

```bash
# Check VPN status
python <BOT_DIR>/skills/vpn/scripts/vpn.py status

# Connect (fastest server)
python <BOT_DIR>/skills/vpn/scripts/vpn.py connect

# Connect to a specific country
python <BOT_DIR>/skills/vpn/scripts/vpn.py connect --country NL
python <BOT_DIR>/skills/vpn/scripts/vpn.py connect --country US

# Disconnect
python <BOT_DIR>/skills/vpn/scripts/vpn.py disconnect

# List available countries
python <BOT_DIR>/skills/vpn/scripts/vpn.py countries
```

## Important

- The ProtonVPN CLI and GUI app cannot run simultaneously. The script stops the GUI before issuing CLI commands. It does NOT restart the GUI automatically -- the user can relaunch it manually if they want it back.
- Credentials must already be stored on the machine (sign in via the GUI or CLI once before using the skill).
- Linux: requires the official `protonvpn` CLI (https://protonvpn.com/support/linux-vpn-tool/). Status uses NetworkManager (`nmcli`) and the `proton0` interface.
- macOS: requires the ProtonVPN app (`brew install --cask protonvpn`). The optional community CLI `protonvpn-cli` is also detected. GUI is quit via `osascript`. Status is parsed from the CLI's own `status` output.
- The script never stores credentials, only invokes the installed CLI.

## Examples

User: "connect to vpn"
User: "vpn status"
User: "connect vpn to netherlands"
User: "disconnect vpn"
User: "what vpn countries are available?"
