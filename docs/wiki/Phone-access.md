The war room listens on localhost only. To open it from the phone anywhere, put it on your
tailnet with `tailscale serve`: HTTPS at the machine's tailnet name, reachable only by your
own Tailscale devices, nothing on the LAN or the internet, no port forwarding.

1. Install Tailscale on the machine that runs `clipper serve` (https://tailscale.com/download) and
   on the phone (App Store / Play Store), and sign in to the same account on both.
2. In the Tailscale admin console, DNS page: turn on MagicDNS and HTTPS certificates
   (`tailscale serve` asks for this the first time if it is off).
   The first `on` may also print a link to enable Serve for the tailnet: open it, allow, and
   the command carries on.
3. `clipper warroom --tailnet on`. It makes a war room token in `secrets/warroom_token.txt`
   if `[warroom] token` is empty, starts `tailscale serve` (it persists across reboots), and
   prints the link to open once on the phone (`https://<name>.ts.net/?key=...`). The key
   is printed only there; the phone keeps a cookie for a year.
4. Restart serve (`clipper services restart serve`) so the war room asks for the
   token, and set `[warroom] tailnet = true` in clipper.toml.
5. On the phone, open the link, then Share / menu → Add to Home Screen.

Check: `clipper doctor --role serve` (fetches the war room through the tailnet name), or
`clipper warroom --tailnet status`. Off again: `clipper warroom --tailnet off`. When serve
moves to the NAS (#9), do the same there with the Tailscale container.
