The browser bridge lets several AI agents drive one signed-in Chrome at the same time, safely, so that they can use the genealogy sites the owner already pays for or is signed in to (FamilySearch, Ancestry, Archion, Geneanet, Matricula, ScotlandsPeople and others) without each agent opening its own debugging session. It is a small HTTP daemon in front of one long-running [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) process, and it enforces one rule that the agents cannot break by mistake: one agent per site at a time, each in its own tab.

## Setting up

The bridge needs Python 3.10 or later, Node.js (for `npx`), and a recent Chrome. It starts chrome-devtools-mcp with `--auto-connect`, which attaches to the Chrome you already have open rather than launching a new one; this needs a Chrome version with remote debugging that can be switched on from `chrome://inspect/#remote-debugging`. The requirements change between Chrome and chrome-devtools-mcp releases, so check the chrome-devtools-mcp README for the version pinned in `daemon.py` (`MCP_VERSION`, 1.13.0 as of November 2026) if the first connection fails.

1. Make a separate Chrome profile for the agents (for example one called "Research"), and sign in there to the research sites. Keeping the agents out of your everyday profile means they never see your email, bank or work tabs, and it makes their tabs easy to tell apart.
2. Name that profile in `project.json`, as `"bridge": {"profile": "Research"}`. The daemon then refuses any tab that opens in another profile.
3. Mark the profile once, so the daemon can recognize it. In a window of that profile, open `https://example.com/`, open the DevTools console, and run `localStorage.setItem('bridge-profile', 'Research')` with the same name as in `project.json`. Check which profile the window belongs to before you do this, because a marker set in the wrong profile defeats the check. If the profile's site data is ever cleared, set the marker again.
4. Start the daemon from the project root: `python3 tools/browser_bridge/daemon.py &`. It listens on `127.0.0.1:9333` (`bridge.port` in `project.json`, or `--port`, or `BRIDGE_PORT`).
5. Check it with `curl -s http://127.0.0.1:9333/ping`. A reply with `"version": 2` means the daemon enforces site claims. The daemon connects to Chrome on the first browser request, not at start-up, and Chrome then asks "Allow remote debugging?"; click Allow.

With no profile set in `project.json`, the profile check is off and agents may open tabs in any profile; this is acceptable for a test, but not for real work in a browser you also use yourself.

### Keeping the approval prompts rare

Chrome asks for approval once per connection, not once per daemon. A new connection, and so a new prompt, follows whenever the old one is lost: a daemon restart, a second daemon on another port, the computer sleeping or locking, or Chrome restarting. A prompt left unanswered for 60 seconds is abandoned, and the next request asks again. So:

- Agents must never restart the running daemon, or start a test daemon against the owner's Chrome, without asking the owner first.
- On macOS the daemon runs `caffeinate -dims` while any site is claimed, and for an hour after the last release (`BRIDGE_AWAKE_GRACE`, in seconds), so the computer does not sleep in the middle of a run. On other systems, keep the computer awake by other means.
- After one failed connection the daemon refuses browser requests for two minutes (`BRIDGE_CONNECT_BACKOFF`) with the message "Chrome is not connected", so agents retrying in a loop do not pile up prompts. An agent that sees this message stops, records a failed retrieval, and asks the owner to click Allow or unlock the computer. `/ping` reports `chrome` (the connection state), `retry_in_s` and `keeping_mac_awake`.

## How it works

```
agents and scripts  --HTTP, milliseconds-->  daemon.py (127.0.0.1:9333)
daemon.py           --one stdio pipe------>  chrome-devtools-mcp (one process)
chrome-devtools-mcp --DevTools protocol--->  Chrome, signed-in tabs
```

Calling chrome-devtools-mcp afresh for each action reconnects every time, which brings a new approval prompt and often times out on the large single-page viewers genealogy sites use. The daemon keeps one connection open and serves many agents through it.

## Several agents at once

