These are working notes on particular genealogy sites, viewers and bridge behaviours, for any agent that drives a browser through the bridge. Add to them whenever you learn something, and date what you add, because sites change their pages without notice and a lesson from months ago may no longer hold. They complement the general rules in `AGENTS.md` ("Browser automation") and `README.md` in this folder.

## Before every run

- Stop at once, and record a failed retrieval rather than a result, on any of these: the site shows you signed out (FamilySearch redirects to `ident.familysearch.org`); a tool returns "429" or "out_of_credits"; the same step fails two or three times.
- Never reuse an existing filename in `sources/`: check with `ls` first, because an overwrite of a tracked image has happened and had to be restored with `git checkout`.
- Never click a purchase, upgrade or paid OCR prompt.
- Before sending a worker to a site, check that it answers at all, for example with `curl -s -o /dev/null -m 20 -w '%{http_code}' <site URL>`, and send the worker only if a status code comes back. A bridge tab on an unresponsive site never finishes loading, it can stall every agent's calls, and it may make Chrome ask for remote-debugging approval again (probable cause as of November 2026, not proved).
- If an archive's server closes connections without an HTTP response while DNS, TCP and TLS all work, stop repeated navigation and retry only when availability changes. Do not restart Chrome or disable TLS verification to work around it, and do not treat the failure as a negative result.
- Closing the laptop lid sleeps the Mac despite the daemon's `caffeinate`, which drops Chrome's connection until the owner approves it again on an unlocked screen.
- Do not post page data (an image blob, say) to a local receiver on `127.0.0.1`. In one case the evaluate call hung and the bridge then lost Chrome, which needed the owner to click Allow; the two may be connected, but that is not known. Use the download methods below, one image per navigation, or `client.screenshot` of the viewer.
- Never import or run another worker's original scripts: they set `BRIDGE_AGENT` to that worker's name, so a claim made through them is stamped with the other name and blocks the site until the 30-minute claim expires. Copy a script and change `BRIDGE_AGENT` first, and check after any `sed` edit that your copy carries your own name (macOS `sed -i` needs `''` as its argument). A finished worker's claim expires on its TTL; wait and retry rather than act under its name.

## Bridge behaviour

- The daemon's `/call_tool` HTTP wrapper takes `{"name":"new_page","args":{"url":"https://..."}}`. The key is `args`; `arguments` is the nested MCP transport key used inside the daemon, and sending it produces a 500 validation error saying `url` is undefined. Never restart Chrome to fix this error.
- If a worker is refused with "did not open in Chrome's Claude profile (marker missing)" and the page list shows no `example.com/#bridge-anchor-...` tab, the anchor tab has been closed and new tabs fall back to whichever profile is in front. Without restarting the daemon or turning off the check, any tab the bridge has already accepted in the right profile can replant the anchor: `client.evaluate(<tab>, "() => { window.open('https://example.com/#bridge-anchor-<tag>', '_blank', 'noopener'); return true; }")`. The new tab belongs to the opener's profile, and the daemon adopts an unowned tab on the marker page that passes the marker check. When no accepted tab is open, the owner has to bring a window of the right profile to the front once. Do not close the "Example Domain" tab.
- Opening a tab in the background of a given Chrome profile through DevTools `Target.createTarget` with that profile's `browserContextId` does not work: Chrome refuses a context that DevTools did not create ("Failed to find browser context"). New tabs come from the anchor tab with `window.open`, which also brings Chrome to the front, once per claim, which is why spare tabs are kept (README section 3).
- A real Chrome download (a clicked Download button or an `a.click()` on a blob) shows the download bubble and brings the browser window to the front, which can interrupt the owner's other work. Prefer an in-page fetch that saves through the bridge where the site allows it (see the FamilySearch and Ancestry sections).
- Older MCP clients and newer servers can disagree on workspace-root negotiation for screenshot paths, and the initial MCP connection can take up to 60 seconds, so a bridge deadline shorter than that fails spuriously. As of November 2026 the daemon answers the roots request for the repository and `list_pages` waits 65 seconds.

## FamilySearch film viewer

