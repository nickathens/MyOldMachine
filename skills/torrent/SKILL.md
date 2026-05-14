# Torrent

Search torrent indexers via Jackett and download via aria2. VPN-gated by default. Two-step flow: search returns options, user picks, then download.

## Architecture

- **Jackett** runs in Docker on `127.0.0.1:9117` (localhost only). Aggregates ~500 public indexers behind one API.
- **search.py** queries Jackett, ranks by seeders, filters to safe range (>=5 seeders, <=50GB by default), returns top 10 as JSON.
- **download.py** verifies ProtonVPN is connected (Linux via NetworkManager, macOS via the ProtonVPN CLI), then runs aria2c one-shot. No seeding after download (`--seed-time=0`).
- Files land in `~/Downloads/torrents/`. Telegram cannot send files >2GB, so completion is a text-only summary with the path.

## One-time setup

The Jackett container is not started by this skill; you bring it up once and keep it running. Recommended (uses the `docker-services` pattern):

```bash
docker run -d --name jackett \
  --restart unless-stopped \
  -p 127.0.0.1:9117:9117 \
  -v ~/.config/jackett:/config \
  lscr.io/linuxserver/jackett:latest
```

Then:

1. Open http://127.0.0.1:9117 in a browser on this machine.
2. Click "Add indexer" and add the ones you want. Recommended starters:
   - **1337x** (general)
   - **YTS** (movies)
   - **EZTV** (TV)
   - **Nyaa** (anime)
3. Copy the API key from the top-right of the Jackett UI and save it:
   ```bash
   jq -r .APIKey ~/.config/jackett/Jackett/ServerConfig.json > ~/.jackett-api-key && chmod 600 ~/.jackett-api-key
   ```

Until the API key file exists and at least one indexer is configured, searches will fail or return empty.

## Usage

### Search

```bash
python <BOT_DIR>/skills/torrent/scripts/search.py "matrix 1999"
python <BOT_DIR>/skills/torrent/scripts/search.py "matrix 1999" --limit 15
python <BOT_DIR>/skills/torrent/scripts/search.py "ubuntu 24.04" --min-seeders 1 --max-size-gb 10
```

Output is JSON. Each result has:
```json
{
  "index": 1,
  "title": "The.Matrix.1999.1080p.BluRay.x264-...",
  "seeders": 1240,
  "size_gb": 2.3,
  "tracker": "1337x",
  "magnet": "magnet:?xt=urn:btih:...",
  "category": "Movies/HD"
}
```

### Download

```bash
python <BOT_DIR>/skills/torrent/scripts/download.py --magnet "magnet:?xt=urn:btih:..."
python <BOT_DIR>/skills/torrent/scripts/download.py --magnet "..." --no-vpn
```

Default behavior:
- Refuses to start unless ProtonVPN is connected (override with `--no-vpn` for trusted content only).
- Size cap (50GB default) is enforced at search time, so any magnet from `search.py` is already under the cap.
- `--seed-time=0`: leeches only, no seeding after download completes.
- Lands in `~/Downloads/torrents/`.

If the VPN is off, connect via the `vpn` skill (`vpn.py connect --country NL`). Pass `--no-vpn` only for content you know is not copyright-restricted (Linux ISOs, public domain, your own backup seeds).

## Typical flow

1. User: "torrent matrix 1999"
2. Run `search.py "matrix 1999"`, parse JSON, present top results.
3. User picks number 3.
4. Run `download.py --magnet "<magnet from result 3>"`.
5. On completion, report file path and size.

## Notes

- Quality is shown via the title text only (e.g. "1080p", "2160p", "WEBRip"). Auto-pick by quality is intentionally not done; user picks based on the trade-off they want.
- The VPN status check is cross-platform: Linux uses `nmcli` (matching the `vpn` skill), macOS shells out to `protonvpn status`. It runs at the start of the download only — if the VPN drops mid-download, traffic is exposed. For belt-and-suspenders, enable the ProtonVPN killswitch in the GUI so the system blocks all traffic if the tunnel drops.
- If Jackett returns zero results, either no indexers are configured yet, or the query is too narrow. Check http://127.0.0.1:9117.
- Upload is capped at 1KB/s and `--seed-time=0` stops seeding immediately after download — minimal swarm participation. Public indexers don't enforce ratios, so this is fine.
