`clipper faces <stream>` (#54) finds who is on screen: every keyframe
(one every ~4 s on Kick) through two small face models OpenCV runs on the CPU, no GPU or
Modal. An 8 h stream takes ~15 min on a 16-core laptop, most of it reading the video off the
NAS share. It is a worker stage (`faces` in `[worker] stages`), run after the fast lane and before
the slow cut, so the picks and Shorts reach review without waiting and the segment and
compilation covers get it; run it by hand before `clipper thumbs` for anything else. Enquirer covers
(prompt v5) then get each named person's best frames as candidates, labelled with who is in them,
and use the found head box when the main face is one (`face_found` in thumbnails.json). The weekly
takes each named person's best faces from every stream of the week (`[weekly] face_frames`, 3)
ahead of the thumbnail stage's frames. A machine without the `faces` extra must take `faces` out of
`[worker] stages`; `clipper doctor` fails until one or the other is done.

1. `uv sync --extra faces` (add it to the other extras you use). The models (YuNet and SFace,
   from OpenCV's model zoo, MIT and Apache-2.0) download on the first scan into `[paths]
   faces/models`, checked against pinned hashes.
2. `clipper faces <stream>` writes `faces.json` and `faces/F01.jpg`... one contact sheet per
   group of faces, biggest first. Groups that aren't people (game characters, puppets, a
   poster on the wall) are fine to leave unnamed.
3. Name the regulars once from the sheets: `clipper faces <stream> --name F01=Andy --name
   F03=Jer --enroll` keeps them in `[paths] faces/known.json`, one file for every show. Later
   streams are named by face (`[faces] match`). Enroll from an ordinary stream, not a costume
   one; a second stream's `--enroll` adds another sample. `clipper faces --enroll-photos` adds
   the `[thumbnail] photos` portraits under their file names.
4. Or do it in the war room ([War room](War-room#faces)): name and enroll groups from their
   sheets, or draw a box round a face on a paused video ("Who's this?", B) to keep one that
   never made a group. `clipper faces <stream> --at 1:23:45 --box x,y,w,h --person Name` is the
   same from the command line; `clipper faces --forget Name` drops someone.
5. `clipper doctor --role gpu` lists the known faces.
6. For covers, keep a few photos of each regular in the cast library (`clipper cast grab
   <stream>`, or the war room's Cast window; see [Cast library](Cast-library)).
