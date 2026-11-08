The Discord bot (`clipper bot`) posts a card per item in a thread per stream and answers its
buttons, and DMs you when something needs you ([Review](Review), [Reviewing](Reviewing)). Five
things on Discord's side, once:

1. **Create the app.** https://discord.com/developers/applications → New Application (name it
   after your channel). Under **Bot**: Add Bot, then Reset Token and copy the token. The bot
   needs no privileged intents: leave Presence, Server Members and Message Content off (it
   uses the `guilds` intent only).
2. **Save the token** as the `DISCORD_BOT_TOKEN` environment variable on the machine that runs
   the bot (`clipper setup` asks for it on a bot role and checks it against Discord), or in the
   file `[paths] discord_token` (`secrets/discord_token.txt`). Never in clipper.toml.
3. **Invite it to your server.** Under **OAuth2 → URL Generator** tick the scopes `bot` and
   `applications.commands`, and the bot permissions View Channels, Send Messages, Create Public
   Threads, Send Messages in Threads, Embed Links, Attach Files and Read Message History (plus
   Manage Channels if clipper may make `#weekly` for you). Open the URL it builds and pick the
   server.
4. **Copy the ids.** Discord → Settings → Advanced → Developer Mode on. Then right-click → Copy
   ID on the server (`[discord] guild_id`), on the review channel, a text or forum channel
   (`review_channel_id`: one thread or post per stream), and on yourself (`allowed_user_ids`;
   the first id gets the DMs unless `[notify] user_id` says otherwise). In clipper.toml:
   ```toml
   [discord]
   enabled = true
   guild_id = "..."
   review_channel_id = "..."
   allowed_user_ids = ["..."]
   ```
   `clipper review invite <user id>` adds another person later ([Reviewing](Reviewing)).
5. **Run it.** `clipper doctor --role bot` checks the token and the three ids; `clipper bot`
   (or its container, [Roles and services](Roles-and-services#services)) signs in, registers
   the slash commands on your server and starts answering. Cards appear at the end of `clipper
   review publish` (the worker's last stage) while `[discord] enabled` is true; `clipper review
   notify <stream>` posts a stream's cards by hand, and `clipper alert test` DMs you a test.

Settings worth knowing, all under `[discord]` and `[notify]` in `clipper.example.toml`:
`max_upload_mb` (10 on a plain server, 50 or 100 with boosts) decides how previews are shrunk
to fit; `thread_name` names the threads; `replan` reruns the schedule after each decision;
`refresh_minutes` keeps the cards in step with decisions made in Kitsu or the war room.