- **Name yourself.** Set `BRIDGE_AGENT=gemini` (or `claude-sub-1`, or any other name) in the environment, or call `client.set_agent('gemini')`. Every request then carries the name. Give each subagent its own name and its own scratch folder, so one agent's script cannot run under another's name.
- **Claim a site, then use the tab you get.** `page = client.claim('familysearch')` opens a new tab that only you can drive and returns its `pageId`; pass that number to `evaluate`, `navigate`, `screenshot` and the download calls. `client.claim('archion', url='https://www.archion.de/...')` opens it at a given page, and `client.open_tab(url)` opens a further tab.
- **Release at the end.** `client.release()` frees all your sites and gives up your tabs; `client.release('archion')` frees one site and its tabs, so stop any script still using them first.
- **A refusal is not a search result.** If another agent holds the site, the call raises `client.ClaimRefused` (HTTP 409) with a message such as `familysearch is held by gemini since 14:02`. Do another task, or wait; never record the refusal as a negative result. `client.claims()` or `curl -s localhost:9333/claims` shows who holds what.
- **What counts as a site.** A registrable domain and its subdomains, so `familysearch.org` covers search, sign-in and the image viewer. Some sites are grouped in `claims.py` because they share one account: Ancestry, Fold3 and Newspapers.com are one site, and Matricula and its image host `icar-us.eu` are another. It is not known whether the Ancestry group also shares one rate limit (TODO: check, and split the group if not).
- **The first use claims.** An agent that navigates or evaluates on a free site claims it without calling `claim`. An agent that acts on a tab nobody owns (one the owner opened) takes ownership of it. A tab that follows a link onto another agent's site is refused on its next request.
- **Spare tabs.** Opening a tab brings Chrome to the front, which interrupts the owner if they are using the computer, so `release()` keeps up to three of the agent's tabs (`BRIDGE_SPARE_TABS`) instead of closing them. Each is sent to `https://example.com/#bridge-spare-...`, checked, and held by the bridge, and the next `claim()` or `open_tab()` reuses the oldest one. No agent may act on a spare tab, and nobody should close them by hand.
- **The anchor tab.** `new_page` cannot name a Chrome profile, so a new tab would open in the profile of whichever window is in front. The daemon therefore keeps one tab on `https://example.com/#bridge-anchor-...`, owned by no agent, and opens later tabs from it with `window.open`, which keeps them in the agents' profile. Leave the "Example Domain" anchor tab open. If it is closed, the next tab is opened the ordinary way and a new anchor is planted once a tab passes the profile check.
- **Claims lapse** after 30 minutes without a request (`BRIDGE_CLAIM_TTL`, in seconds), so a crashed session does not block a site for long. Every request renews the claim. Site claims are saved in `claims.json` (not committed) and survive a daemon restart; tab ownership does not, because tab IDs change, so claim again after a restart.
- **Old scripts still work.** A request with no agent name may use only tabs nobody owns, on sites nobody holds, and claims nothing.
- **No browser extensions.** An extension such as Claude in Chrome does not go through the bridge, so the daemon could not refuse it. Every agent uses the bridge; an agent without Python can use `curl`, for example `curl -s -X POST localhost:9333/claim -H 'Content-Type: application/json' -d '{"agent":"claude-sub-1","site":"familysearch"}'`.

### Limits found in testing

- Scripts run one at a time inside chrome-devtools-mcp, whose `evaluate_script` tool is exclusive: two 4-second scripts in two tabs took 8.3 seconds. Ordinary research is still faster in parallel, because most scripts take milliseconds and the waiting is in page loads and the agents' own thinking, but a long-running script blocks every agent. Write long jobs as a background job in the page that you poll, as `archion_export.py` does.
- Downloads through the shared downloads folder are serialized: `/download_fs` holds a lock while it waits for the new file, and refuses if two new files appear or the destination exists. `/download_file` writes straight to its path and does not need the lock.
- The chrome-devtools-mcp version is pinned, because an `@latest` start once updated the shared npx cache underneath a running daemon. Raise `MCP_VERSION` deliberately, then run the live test below.

## Testing

The unit tests need no browser:

```bash
python3 -m unittest discover -s tools/browser_bridge -t tools/browser_bridge -v
```

