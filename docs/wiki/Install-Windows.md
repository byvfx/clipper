1. **Tools**
   ```powershell
   winget install astral-sh.uv
   winget install Gyan.FFmpeg      # the "full" build: libass captions, loudnorm, QSV/NVENC
   ```
   Open a new terminal so both are on PATH.

2. **Code**: copy the repo folder (everything except `streams/`, `.venv/`, `logs/`) to
   `C:\clipper` (or anywhere), then in it:
   ```powershell
   uv sync --extra gpu --extra faces --extra discord   # worker + serve + bot; a bot-only machine needs only --extra discord
   ```
   The `gpu` extra pulls torch 2.8 with CUDA 12.8 from the PyTorch index (pinned in
   pyproject.toml). The CUDA toolkit is not needed; the wheels bring the runtime and cuDNN.
   If `clipper doctor` says torch sees no CUDA GPU, the CPU wheel slipped in:
   ```powershell
   uv pip install --reinstall torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
   ```

3. **Config**: copy `clipper.toml` from the main machine. Things to change per machine:
   - `[paths] streams`: the NAS share, e.g. `"//nas/clipper/streams"` or a
     mapped drive. Every machine must see the same streams folder.
   - `[cut] clip_codec`: `h264_qsv` needs an Intel GPU; on an NVIDIA-only desktop use
     `h264_nvenc` (driver 610+ for ffmpeg 8.1) or `libx264`. `clipper doctor` test-encodes it.
   - `[transcribe] compute_type = "int8_float16"` and `batch_size = 4` on cards under 8 GB.
   Anything can also be set per machine in `clipper.local.toml` (what `clipper setup` writes)
   or with an env var `CLIPPER_<SECTION>_<KEY>`, e.g. `CLIPPER_CUT_CLIP_CODEC=libx264`, so one
   shared clipper.toml can stay identical.

4. **Prompts and secrets**: copy `prompts/` (it comes with the repo) and `secrets/`
   (`discord_token.txt`, `youtube_client_secret.json`, `youtube_token.json`; only the
   machine that uploads needs the YouTube files).

5. **Keys** as *user* environment variables (System > About > Advanced system settings >
   Environment Variables, or):
   ```powershell
   [Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', '<key>', 'User')
   [Environment]::SetEnvironmentVariable('KITSU_TOKEN', '<Kitsu bot token>', 'User')
   ```
   Add `ANTHROPIC_WORKSPACE_ID` if the key isn't scoped to a workspace. Open a new terminal.

6. **Fonts**: thumbnails and Short captions use Impact and Bahnschrift, both standard on
   Windows. On Linux install `ttf-mscorefonts-installer` (Impact)
   and Roboto Condensed (`fonts-roboto-unhinted`). The [weekly issue](Weekly-issue) needs a few more.

   Optional: [cover layouts and photos](Covers), the [cast library](Cast-library), [faces](Faces).

7. **Check**: `clipper doctor --role gpu` (or `--role serve`, `--role bot`).

8. **Run unattended.** Serve and the bot run as containers (Docker compose, `deploy/hub`, on
   the NAS or any Docker host: [Roles and services](Roles-and-services#services)). On Windows
   the scheduled task is for the GPU worker (at logon, restarted if it exits, no admin needed;
   the scripts share `deploy\common.ps1`, which lists the user env vars the task copies in,
   since scheduled tasks don't always see ones set after logon: a new key goes there too):
   ```powershell
   deploy\worker\worker-task.ps1 -Install     # GPU worker
   ```
   `-Status`, `-Restart`, `-Stop`, `-Uninstall` work. Logs go to `logs\`. `clipper services`
   shows the task plus the recorder and any NAS cuts, with serve's and the bot's heartbeats
   from the share; `clipper services restart worker` runs the same script, and `serve` or `bot`
   over ssh once `[serve] hub` names the compose folder. The bot reaches serve at `[serve] url` (empty = this
   machine); on different machines set it, give both the same `[warroom] token` (or
   `secrets/warroom_token.txt`), and put `[paths] schedule` and `[paths] news` under the
   streams share on both (the bot reads what serve writes there; `clipper doctor --role
   bot` checks).
