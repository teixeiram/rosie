# Sending recipes to Rosie from other apps

## iPhone: a "Send to Rosie" Shortcut

iPhone home-screen web apps can't appear in the Share sheet, so a small Shortcut
does it: it takes the page you're looking at and opens Rosie with the link
filled in. You check it and tap **Import with AI** (nothing is spent until you do).

Build it once (Shortcuts app):

1. Tap **+** to make a new shortcut. Name it **Send to Rosie** (tap the name at the top).
2. Tap the **ⓘ** (Details) button and turn on **Show in Share Sheet**. Back in the
   editor, a "Receive **Any** input from **Share Sheet**" line appears at the top: tap
   **Any** and leave only **URLs** and **Safari web pages** ticked.
3. Add the action **Get URLs from Input**. It should say "Get URLs from *Shortcut Input*".
4. Add **URL Encode**. It should say "URL Encode *URLs*".
5. Add **Open URLs**, and type this as its URL, ending with the blue *URL Encoded Text*
   variable (tap the variable bar above the keyboard):

   `https://rosie.example.com/?url=` *URL Encoded Text*

6. Tap **Done**.

Use it: on a recipe page in Safari (or any app that shares a link), tap **Share**, then
**Send to Rosie**. It opens Rosie in Safari on the Import tab with the link filled in.
Tap **Import with AI**.

Notes:
- It opens in **Safari**, not the home-screen app (iOS keeps their sign-ins apart), so
  the first time you'll sign in to Rosie in Safari. That sign-in lasts 180 days.
- Only works where Rosie is reachable (at home or on your VPN), like the rest of the app.
- **Instagram:** shared links need an Instagram login to read, so the import will fail.
  Take screenshots of the post instead and use **📷 Choose screenshots or PDF** on the
  Import tab (several screenshots of one recipe are fine).
- If a site refuses the link ("The site refused the request"), screenshots work there too.

## Android

Install Rosie to the home screen (Chrome menu, **Add to Home screen** / **Install app**).
Rosie then appears in Android's Share menu: share a page to it and the link is filled in.

## YouTube

Not supported yet.
