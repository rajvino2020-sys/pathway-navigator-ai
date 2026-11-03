# Pathway Navigator

"You don't need to know the way. We'll help you discover the doors."

A person who wants to start something of their own, but does not know how the
entrepreneurial world works, speaks in their own words. The app works out where they are
and shows the few support "doors" (training, community support, subsidy, credit) that make
sense for them now, in order, with a next step, a practice script and the official source.
If a door closes, they say why, and the app finds another.

## How it works

| Step | Who does it | What |
|---|---|---|
| 1. Understand | Muse Spark | Turns free speech or text (any language) into a structured profile. Never guesses money or location. |
| 2. Tags and rules | Code | Derives tags (idea_only, money_low, rural...) and finds which routing rules fire. Exact, testable. |
| 3. Route | Muse Spark | Picks and explains Start here / Next / Later, in the person's language, using only the pathway library. |
| 4. Make safe | Code | Drops unknown pathways, overwrites every link and date from the library, flags "you qualify" wording. |
| 5. Re-route | Code + Muse Spark | A refusal (with the reason) removes that door and brings forward an alternative. |
| Photo | Muse Spark (vision) | Explains a photographed notice or form in simple words. It never says whether you qualify. |

If the model is unreachable, a rules engine produces the same slots so the demo never goes blank
(the screen says so).

## Project status: FROZEN

No new features. From here: test, fix bugs, record the demo, submit.

Languages: the page, the questions and the answers work in English, Tamil, Hindi, Telugu, Bengali and Marathi.
The page follows the browser language, the language box, and the language the person speaks.
All wording is in `static/i18n.js` (labels) and `core.py` `QUESTION_TEXT` (follow-up questions).
The Tamil, Hindi, Telugu, Bengali and Marathi text was machine-written. Please have a native speaker read it.

## Run it (5 minutes)

```bash
cd navigator
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests                   # should print OK
python app.py                                          # demo mode, no key needed
```
Open http://localhost:5000 and press "Use example" then "Show me the doors".

With the model:
```bash
export MODEL_API_KEY=your-key        # Windows: set MODEL_API_KEY=your-key
export MODEL_NAME=muse-spark-1.3     # use muse-spark-1.2 if 1.3 is not enabled for you
python app.py
```
The header badge under the results says "Powered by Muse Spark" when the model was used.

## First live check (do this once, 15 minutes)

These parts could not be tested without a key, so confirm them first:
1. `curl localhost:5000/api/health` shows `model_configured: true`.
2. Speak or type the example. If you get "API error 4xx", read the message: it is usually the
   model name (try muse-spark-1.2) or the key.
3. Look at the warnings line under the results. "dropped unknown pathway id" means the model
   invented something and the code removed it. A few are fine; many means the prompt needs work.
4. Try the photo box with a photo of any notice.
5. Run the 4 checks in "Demo script" below.

## Final checks before you record (about 20 minutes)

1. `python -m unittest discover -s tests` shows OK.
2. Tamil story: speak or type it, answer the two questions, check the doors, press the refusal button on SVEP with
   a reason, check SVEP disappears and a new door is shown, then switch the language box to Hindi and back.
3. English story with a city (for example Chennai): SVEP and PMFME should not appear.
4. No money amounts are calculated anywhere on the screen.
5. Open `http://localhost:5000/?debug=1` if something looks wrong: the notes line shows what the code corrected.
6. Verify, in the official guidelines, whether PMFME covers tea stalls / food retail, and whether PMEGP covers trading shops.
   The library now carries a "confirm this first" caution for both; update it once you know.

## Demo script (about 2 minutes)

1. Say (or "Use example"): the village woman with a food idea and Rs 10,000.
2. Show the snapshot, then Start here (Learning Program and SVEP), Next (PMFME), Later (MUDRA),
   Not yet (Seed Fund). Point at "official source, last checked" on a card.
3. Show "Practise before you go".
4. On SVEP press "I was refused here", type "not available in our block". Show the new route.
5. Optional: photograph a notice, show the plain-language explanation. Press Listen.

## Short submission text (edit freely)

Problem: Many people want to start something of their own but do not know how the
entrepreneurial ecosystem works, so they never find the programmes that exist for them.
Solution: Pathway Navigator listens (voice, any language), understands the person's starting
point, and shows only the few doors that fit now, in the right order, with preparation and
a way to practise. When a door closes, it finds another.
Muse Spark is used for: multilingual understanding of free speech into a structured profile,
reasoning over a verified pathway library to choose and explain the route, and vision for
explaining photographed notices.
Trust: the model never writes links or dates; they come from a library checked against
official sources. Wording never promises eligibility. A rules engine is the fallback.
Limits: India only; five pathways; the library must be re-verified as schemes change.
Next: more states and pathways, Nepal and Bangladesh, full role-play practice, partner NGOs.

## Files

- `app.py` web server. `core.py` tags, rules, prompts, safety. `llm.py` Meta Model API client.
- `static/index.html` the interface (voice, door cards, refusal flow, photo). `static/i18n.js` its six-language wording.
- `data/` pathway library (verified 2026-10-30), routing engine, profile schema.
- `tests/` 49 tests, including full journeys: `python -m unittest discover -s tests`.

## Speed

Muse Spark reasons before it answers, and "reasoning effort" sets how long. The app asks for `minimal` when reading
what she said and `low` when choosing the doors. Each answer prints a line like
`[timing] route: 6.2s (model muse-spark-1.3, output tokens 900)` in the terminal. If answers get worse, raise
`MODEL_ROUTE_EFFORT` to `medium` in `.env`; if they are still slow, lower it. Repeat requests are instant (cached).

## Known limits

- Voice input uses the browser's speech recognition: Chrome or Edge, and quality varies by language.
  (Muse Spark also accepts audio; sending the recording to it is a possible upgrade.)
- The Meta API calls assume the OpenAI-compatible chat completions format at
  https://api.meta.ai/v1 as described in Meta's docs. Check the docs if the key is rejected.
- Scheme facts change. Re-check the library before relying on it, and keep "last verified" honest.
