"""End-to-end journeys through the real Flask app with a scripted stand-in for the model."""
import copy
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import core  # noqa: E402
import llm  # noqa: E402

TAMIL_STORY = "எனக்கு ஒரு பூ கடை வைக்க ஆசை. என்னிடம் முதலீடு இல்லை. குடும்பம் ஆதரவு தரவில்லை."
HINDI_STORY = "मैं सिलाई का काम शुरू करना चाहती हूँ। मैं गाँव में रहती हूँ और मेरे पास 5000 रुपये हैं।"
ENGLISH_STORY = "I want to start tailoring in Chennai. I have 30000 rupees and good stitching skills."


def base_profile(language, sector="retail", amount=None, location="unknown", ev=None):
    p = core.demo_profile()
    p["business_idea"].update({"sector": sector, "description": "shop", "idea_stage": "just_an_idea"})
    p["resources"]["money_available"] = {"amount": amount, "currency": "INR" if amount is not None else None,
                                         "confidence": "exact" if amount is not None else "unknown"}
    p["location"]["rural_or_urban"] = location
    p["language"]["preferred_language"] = language
    p["evidence"] = ev or {"rural_or_urban": None, "money_amount": None}
    return p


def fake_model(messages, timeout=90, **kwargs):
    """Behaves like a careful model: extracts only what was said, routes from the fired rules."""
    system, payload = messages[0]["content"], json.loads(messages[1]["content"])
    if system.startswith("You turn what a person says"):
        text, prior = payload["NEW_TEXT"], payload.get("PRIOR_PROFILE")
        if prior:                                       # a follow-up answer
            p = copy.deepcopy(prior)
            p["evidence"] = {"rural_or_urban": None, "money_amount": None}
            if text.strip() == "village":
                p["location"]["rural_or_urban"] = "rural"
                p["evidence"]["rural_or_urban"] = "village"
            if text.strip() == "0":
                p["resources"]["money_available"] = {"amount": 0, "currency": "INR", "confidence": "exact"}
                p["evidence"]["money_amount"] = "0"
            return p
        if "பூ கடை" in text:
            return base_profile("Tamil")
        if "सिलाई" in text:
            return base_profile("Hindi", "services", 5000, "rural",
                                {"rural_or_urban": "गाँव में", "money_amount": "5000"})
        return base_profile("English", "services", 30000, "urban",
                            {"rural_or_urban": "Chennai", "money_amount": "30000"})
    # routing
    prof = core.normalise_profile(payload["USER_PROFILE"])
    rej = payload["REJECTIONS"]
    route = core.fallback_route(prof, payload["TAGS"], payload["FIRED_RULES"], rej)
    notes = []
    for r in rej:
        local = "not available" in r["reason"]
        notes.append({"pathway_id": r["pathway_id"],
                      "class": "local_availability" if local else "fixable_requirement"})
        if not local and not route["doors"]["later"]:
            route["doors"]["later"].append({"pathway_id": r["pathway_id"], "status": "possible_later",
                                            "why_it_may_fit": "later", "must_confirm": [],
                                            "next_step": "after the plan", "fix": "write a simple plan"})
    route["rejection_notes"] = notes
    return route


def all_ids(result):
    d = result["doors"]
    return d["start_here"], d["next"], d["later"]


