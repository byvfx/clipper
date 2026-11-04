`clipper people` keeps one record per person: their name and other names (including Whisper's
spellings), their channels on each platform, their [archive](Archives) subject and their
[cast library](Cast-library) folder. [Story dossiers](Story-dossiers) find the people a brief
names through it, and [investigations](Investigations) use it to merge spellings and to know which
names are the subject himself. It is part 1 of #138: later parts build on it (people found on
the fly, who said what, the Obsidian vault).

## Where records come from

`people.json` (`[paths] people`, gitignored) is the record. Until everyone is in it, `clipper
people` also folds in the older lists, so nothing has to move at once:

- `research_notes/streamers.json` (`[paths] streamers`): its `streamers` and `uploads_only`
- `[story] people` in clipper.toml
- the cast library's folders (`[thumbnail] photos`): their aliases and channels, and the folder
  itself as the person's `cast`

Entries are one person when any of their names match: the name, the words in its brackets
("The Quartering (Jer)" answers to Jer), the id and the aliases, ignoring case, punctuation and
a leading "the". A field `people.json` sets wins, and lists are joined. An archive subject named
like one of a person's names (`archive/jer`) is theirs unless a record says another.

Channels are confirmed ones only: typed by hand or from a hand-kept list, never guessed by a
model. The platforms are `youtube`, `kick`, `twitch`, `rumble`, `odysee` and `x`.

## Commands

```bash
clipper people list                         # everyone, and which lists each record comes from
clipper people show jer                     # one record, by any of their names
clipper people import --dry-run             # who would be written, and names two people share
clipper people import                       # write everyone into people.json
clipper people set Don --alias "Don Flurgundy"           # makes two entries one person
clipper people set jer --alias Jeremy --channel x=TheQuartering --channel https://odysee.com/@TheQuartering
clipper people set "Ben Avery" --channel youtube=@BenAvery   # a new person
clipper people set jer --subject jer --cast Jer --notes "Rumble first"
```

`set` writes the person's whole merged record into `people.json` (adding them if nobody has that
name); `--name` renames and keeps the old name as an alias. `--unalias` drops an alias, but a
name that comes from `streamers.json` or a cast folder comes back from there: fix it at the
source. `clipper doctor` (all and serve roles) warns when two people share a name, since a lookup
by it is then ambiguous and investigate won't merge on it.