- Typing the number into the viewer's Image box is reliable; the `?i=` parameter in the URL is not (it has opened the wrong image).
- The "Image Index" panel lists the indexed entries on each image and is the cheapest way to screen a film, but it is incomplete: images with "No indexes" must be read by eye, and some entries on indexed images are missing. An index screening is never a negative result.
- The index can attribute a parent's name to the child: a baptism listed under the father's name may be a sibling's or a stillborn child's record. Read the image.
- Fast navigation sometimes leaves the index panel empty; wait a few seconds.
- Image numbers in a record's citation can be the item's own ("image 251 of 352") while the viewer counts the whole film ("593 of 694"). Say which you mean.
- Do not assume each viewer image advances a numbered index page: consecutive images can be different exposures of the same leaf.
- The catalog place search works by URL as `https://www.familysearch.org/en/search/catalog/results?count=20&offset=0&q.place=<Place>`; a plain `?query=` URL drops the query (as of November 2026). Restricted films show a greyed camera icon and the text "not currently digitally accessible due to access restrictions". Some films are restricted to a family history center or affiliate library, and the home viewer refuses them explicitly.
- Catalog film links now open `view=explore&grid=on` (as of November 2026). The same ARK with those parameters removed opens the standard viewer with its Download button. This changes presentation only; restrictions still apply. Wait until the image-number control exists before running a download helper, since a newly opened tab sometimes takes more than three seconds to render it.
- Download by button: open a new tab per image, `find` "image viewer download button", click it by element. The current Download opens a format dialog with PDF as the default, so select "JPG Only" and confirm. The file lands in `~/Downloads` as `<id>.jpg` at full resolution, which Archion does not give.
- Download without a Chrome download: after moving the viewer to the image number, read the image's ark from the page URL (check that `i` equals the image number minus one) and fetch `https://<deepzoom host>/service/records/storage/deepzoomcloud/dz/v1/<ark>/dist.jpg` inside the page with the page's own `fssessionid` as a Bearer header. With cookies alone the service answers 401 or fails CORS. The file matched the button's file in size. The token never leaves the page. If the fetch fails, fall back to the button, which may bring Chrome to the front.
- Sign-out: FamilySearch signs the automation profile out about once a day (as of November 2026), so expect it on a morning run. If the sign-in page still shows after the check below, stop, report a failed retrieval and ask the owner to sign in.
- A fresh FamilySearch tab can briefly redirect to `ident.familysearch.org` while the account is signed in, even when another tab is signed in. Wait 10 seconds, read `location.href` again and reload the target once before reporting a sign-out.
- US county probate collections can be one image per page, and a catalog item can be a different volume from the one a similar-looking DGS number suggests; check the item title before citing.

## Archion viewer

- URL: `https://www.archion.de/de/viewer/churchRegister/<viewer id>`.
- Jump by typing the Bild number into the box at the top left and pressing Return. After each jump, wait 3-5 seconds and check the page number in a screenshot: the viewer often keeps showing the previous image. Reload and jump again if it is stale.
- The Bild box is a select element: typing into it can append digits and land on the wrong Bild. What worked (as of November 2026): `find` "Bild number select box", then `form_input` with the number.
- The "+" zoom button moves between loads; take a screenshot before clicking it. The first click after a page loads sometimes misfires (it once opened the Archion search page).
- "Im Vollbild anzeigen" (fullscreen) plus three to seven clicks on the "+" at the bottom makes most hands legible; five or six, panned onto the area, for a struck-through or small header.
- The download button does nothing, and a script lookup of the image URL was blocked, so by hand images are saved as zoomed screenshots. Say so in the sidecar; they are screen resolution (often under 700 px wide), so prefer a FamilySearch copy of the same register where one exists.
- The "txt" panel offers paid OCR. Do not use it.
- Exporter: reading the viewer's own scripts (the document viewer and a tile-zoom plugin) shows that zoom clicks are ignored while `inAction` is true, and that each zoom clears the tile grid and asynchronously requests authorized tile URLs. Fixed click counts and delays therefore cannot establish that an image is ready. `archion_export.py` checks the selected Bild, animation state, native level, dimensions and complete tile count before loading and drawing the authorized tiles; see the README for usage. Native size is typically a few thousand pixels wide, and digital zoom above the native level adds no source detail.
- Loading every off-screen tile at once can time out. The exporter uses 12 workers and a background job polled by the client, with a 30-second per-tile limit and a 180-second overall limit. Preserve tile DOM order when drawing, whatever the load order.
- A register's printed page number, its Bild number and an index's page number often do not run in step: a Bild can be a spread of two pages, family-book Bilder can hold four columns, and page labels in cursive (0 and 4, for example) are easy to misread. Treat any Bild-to-page pair as approximate and check the page number on screen. Margin cross-references in family books ("see page N") are quicker than guessing Bild numbers.
- A register's front matter sometimes says that baptisms before a given year or some marriage years are missing; read it before searching those years.

## Matricula viewer

