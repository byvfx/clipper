`clipper x` posts scheduled Shorts to X as native video through the X API v2 (pay-per-use:
credits bought in the developer console, about $0.015 a post; a post with a link costs $0.20,
so clipper never puts one in). The text is the Short's title without the "KINO CASINO | "
prefix (`[x] hook_first`: the feed shows only the first words), then up to `[x] max_hashtags` (3)
hashtags ending with the show's own (`#KinoCasino`, `[x] show_hashtag`), then a credit line
tagging whose footage it is: `[x] credit` ("From {show}: {mentions}") with the show's `[x] mentions`
(set them in `[shows.<key>.x]`, e.g. `mentions = ["Andywarski", "PoweroftheTruth"]`; each one gets
notified; no mentions, no credit line). Shorts taller than `[x] max_height` (1280) are re-encoded to 720x1280 first, X's
recommended portrait size.

1. **Developer app** at console.x.com, signed in as the X account the posts go out from.
   Buy a few dollars of credits and set a spending limit. Create an app; under **User
   authentication settings** turn on OAuth 2.0, permissions **Read and write**, type of app
   **Native App**, callback URI `http://127.0.0.1:3456/callback` (the port is
   `[x] redirect_port`) and a website URL.
2. **Keys**: copy the **OAuth 2.0 Client ID** (Keys and tokens) and save
   `{"client_id": "..."}` as `secrets/x_client.json`. A **Web App** (confidential client)
   works too: add `"client_secret": "..."`.
3. **Sign in** once: `clipper x --sign-in`, with the browser signed in to X as that account.
   The token lands in `secrets/x_token.json`; it lasts 2 h and refreshes itself (scope
   `offline.access`). `clipper doctor` checks the scopes.
4. **Post**: `clipper x --items kick-131151926/v04_t21 --dry-run` shows the file and text;
   without `--dry-run` it posts. `clipper x` lists what was posted, with links. A failed post
   is recorded (`clipper x` shows the error) and `--items` tries it again.
5. **Automatic** (optional): `clipper x --auto` posts approved, scheduled Shorts from streams
   that aired in the last `[x] max_age_hours` (72) and aren't on X yet, newest stream first, up
   to `max_per_24h` (6); titles saying "host(s)" are skipped. It doesn't wait for YouTube: on X a
   clip is worth most while the topic is hot. `[x] after_youtube = true` waits until each Short
   is public on YouTube (checked with YouTube) instead. Set `[x] auto_post_minutes` (e.g. 60) and restart
   serve to have it run that for you.
6. **Weekly issue**: `clipper x --issue --dry-run` shows the newest issue as a thread (X takes four
   images a post: pages 01-04, 05-08, 09-10) with its text; without `--dry-run` it posts. To change
   the text, put a list of three strings in the issue's `issue.json` under `post.x`. A run that
   failed halfway carries on as replies to the last post (`weekly/<Saturday>/x.json`). It needs all ten
   pages, every caption within `[x] max_chars`, and room for the whole thread in `max_per_24h`, which
   issue posts share with Shorts; a second run at the same time is refused.

Each post's cost is logged like a model call (`clipper costs`, kind `x`).
