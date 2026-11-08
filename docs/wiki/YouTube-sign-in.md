`clipper upload` talks to YouTube as your channel through Google's Data API, with an OAuth
"Desktop app" client you make once in Google Cloud. None of this costs money; the API has a
daily quota ([YouTube channel](YouTube-channel), point 8).

1. **A Cloud project.** https://console.cloud.google.com → New project (any name).
2. **Enable the API.** APIs & Services → Library → *YouTube Data API v3* → Enable.
3. **The consent screen.** APIs & Services → OAuth consent screen (Google Auth Platform):
   External, your app's name and your email; no scopes to add here. Then publish it
   (**Audience → Publish app**, "In production"): while it stays in Testing, Google expires the
   sign-in every 7 days and uploads stop with `invalid_grant`. You click past the "unverified
   app" warning once; no verification is needed to upload to your own channel.
4. **The client.** Credentials → Create credentials → OAuth client ID → **Desktop app**.
   Download the JSON and save it as `secrets/youtube_client_secret.json` (`[paths]
   youtube_client_secret`) on the machine you will sign in from (step 5).
5. **Sign in on a machine with a browser** (your laptop): `clipper upload --sign-in` opens one.
   Pick the Google account and, when it asks, **the channel**: a Brand Account channel shows as
   its own choice, and the personal identity has no channel to upload to. It asks for the
   upload and `youtube.force-ssl` scopes (captions, playlists), and the token lands in
   `secrets/youtube_token.json`. The sign-in needs the browser on the same machine (Google calls
   back to localhost), so a headless serve, the containers here, never signs in itself: copy
   both files into the `secrets/` folder serve reads (on the NAS share here), as private as the
   Discord token. The token refreshes itself wherever it is used; `clipper doctor --role serve`
   (online) checks it, and when it stops working, sign in again the same way and copy the token
   over.

Only serve uploads: `clipper doctor --role serve` warns when the two files are missing, and other
roles don't need them. What a new channel does to the first uploads (verification, the daily
cap, Studio as the fallback) is [YouTube channel](YouTube-channel).