- `?pg=N` opens image N directly without sign-in; wait about 4 seconds. A `pg` beyond the book's end silently loads pg 1. Index page numbers can be one page off.
- The sidebar clips the right-hand page unless you click fullscreen first, and fullscreen resets on every navigation. Zoom regions that worked without fullscreen on one run cut pages in half on another, so check that both page edges are visible before reading.
- Image URLs carry tokens, so they cannot be fetched directly at full resolution.
- Full-resolution download (tested as of November 2026): the viewer draws a canvas, but it loads one whole image from `img.data.matricula-online.eu/image/<base64 of the hosted-images URL>?csrf=...&ctrl=...`. Run this in the page with the browser tool's JavaScript, which saves the file to `~/Downloads` without returning the tokenized URL (returning it trips the tool's privacy filter):
  `const e=performance.getEntriesByType('resource').filter(e=>e.name.includes('img.data.matricula-online.eu/image/')).pop(); const b=await (await fetch(e.name)).blob(); const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='NAME.jpg'; document.body.appendChild(a); a.click(); a.remove(); b.size`
  After moving to another pg, wait for the new image to load and take the last matching entry. The served file is the archive's own size (about 1500 px wide for a spread in one test), so it is the best copy available, though below the 700 px-per-page ideal for a spread. Fetching the hosted-images URL directly fails (CORS); loading it as an image works but gives the same size.
- This download is a real Chrome download, so it shows the download bubble and brings the browser window to the front. It saved the first image but not the next two in the same tab. Until there is an in-page fetch that saves through the bridge, capture pages with `client.screenshot` and reuse one tab.

## Ancestry

- Search: one request per call, 5 to 10 seconds apart.
- Census search URL form: `https://www.ancestry.com/search/collections/<collection id>?name=John&birth=1843_Bavaria-Germany&birth_x=2-0-0&residence=_Cook-Illinois&spouse=Mary&count=50`. Places follow an underscore. `father=` and `mother=` are ignored; `residence` ranks results but does not filter them. The page text lags one load behind the URL, so wait about 8 seconds. The 1860 census index shows no spouse or parents in its result list (as of November 2026).
- Do not use guessed numerical place IDs in search URLs, since they can redirect to unrelated places.
- Image download by menu: open the record's own viewer tab, then Tool menu, then Download. A full-size JPEG can appear in Downloads after an initial directory check, so allow the download to finish before declaring failure. Image numbers in a combined film are the viewer's, not a crosswalk to a FamilySearch estimate for the same book.
- Image download fallback: when the Download button stops producing files (one file, then nothing, with no prompt visible; the same symptom has appeared on FamilySearch), fetch the image instead. In the signed-in viewer tab, `fetch('/imageviewer/api/media/token?dbId=<collection>&imageId=<image id>')` returns JSON with `imageDownloadUrl` (a relative URL to `/api/media/retrieval/v2/image/...jpg?securitytoken=...&download=True`); prefix `https://www.ancestry.com` and save it with `client.download_file(page, url, dest_path)`. This gives full resolution with no Download click and no Chrome download prompt. The token is short-lived and per image: request a new one for each image and never write it into a file. Helper: `tools/browser_bridge/ancestry_fetch.py --page <tab> --db <collection> --out <dir> <image ids>`. The same click in a fresh tab sometimes works again, so the button failure seems tied to the tab or to rapid repeated clicks; it is unclear which.
- Original probate and will books on Ancestry usually run in the order outside fold, then the pages, then the back with the probate certificate.
- The Newspapers.com Obituary Index on Ancestry reaches clippings, but its machine summaries can be wrong (an address read as a burial place, for example).

## Newspapers.com

- The page viewer has no OCR text in its HTML and screenshots are too small to read. Deep-zoom tiles could be fetched and stitched into a canvas inside the page (a script run with `client.evaluate` after `client.navigate` to `https://www.newspapers.com/image/<id>/`, then read back as a data URL in slices and written as a full-resolution JPEG). As of November 2026 that method failed because the viewer loads cropped tiles rather than `/img/dz/` tiles, so save crops until a script is rewritten.
- Search URLs: the filtering parameter is `keyword=`; `keywords=` is ignored silently and returns every page in the date range. `&county=<name>` with `region=us-<state>` narrows to a county.

## Geneanet

