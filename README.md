# Aqar watcher

Sends you a Telegram message when a villa or apartment in Jubail is added to or removed from Aqar.
It runs free on GitHub, checking every ~10 minutes.

## 1. Create the Telegram bot (2 min)
1. In Telegram, open **@BotFather** and send `/newbot`. Pick any name. It replies with a **token** like `123456:ABC-xyz…`. Copy it.
2. Open your new bot (BotFather links it) and press **Start**. This step is required, because a bot can't message you until you've messaged it.
3. Open **@userinfobot** and press Start. It replies with your **Id** (a number). Copy it.

## 2. Put the code on GitHub (5 min)
1. Sign in at github.com and create a **new repository** named `aqar-watch`. Set it to **Public**, because public repos get unlimited free Actions minutes and a private one would run out. The repo only stores public Aqar listing IDs. Your token stays secret either way.
2. Add the 3 files with **Add file → Create new file**, pasting each file's contents:
   - `watch.py`
   - `requirements.txt`
   - `.github/workflows/watch.yml` (type the slashes in the name box and GitHub creates the folders)

   On a computer you can instead use **Add file → Upload files** and drag in everything from the zip, including the `.github` folder.

## 3. Add your secrets
In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
- `TELEGRAM_BOT_TOKEN`: the token from BotFather
- `TELEGRAM_CHAT_ID`: your Id from @userinfobot

## 4. Turn it on
1. Open the **Actions** tab. If GitHub asks, click "I understand… enable".
2. Click **Aqar watch → Run workflow**, tick **"Only send a Telegram test message"** and run it. You should get "✅ Test from your Aqar watcher".
3. Click **Run workflow** again with the box unticked. You'll get "👀 Aqar watcher is live" along with a count of what it's tracking. From then on it runs by itself.

## Changing what it watches
Edit the `SEARCHES` list at the top of `watch.py`. It watches apartments and villas, both rent and sale. Delete a line to stop watching that search. Other Aqar categories use the same slug format as Aqar's site, e.g. `دور-للإيجار` (floor for rent).

## How "removed" works
A listing must be missing from the search 2 checks in a row. The watcher then opens the ad page itself to confirm it's closed or deleted, so a glitchy page load won't send you a false alarm. Expect a "removed" alert about 20–30 min after an ad comes down.

## If something's off
- **No messages at all:** check the run log in the Actions tab. A red run usually means a secret is wrong, or you skipped pressing Start on your bot.
- **"couldn't read Aqar for the last 6 checks":** Aqar is blocking GitHub's servers. The watcher keeps retrying, and you'll get "working again" once it recovers.
