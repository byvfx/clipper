# TikTok developer app: privacy and terms pages

TikTok's developer app form (issue #28) needs a public website, a privacy policy URL and a terms URL.
These are on byvfx.com (Squarespace), not linked from the menu:

- Website: `https://byvfx.com`
- Privacy: `https://byvfx.com/lolcow-privacy`
- Terms: `https://byvfx.com/lolcow-terms`

## Adding them in Squarespace

1. Pages, then the **+** next to **Not linked**, then **Blank page**. "Not linked" keeps it out of the
   site menu but public.
2. Page settings (gear icon): title "Lolcow Cinema Clipper Privacy Policy", URL slug `lolcow-privacy`.
3. Edit the page, add a **Text** block, paste the privacy text below. Use the text block's heading
   styles for the headings if you like; plain text is fine.
4. Repeat with slug `lolcow-terms` for the terms.
5. Open both URLs in a private browser window to check they load without a login.

Replace `[contact email]` in both before publishing.

If TikTok asks to verify the URLs, use **Domain** verification (a DNS TXT record). Squarespace can't
serve TikTok's signature file at a chosen path, so URL-prefix verification won't work there. If the
domain is on Squarespace Domains: Settings, Domains, byvfx.com, DNS, Custom records, add the TXT
record TikTok shows (host `@`). It can take up to an hour to be seen.

---

## Privacy text

**Lolcow Cinema Clipper Privacy Policy**

Effective October 21, 2026

Lolcow Cinema Clipper ("the app") is a private tool used by the Lolcow Cinema team to send short
video clips of our own show to our own TikTok account as drafts. It is not offered to the public.

**What the app accesses.** When we sign in to TikTok through the app, TikTok gives the app our
account's basic profile information (account ID, display name and profile picture) and permission to
upload videos to our account's drafts. The app does not access anyone else's account, messages,
followers or viewing data.

**What the app stores.** The app stores the sign-in tokens TikTok issues, on our own computer, so it
can upload drafts without signing in each time. It also keeps a record of which clips it has sent so
it does not send the same clip twice. Nothing is sold, shared with third parties or used for
advertising.

**Removing access.** Access can be removed at any time in the TikTok app under Settings and privacy,
Security and permissions, Apps and services (or Manage app permissions). When access is removed, the
stored tokens stop working and we delete them.

**Contact.** Questions about this policy: [contact email]

**Changes.** If this policy changes, the new version will be posted on this page with a new
effective date.

---

## Terms text

**Lolcow Cinema Clipper Terms of Service**

Effective October 21, 2026

Lolcow Cinema Clipper ("the app") is a private tool used by the Lolcow Cinema team to upload clips
of our own show to our own TikTok account as drafts. There is no public sign-up.

**Use.** The app may only be used with TikTok accounts operated by the Lolcow Cinema team. Anyone
using it must follow TikTok's Terms of Service and Community Guidelines. Videos sent by the app are
drafts; a person reviews each one in the TikTok app before it is posted.

**Content.** The app only uploads clips from shows we have permission to publish. We are responsible
for what we post.

**No warranty.** The app is provided as is, without any warranty. We are not liable for any loss
from its use, to the extent the law allows.

**Changes.** We may change or stop the app at any time. Updated terms will be posted on this page
with a new effective date.

**Contact.** [contact email]
