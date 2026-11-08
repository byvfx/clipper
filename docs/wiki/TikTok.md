> [!CAUTION]
> **Parked (2026-11-01).** We post to TikTok by hand. Auto-send is off (TikTok flagged the drafts as
> bot activity), and direct posting is dropped: TikTok's app audit refuses tools that only post to
> your own accounts (issues #62, #116). The steps below still work if this is ever picked up again.

`clipper tiktok` sends scheduled Shorts to the TikTok app's drafts (Content Posting API,
Upload route). You open the notification on the phone, paste the caption and post. TikTok's
API takes no caption for drafts, so clipper posts it to Discord (`[tiktok] caption_channel_id`,
else the `[record]` alert webhook or channel) and keeps it in schedule.json.

1. **Developer app** at developers.tiktok.com, owned by an organization: platform Desktop,
   products **Login Kit** (redirect URI `http://localhost:3455/callback/`, the port is
   `[tiktok] redirect_port`) and **Content Posting API** (leave Direct Post off), scopes
   `user.info.basic` and `video.upload`. It needs a website, privacy and terms URL; ours are
   pages on byvfx.com ([docs/tiktok-app-pages.md](https://github.com/byvfx/clipper/blob/main/docs/tiktok-app-pages.md)). Testing works in **Sandbox** with the TikTok
   account added as a target user.
2. **Keys**: save `{"client_key": "...", "client_secret": "..."}` as `secrets/tiktok_client.json`.
3. **Sign in** once, as the TikTok account: `clipper tiktok --sign-in`. The token lands in
   `secrets/tiktok_token.json`; it refreshes itself and the sign-in lasts 365 days
   (`clipper doctor` warns two weeks before).
4. **Send**: `clipper tiktok --items <stream>/v01 --dry-run` shows the file and caption;
   without `--dry-run` it sends. `clipper tiktok` lists what was sent and marks drafts you
   posted as posted. The draft arrives as a notification in the TikTok app's **Inbox**
   (System notifications), not under Profile > Drafts; it becomes a draft once opened.
5. **Captions**: create a Discord text channel (ours is #tiktok; the bot can't create channels)
   and set `[tiktok] caption_channel_id`. Each send posts a note and then the bare caption, so
   Copy Text on the phone copies just the caption. `clipper tiktok --caption <item>` posts it again.
   A draft left unposted expires in the app (TikTok's status still says it is in the inbox):
   `clipper tiktok --items <item>,... --resend` sends it again and keeps the old send under
   `previous` as expired, which frees its place in the 24 h count.
6. **Automatic** (optional): `clipper tiktok --auto` sends the next unsent scheduled Shorts, earliest
   YouTube slot first, as many as `max_per_24h` allows (captions saying "host(s)" are skipped).
   Set `[tiktok] auto_send_minutes` (e.g. 60) and restart serve to have it run that for you.

Limits: TikTok allows at most 5 pending drafts in 24 h; `[tiktok] max_per_24h` (default 3,
at most 5) stays under it. Most accounts can post up to 3 minutes. Direct posting without the phone step needs
TikTok's app audit, which requires an app meant for other creators, not your own accounts (#62).