For a live test, start a second daemon so the working one is not disturbed (this brings one approval prompt), then run the two-agent test, which uses only example.com, example.org, wikipedia.org and loc.gov and closes the tabs it opens:

```bash
python3 tools/browser_bridge/daemon.py --port 9334 &
python3 tools/browser_bridge/live_two_agents.py --port 9334
kill %1
```

The daemon logs every refusal, error and tab it closes to stderr, with the time.

## Working with genealogy sites

- **Do not wait for a stable page.** FamilySearch, Archion and ScotlandsPeople change the page continuously, and a tool that waits for the page to settle times out after 60 seconds. Navigate with `client.navigate(page, url, wait_sec=3)` and read what is there.
- **Wrap JavaScript as a function**: `() => document.title`, or `() => { return document.querySelector('h1')?.innerText; }`, not a bare expression. When JavaScript sits inside a Python string, avoid a literal `\n` inside JavaScript quotes; use `split(/\r?\n/)` or `String.fromCharCode(10)`.
- **Prefer text to screenshots.** `() => document.body.innerText` or a CSS selector returns in milliseconds and costs far fewer tokens than an image. Keep screenshots for handwriting, headstones and images you will save.
- **Search by URL** where a site allows it, rather than filling in forms, for example `https://www.familysearch.org/search/full-text/results?q.fullName=%2B"John Smith"&q.text=%2BBoston`. FamilySearch's machine reading of handwriting is rough, so a miss in full-text search is not proof of absence.
- **Read `site-notes.md`** before working on a site, and add what you learn there, dated.

## Python client

```python
import sys
sys.path.append('tools/browser_bridge')
import client

if not client.is_running():
    raise RuntimeError('Start daemon.py first')
client.set_agent('claude-sub-1')        # or BRIDGE_AGENT in the environment
page = client.claim('familysearch')     # ClaimRefused if another agent holds the site

client.navigate(page, 'https://www.familysearch.org/search/...', wait_sec=3)
rows = client.evaluate(page, '''() => Array.from(document.querySelectorAll('table tr'))
    .map(r => r.innerText.trim()).filter(Boolean)''')
client.screenshot(page, 'sources/1880-census-page.png')
client.download_fs(img_num=305, dest_filename='1865-ship-manifest.jpg', page=page)
client.download_file(page, 'https://example.org/image.jpg', 'sources/image.jpg')

client.get_pages(mine=True)             # your tabs
client.release()                        # at the end of the session
```

`archion_export.py` saves a full-resolution Archion register image from a tab you own, by loading every tile at native resolution and drawing them into one image: `python3 tools/browser_bridge/archion_export.py --page <pageId> --bild 43 --output sources/<name>.jpg`. It refuses to overwrite a file, writes a JSON sidecar with the dimensions and source URL, and treats zero tiles as a failed retrieval rather than saving a blank image. Viewer zoom above the native level enlarges pixels without adding detail. `ancestry_fetch.py` fetches Ancestry images into `sources/` in the same way; see its docstring.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `Connection refused` on port 9333 | The daemon is not running | Start `daemon.py`, or ask the owner to. |
| "Chrome is not connected" | The approval prompt was not answered, or the computer locked | Stop, record a failed retrieval, and ask the owner to click Allow or unlock the computer. Retrying in a loop brings more prompts. |
| `list_pages timed out after 60000ms` | chrome-devtools-mcp could not reach Chrome | Check that Chrome is open, remote debugging is allowed, and the computer is unlocked. |
| "did not open in Chrome's ... profile" | Another profile's window was in front and there was no anchor tab | Bring a window of the agents' profile to the front and try once more. |
| HTTP 500 on evaluate | A JavaScript error or a timeout; the message gives the cause | For syntax errors, wrap the code as `() => { ... }`. |
| The approval prompt keeps coming back | The daemon was restarted, or something called chrome-devtools-mcp directly | Approve it once and keep a single daemon running. |
| A search returns nothing while the site is slow or signed out | Rate limiting or a lost sign-in | Record a failed retrieval, never a negative result. |
