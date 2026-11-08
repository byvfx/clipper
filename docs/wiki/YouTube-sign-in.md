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
   youtube_client_secret`) on the machine that uploads (serve's).
5. **Sign in.** `clipper upload --sign-in` opens a browser: pick the Google account and, when it
   asks, **the channel**. A Brand Account channel shows as its own choice; picking the personal
   identity leaves clipper with no channel to upload to. It asks for the upload and
   `youtube.force-ssl` scopes (captions, playlists). The token lands in
   `secrets/youtube_token.json` and refreshes itself; `clipper doctor --role serve` (online)
   checks it, and `clipper upload --sign-in` again redoes it.

Only serve uploads, so only that machine signs in: `clipper doctor --role serve` warns when the
two files are missing, and other roles don't need them. What a new channel does to the first
uploads (verification, the daily cap, Studio as the fallback) is [YouTube channel](YouTube-channel).