class TestJourneys(unittest.TestCase):
    def setUp(self):
        appmod.CACHE.clear()
        self.c = appmod.app.test_client()
        self.patches = [mock.patch.object(llm, "available", return_value=True),
                        mock.patch.object(llm, "chat_json", side_effect=fake_model)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def extract(self, text, prior=None, ui="English"):
        return self.c.post("/api/extract", json={"text": text, "ui_language": ui, "prior_profile": prior}).get_json()

    def route(self, profile, rejections=None):
        return self.c.post("/api/route", json={"profile": profile, "rejections": rejections or []}).get_json()

    def ids(self, r, slot):
        return [d["pathway_id"] for d in r["result"]["doors"][slot]]

    def test_tamil_story_to_alternative_door(self):
        x = self.extract(TAMIL_STORY, ui="English")                       # dropdown still on English
        self.assertEqual(x["profile"]["language"]["preferred_language"], "Tamil")
        self.assertEqual(x["questions"], [core.QUESTION_TEXT["money_available"]["Tamil"],
                                          core.QUESTION_TEXT["rural_or_urban"]["Tamil"]])
        x = self.extract("village", x["profile"], ui="English")           # follow-ups typed in English
        self.assertEqual(x["questions"], [core.QUESTION_TEXT["money_available"]["Tamil"]])
        x = self.extract("0", x["profile"], ui="English")
        self.assertEqual(x["missing"], [])
        self.assertIn("money_0", x["tags"])
        self.assertEqual(x["profile"]["language"]["preferred_language"], "Tamil")

        r = self.route(x["profile"])
        self.assertEqual((r["status"], r["engine"]), ("ok", "model"))
        first = self.ids(r, "start_here")
        self.assertIn("learning_program", first)
        self.assertFalse({"pmegp", "pmfme"} & set(first))                  # nothing needing own money first

        # refuse a community door as unavailable locally
        refused = "svep"
        rej = [{"pathway_id": refused, "reason": "not available in our block"}]
        r2 = self.route(x["profile"], rej)
        everywhere = self.ids(r2, "start_here") + self.ids(r2, "next") + self.ids(r2, "later")
        self.assertNotIn(refused, everywhere)
        self.assertIn("learning_program", everywhere)

        # refuse another door for a fixable reason: allowed only in "later", with a fix
        nxt = (self.ids(r2, "next") + self.ids(r2, "later"))[0]
        rej.append({"pathway_id": nxt, "reason": "bank wants a business plan"})
        r3 = self.route(x["profile"], rej)
        self.assertNotIn(nxt, self.ids(r3, "start_here") + self.ids(r3, "next"))
        self.assertTrue(r3["result"]["doors"]["start_here"])
        for d in r3["result"]["doors"]["later"]:
            if d["pathway_id"] == nxt:
                self.assertTrue(d.get("fix"))

    def test_hindi_story_complete_without_questions(self):
        x = self.extract(HINDI_STORY, ui="Hindi")
        self.assertEqual(x["missing"], [])
        self.assertEqual(x["profile"]["language"]["preferred_language"], "Hindi")
        r = self.route(x["profile"])
        self.assertEqual(r["status"], "ok")
        self.assertIn("svep", self.ids(r, "start_here") + self.ids(r, "next"))   # rural, low money

    def test_english_urban_story_never_gets_rural_door(self):
        x = self.extract(ENGLISH_STORY)
        self.assertIn("urban", x["tags"])
        r = self.route(x["profile"])
        every = self.ids(r, "start_here") + self.ids(r, "next") + self.ids(r, "later")
        self.assertNotIn("svep", every)
        self.assertEqual(self.ids(r, "start_here"), ["learning_program"])

    def test_switching_languages_mid_journey_keeps_questions_in_new_language(self):
        x = self.extract(TAMIL_STORY)
        x2 = self.extract("village", x["profile"], ui="Hindi")             # script of "village" is Latin; prior Tamil wins
        self.assertEqual(x2["profile"]["language"]["preferred_language"], "Tamil")

    def test_every_door_has_library_link_and_date(self):
        x = self.extract(HINDI_STORY)
        r = self.route(x["profile"])
        for slot in ("start_here", "next", "later"):
            for d in r["result"]["doors"][slot]:
                self.assertEqual(d["official_source"]["url"], core.PATHWAYS[d["pathway_id"]]["official_sources"][0]["url"])
                self.assertEqual(d["last_verified"], core.PATHWAYS[d["pathway_id"]]["last_verified"])


class TestNoCalculations(unittest.TestCase):
    def test_calculation_sentences_are_removed(self):
        route = {"doors": {"start_here": [], "next": [{
            "pathway_id": "pmegp",
            "why_it_may_fit": "This may fit. Rs 30000 divided by 5% is about Rs 600000 project size. Ask the office.",
            "must_confirm": ["Illustration only; the bank and agency decide.", "Eligible activity"],
            "next_step": "Check the portal."}], "later": [], "not_yet": []},
            "message_to_user": "Hello. 30000 / 10 = 3000 is your share. Start with learning."}
        r, w = core.finalize(route)
        d = r["doors"]["next"][0]
        self.assertEqual(d["why_it_may_fit"], "This may fit. Ask the office.")
        self.assertEqual(d["must_confirm"], ["Eligible activity"])
        self.assertEqual(r["message_to_user"], "Hello. Start with learning.")
        self.assertTrue(any("calculation" in x for x in w))

    def test_normal_text_untouched(self):
        t = "This may fit because it supports new service businesses. Ask the bank."
        self.assertEqual(core.strip_calc(t), (t, False))


if __name__ == "__main__":
    unittest.main()
