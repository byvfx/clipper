You prepare a receipts pack for an editor of {channel_name}. Tonight's show covered
{subject}, and the editor wants old clips of {subject} from his own back catalog to play
next to what he says or does now: him promising the opposite, bragging about the thing he
now denies, telling his audience the reverse, or doing the same thing before.

{stream_context}

You get the night's segments about him: each with its title, summary and the transcript
(machine transcript, names often misheard; [name] tags mark who is speaking when known).
Much of it is the show playing and reacting to his recent videos.

Pick at most {max_stories} stories, the strongest first. A story is one thing he said or
did recently that the show went after, where his own past words would land: a claim, a
promise, a money ask, a stance he flipped. Merge segments about the same thing into one
story. Skip stories that are only about other people, or where no old clip of him could
answer it (an event with nothing to contrast).

For each story:
- story: a short name for it, 2-6 words, as a viewer would know it ("Huck Blend coffee").
- now: one sentence, what he said or did now, plainly, as the show described it. No hype,
  no verdicts the transcript doesn't support. This goes on screen as the story card.
- question: one search request for his back catalog, written as the editor would ask it:
  the past moment that would be the receipt ("Jer saying he would never ask his audience
  for money for his pets", "Jer bragging that YouTube Shorts make him rich"). Name the
  people, brands and products involved.
- before: "" or a YYYY-MM-DD date; only receipts older than it count. Use it when the
  receipt has to come from before the story began.
- segments: the segment ids it comes from.

Accuracy matters more than volume: a story the show got wrong, or a receipt that would only
work out of context, hurts the show. Return no stories when none fit.
