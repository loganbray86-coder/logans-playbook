Playbook Connect (Chrome add-on)
================================
Connects your private ESPN fantasy football league to Playbook in one click, so you don't have to copy espn_s2 / SWID by hand.

Install (free, about 1 minute):
 1. Unzip playbook-connect.zip.
 2. In Chrome open chrome://extensions and turn on "Developer mode" (top right).
 3. Click "Load unpacked" and choose this playbook-connect folder.
 4. Sign in at fantasy.espn.com, click the PB icon (pin it from the puzzle-piece menu), tap "Connect to Playbook".

What it does with your ESPN sign-in:
 - Reads only the espn_s2 and SWID cookies, only when you tap Connect.
 - Sends them once over HTTPS to the Playbook server you picked (default playbook-friends.onrender.com), nowhere else.
 - Never saves them. The only thing it stores is which Playbook server you picked. No analytics.
 - Playbook only reads ESPN; it can't make moves.

Permissions: "cookies" (to read those two ESPN cookies), "storage" (to remember the server choice),
host access to *.espn.com and the two Playbook servers only.