- Register photographs can be public in the normal viewer while the index asks for sign-in. Read the viewer's observed `/registres/api/images/<register>?min_page=...&max_page=...` response: each page gives `image_base_url` and `image_route`. Fetch its observed `ImageProperties.xml` for WIDTH, HEIGHT and TILESIZE.
- The standard Zoomify levels halve the dimensions down to 256 pixels or less; the native tile index includes all preceding levels, with TileGroup = floor(globalTileIndex / 256). Use six workers, check every response, and draw at exact tile coordinates into a native-size PNG; never fill missing tiles. Open each finished assembly to check it.
- The public metadata fetch worked inside the owned browser tab, while a direct HTTP request earlier returned 403; that 403 did not show that the source images were unavailable. Use no restriction bypass and no paid feature.

## Poznań Project and Geneteka

- Poznań Project searches are POSTs to `https://poznan-project.psnc.pl/getresults.php` with `action=extended`; `extended_window_lastname_mode` must be `both` (with groom and bride surnames) or `one` (with `lastname` and `parish_id`). Mode `1` returns `NG_ERROR_SURNAME1`.
- Geneteka has a JSON interface (`https://geneteka.genealodzy.pl/api/getAct.php`, with parameters such as `bdm`, `w`, `rid` and `search_lastname`). Check which years it indexes for a parish before treating an empty result as informative.
- Before about 1780, Polish parish baptism entries in some places give no surnames, only the parents' forenames and the village; plan the identity check for that.

## Landesarchiv Baden-Württemberg direct scans

- The public web-fetch tool rejected the thumbnail URLs, but Python HTTPS fetched the metadata and the full image endpoint, so no browser is needed. Read the zoom page's `imageSource` to get `bild.php`'s `originalBilddatei`; request `zoom=100` and JPEG `quality=100`, preserving the `scopeid_besta` from that page.
- Do not infer a `titlaufn` (the item number in the image path) by adding one to the previous item's: consecutive items can have unrelated numbers. Image paths take the form `/data/bildcms/internetbilder/archiv_4/olf/bestand_<digits>/titlaufn_<ten digits>/04_<ten digits>_<four-digit Bild>_4-<integer titlaufn>-<Bild>.jpg`. Returned spreads were about 2600 pixels wide.

## National Archives of Ireland, Tithe Applotment Books and census (nationalarchives.ie)

- The tithe and census sites share a server and have been unreachable for a whole day while the main `nationalarchives.ie` site answered (as of November 2026). Run the `curl` status check from "Before every run" first.
- The FamilySearch microfilm of the Applotment Books is restricted, both image and index, for the reels checked; the RootsIreland tithe index needs a subscription, so it belongs on the paid-month list. The free route is the National Archives site, once it answers.

## NLI Catholic Parish Registers (registers.nli.ie)

- The site answers `curl` with 403 (probably bot protection); in the browser the home page is a map search with no parish list in its anchors. The parish list is JSON at `https://registers.nli.ie/parishes?page=1` and following pages (about 23 as of November 2026).
- Full-resolution images: an in-page fetch from `registers.nli.ie` fails, and `curl` gets a Cloudflare 403. Open the image-server URL (`https://iserver.nli.ie/fcgi-bin/iipsrv.fcgi?FIF=<path>/<record>_NNN.jp2`) in its own tab with `client.open_tab`, then fetch from that tab with `client.download_file`. `&CVT=jpeg` alone caps at 1500 px; the true size (`&obj=Max-size`) is about 4300 x 3700, so fetch tiles with `RGN=x,y,w,h&WID=<w>&CVT=jpeg` and stitch them.

## Google News Archive

- Claim key `google.com`. Issue URL form: `https://news.google.com/newspapers?nid=<paper id>&dat=YYYYMMDD&printsec=frontpage`; a 404 means the issue is not scanned, and coverage can be sparse or missing for whole years or decades, so sample before planning a search.
- `&h=3000` on the page image URL gives only about 2500 px per two-page spread, too blurred for small print. Full-resolution tiles come from `&zoom=6&tid=0..383` (about 70 seconds per spread), stitched in 3x3 blocks, 8 blocks across (tid = block * 9 + row * 3 + col), into about 6144 x 4608 pixels. In one test the bottom third was empty, so check the tile range.

## DigitalNC

- The search date filters (`date1`, `date2`) are ignored; filter with `lccn=` and `sort=date`. Title-browse and issue-calendar pages close the bridge tab ("No open tab with pageId"); issue URLs of the form `/lccn/<lccn>/<YYYY-MM-DD>/ed-1/seq-N/` and their `/ocr/` pages work, and a 404 there means the issue is not online.

## Films and registers mapped in this project

Record per-project film and register mappings here (image-to-year, Bild-to-page pairs), each with a date and your agent name, so the next agent does not repeat the paging.

TODO: add project-specific mappings as they are found.
