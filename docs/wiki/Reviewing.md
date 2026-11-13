You approve what goes out. Nothing is uploaded without a person's approve, and every decision
lands in Kitsu whichever surface you make it on. Three surfaces; pick what fits the moment:

| | Discord | Kitsu | War room |
|---|---|---|---|
| Where | the server's review channel, a thread per stream | `http://<host>:8090`, your login | the page `clipper serve` serves; on a phone through the tailnet |
| Good for | the phone, one card at a time | the detail: columns, comments, every revision | the queue at once, bulk edits, covers, the schedule |

## Getting in

- **Discord**: the operator runs `clipper review invite <your user id>` (Discord → Settings →
  Advanced → Developer Mode on, then right-click your name → Copy User ID) and restarts the
  bot. Until then the bot answers "You're not on this bot's allowlist".
- **Kitsu**: a login from the operator ([Kitsu](Kitsu)); the show is a production you are on.
- **War room**: a link from the operator, `https://<name>.ts.net/?key=...` through their
  tailnet; open it once and it keeps a cookie for a year ([Phone access](Phone-access)). Your
  phone has to be on the same tailnet. (The war room listens on localhost only unless the
  operator sets `[warroom] host = "0.0.0.0"` and a token; only then is `http://<host>:8765/?key=...`
  on the LAN an option.)

## The card (Discord)

Each item is one card: the preview (480p, the first 60 s of a long item), title, hook, the Kitsu
link, and buttons. **Approve**. **Reject…** with a reason. **Changes…** says what to change:
the item is re-cut or re-covered and comes back as a new card. **Hold** keeps it out of the
schedule until you unhold it. **Reopen** undoes a decision. **Pin time…** sets its publish time.
**Retitle…** edits the YouTube title, description, hashtags, tags and playlist in one form.
**Thumbnail text…** changes the cover's words; the new cover replaces the old one. Slash
commands: `/status`, `/pending`, `/item`, `/schedule`, `/post`. There is no bulk approve: every
release needs its own.

## The same words on each surface

| What you mean | Discord | Kitsu | War room |
|---|---|---|---|
| Yes, publish it | Approve | status **approved** (or done) | approve |
| No | Reject… | status **rejected** | reject |
| Change it and show me again | Changes… | status **retake** with a comment | retake note |
| Keep it back for now | Hold | the **Hold** column | hold |
| Publish at this time | Pin time… | the **Publish at** column | pin |
| Fix the words | Retitle…, Thumbnail text… | YouTube title / description, Thumbnail text columns | the item's fields |

The war room's buttons and keys are on [War room](War-room). Approved items go into the next
free slots and are uploaded private with a publish time ([Scheduling and uploads](Scheduling-and-uploads));
a hold or a pin moves them. A Short that arrives **held** carries other people's voices over the
limit ([Review](Review#shorts-held-for-other-voices)): release the hold if the show's own
commentary carries it, reject it or ask for a trim if not.
