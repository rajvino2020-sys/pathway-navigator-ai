import copy
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import core  # noqa: E402
import llm  # noqa: E402


def persona(**over):
    p = core.demo_profile()
    for k, v in over.items():
        p[k] = v
    return core.normalise_profile(p)


def route_for(p, rejections=None):
    tags, missing = core.derive_tags(p)
    rules = core.fired_rules(tags)
    r = core.fallback_route(p, tags, rules, rejections or [])
    result, warnings = core.finalize(r)
    return tags, rules, result, warnings


def ids(result, slot):
    return [d["pathway_id"] for d in result["doors"][slot]]


class TestLibrary(unittest.TestCase):
    def test_references_resolve(self):
        for pid, p in core.PATHWAYS.items():
            for a in p.get("alternatives", []):
                self.assertIn(a, core.PATHWAYS, f"{pid} -> {a}")
            for t in p.get("training_links", []):
                self.assertIn(t, core.TRAINING)
            self.assertTrue(p["official_sources"][0]["url"].startswith("https://"), pid)
            self.assertTrue(p.get("last_verified"), pid)

    def test_rule_tokens_known(self):
        known = set(core.PATHWAYS) | set(core.PLACEHOLDERS) | {"prepare_project"}
        for r in core.ENGINE["routing_rules_v2"]:
            for slot in ("start_here", "next", "later"):
                tok = r.get(slot, "")
                parts = tok.split("_or_") if "_or_" in tok else [tok]
                for t in parts:
                    self.assertTrue(t in known or t.startswith("sisfs"), f"unknown token {t}")


class TestProfile(unittest.TestCase):
    def test_amount_parsing_and_defaults(self):
        p = core.normalise_profile({"resources": {"money_available": {"amount": "Rs 10,000"}}})
        self.assertEqual(p["resources"]["money_available"]["amount"], 10000)
        self.assertEqual(p["resources"]["money_available"]["currency"], "INR")
        self.assertEqual(p["location"]["rural_or_urban"], "unknown")

    def test_unknown_money_is_not_zero(self):
        p = core.normalise_profile({})
        tags, missing = core.derive_tags(p)
        self.assertNotIn("money_0", tags)
        self.assertIn("money_available", missing)
        self.assertIn("rural_or_urban", missing)

    def test_garbage_input_does_not_crash(self):
        core.normalise_profile(None)
        core.normalise_profile("hello")
        core.normalise_profile({"barriers": "oops", "resources": 5})


class TestPersonaA(unittest.TestCase):
    def setUp(self):
        self.tags, self.rules, self.result, self.warnings = route_for(persona())

    def test_tags(self):
        for t in ("food_business", "idea_only", "early_stage", "money_low", "low_knowledge",
                  "network_low", "rural", "confidence_barrier"):
            self.assertIn(t, self.tags)
        self.assertNotIn("money_0", self.tags)
        self.assertNotIn("validated", self.tags)

    def test_rules_fired(self):
        whens = [r["when"] for r in self.rules]
        self.assertIn(["food_business", "idea_only"], whens)
        self.assertIn(["rural", "money_low", "network_low"], whens)
        self.assertNotIn(["validated", "finance_needed"], whens)

    def test_slots(self):
        self.assertEqual(ids(self.result, "start_here"), ["learning_program", "svep"])
        self.assertEqual(ids(self.result, "next"), ["pmfme"])
        self.assertEqual(ids(self.result, "later"), ["mudra"])
        self.assertEqual([n["pathway_id"] for n in self.result["doors"]["not_yet"]], ["sisfs"])

    def test_links_and_dates_come_from_library(self):
        for slot in ("start_here", "next", "later"):
            for d in self.result["doors"][slot]:
                self.assertTrue(d["official_source"]["url"].startswith("https://"))
                self.assertEqual(d["last_verified"], core.PATHWAYS[d["pathway_id"]]["last_verified"])

    def test_confidence_gate(self):
        self.assertTrue(self.result["prepare_me"])
        self.assertTrue(all(p["practice_script"] for p in self.result["prepare_me"]))

    def test_no_unsafe_wording(self):
        self.assertFalse([w for w in self.warnings if "unsafe" in w])


class TestOtherCases(unittest.TestCase):
    def test_money_zero_keeps_contribution_schemes_out_of_start(self):
        p = persona()
        p["resources"]["money_available"] = {"amount": 0, "currency": "INR", "confidence": "exact"}
        tags, rules, result, _ = route_for(core.normalise_profile(p))
        self.assertIn("money_0", tags)
        self.assertNotIn("pmegp", ids(result, "start_here"))
        self.assertNotIn("pmfme", ids(result, "start_here"))

    def test_rejection_removes_door_and_keeps_alternative(self):
        rej = [{"pathway_id": "svep", "reason": "not available in our block"}]
        _, _, result, _ = route_for(persona(), rej)
        everything = ids(result, "start_here") + ids(result, "next") + ids(result, "later")
        self.assertNotIn("svep", everything)
        self.assertIn("learning_program", ids(result, "start_here"))
        self.assertTrue(ids(result, "next"))

    def test_validated_with_funding_need_reaches_finance(self):
        p = persona()
        p["business_idea"]["idea_stage"] = "tested"
        p["support_needed"] = ["funding"]
        p["location"]["rural_or_urban"] = "urban"
        tags, rules, result, _ = route_for(core.normalise_profile(p))
        self.assertIn("validated", tags)
        self.assertIn(["validated", "finance_needed"], [r["when"] for r in rules])
        every = ids(result, "start_here") + ids(result, "next") + ids(result, "later")
        self.assertTrue({"pmegp", "mudra"} & set(every))
        self.assertEqual([], result["doors"]["not_yet"])   # idea_only is false


class TestUrbanTailoring(unittest.TestCase):
    def test_urban_service_idea(self):
        p = core.demo_profile()
        p["location"]["rural_or_urban"] = "urban"
        p["business_idea"].update({"sector": "services", "description": "tailoring", "idea_stage": "just_an_idea"})
        p["resources"]["money_available"] = {"amount": 30000, "currency": "INR", "confidence": "exact"}
        p["network_and_support"].update({"business_network": "limited", "family_support": "supportive"})
        p["barriers"] = ["lack_of_network"]
        tags, rules, result, _ = route_for(core.normalise_profile(p))
        self.assertIn("urban", tags)
        self.assertNotIn("rural", tags)
        self.assertTrue(rules)                                   # a rule must fire
        everything = ids(result, "start_here") + ids(result, "next") + ids(result, "later")
        self.assertNotIn("svep", everything)                     # rural-only door
        self.assertNotIn("pmfme", everything)                    # food-only door
        self.assertEqual(ids(result, "start_here"), ["learning_program"])
        self.assertIn("pmegp", ids(result, "later"))

    def test_default_never_sends_urban_to_svep(self):
        p = core.demo_profile()
        p["location"]["rural_or_urban"] = "urban"
        p["business_idea"]["idea_stage"] = "established"
        tags, _ = core.derive_tags(core.normalise_profile(p))
        r = core.fallback_route(core.normalise_profile(p), tags, [], [])
        self.assertNotIn("svep", [d["pathway_id"] for d in r["doors"]["start_here"]])


class TestFinalize(unittest.TestCase):
    def test_drops_unknown_overwrites_links_dedupes_trims(self):
        fake = {"doors": {
            "start_here": [{"pathway_id": "made_up_scheme"},
                           {"pathway_id": "svep", "official_source": {"url": "https://evil.example"}},
                           {"pathway_id": "learning_program"}, {"pathway_id": "pmegp"}],
            "next": [{"pathway_id": "svep"}, {"pathway_id": "pmfme"}],
            "later": [], "not_yet": [{"pathway_id": "sisfs", "reason": "later"}, {"pathway_id": "zzz"}]},
            "message_to_user": "You will get this loan, guaranteed."}
        result, warnings = core.finalize(fake)
        self.assertEqual(ids(result, "start_here"), ["svep", "learning_program"])
        self.assertNotIn("evil", result["doors"]["start_here"][0]["official_source"]["url"])
        self.assertEqual(ids(result, "next"), ["pmfme"])
        self.assertEqual(len(result["doors"]["not_yet"]), 1)
        self.assertTrue(any("unknown pathway id" in w for w in warnings))
        self.assertTrue(any("unsafe" in w for w in warnings))

    def test_garbage_route(self):
        result, warnings = core.finalize("nonsense")
        self.assertIn("no valid doors returned", warnings)


class TestApi(unittest.TestCase):
    def setUp(self):
        appmod.CACHE.clear()
        self.c = appmod.app.test_client()

    def test_demo_mode_end_to_end(self):
        with mock.patch.object(llm, "available", return_value=False):
            x = self.c.post("/api/extract", json={"text": "hello"}).get_json()
            self.assertEqual(x["engine"], "demo")
            self.assertEqual(x["questions"], [])
            r = self.c.post("/api/route", json={"profile": x["profile"]}).get_json()
            self.assertEqual(r["status"], "ok")
            self.assertEqual(r["engine"], "rules_fallback")
            self.assertEqual(ids(r["result"], "next"), ["pmfme"])

    def test_missing_money_asks_instead_of_routing(self):
        p = core.demo_profile()
        p["resources"]["money_available"]["amount"] = None
        r = self.c.post("/api/route", json={"profile": p}).get_json()
        self.assertEqual(r["status"], "needs_clarification")
        self.assertEqual(len(r["questions"]), 1)

    def test_rejection_without_reason_asks(self):
        r = self.c.post("/api/route", json={"profile": core.demo_profile(),
                                            "rejections": [{"pathway_id": "svep", "reason": ""}]}).get_json()
        self.assertEqual(r["status"], "needs_clarification")

    def test_model_path_filters_bad_output(self):
        model_route = {"snapshot": {"goal": "x"},
                       "doors": {"start_here": [{"pathway_id": "learning_program", "status": "available_now",
                                                 "why_it_may_fit": "free", "next_step": "begin"},
                                                {"pathway_id": "appropriate_finance_pathway"}],
                                 "next": [{"pathway_id": "pmfme", "status": "prepare_first",
                                           "why_it_may_fit": "food", "next_step": "test product"}],
                                 "later": [], "not_yet": []},
                       "message_to_user": "Start with the free learning program."}
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", return_value=model_route):
            r = self.c.post("/api/route", json={"profile": core.demo_profile()}).get_json()
        self.assertEqual(r["engine"], "model")
        self.assertEqual(ids(r["result"], "start_here"), ["learning_program"])
        self.assertTrue(any("unknown pathway id" in w for w in r["warnings"]))

    def test_repeat_request_uses_cache(self):
        model_route = {"doors": {"start_here": [{"pathway_id": "learning_program", "status": "available_now",
                                                 "why_it_may_fit": "free", "next_step": "begin"}],
                                 "next": [], "later": [], "not_yet": []}, "message_to_user": "hi"}
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", return_value=model_route) as m:
            self.c.post("/api/route", json={"profile": core.demo_profile()})
            self.c.post("/api/route", json={"profile": core.demo_profile()})
        self.assertEqual(m.call_count, 1)

    def test_model_failure_falls_back(self):
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", side_effect=llm.LLMError("boom")):
            r = self.c.post("/api/route", json={"profile": core.demo_profile()}).get_json()
        self.assertEqual(r["engine"], "rules_fallback")
        self.assertTrue(any("boom" in w for w in r["warnings"]))
        self.assertTrue(ids(r["result"], "start_here"))

    def test_model_extraction_asks_for_missing(self):
        raw = copy.deepcopy(core.demo_profile())
        raw["resources"]["money_available"]["amount"] = None
        raw["evidence"] = {"rural_or_urban": "my village", "money_amount": None}
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", return_value=raw):
            x = self.c.post("/api/extract", json={"text": "I want to sell food in my village"}).get_json()
        self.assertEqual(x["missing"], ["money_available"])
        self.assertEqual(len(x["questions"]), 1)

    def test_empty_text_rejected(self):
        self.assertEqual(self.c.post("/api/extract", json={"text": " "}).status_code, 400)

    def test_index_served(self):
        self.assertEqual(self.c.get("/").status_code, 200)


class TestEvidenceGuard(unittest.TestCase):
    TAMIL = "நான் டெய்லரிங் தொழில் பண்ணலாம் என்கிட்ட முதலீடு அவ்வளவா இல்ல"

    def setUp(self):
        appmod.CACHE.clear()
        self.c = appmod.app.test_client()

    def _extract(self, raw, text, prior=None):
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", return_value=raw):
            return self.c.post("/api/extract", json={"text": text, "prior_profile": prior}).get_json()

    def test_guessed_village_and_zero_money_are_reset_and_asked_in_tamil(self):
        raw = core.demo_profile()           # says rural + 10000
        raw["resources"]["money_available"]["amount"] = 0
        raw["evidence"] = {"rural_or_urban": None, "money_amount": None}
        raw["followup_questions"] = {"money_available": "உங்களிடம் எவ்வளவு பணம் உள்ளது?",
                                     "rural_or_urban": "நீங்கள் கிராமத்தில், நகரத்தில் வசிக்கிறீர்களா?"}
        x = self._extract(raw, self.TAMIL)
        self.assertEqual(x["missing"], ["money_available", "rural_or_urban"])
        self.assertEqual(x["questions"][0], core.QUESTION_TEXT["money_available"]["Tamil"])
        self.assertEqual(x["questions"][1], core.QUESTION_TEXT["rural_or_urban"]["Tamil"])
        self.assertEqual(x["profile"]["language"]["preferred_language"], "Tamil")
        self.assertTrue(x["guard"])

    def test_real_evidence_is_kept(self):
        raw = core.demo_profile()
        raw["evidence"] = {"rural_or_urban": "I live in a village", "money_amount": "10,000 rupees"}
        x = self._extract(raw, "I only have 10,000 rupees. I live in a village.")
        self.assertEqual(x["missing"], [])
        self.assertIn("rural", x["tags"])

    def test_invented_quote_is_rejected(self):
        raw = core.demo_profile()
        raw["evidence"] = {"rural_or_urban": "my village home", "money_amount": "10,000 rupees"}
        x = self._extract(raw, "I only have 10,000 rupees.")
        self.assertEqual(x["missing"], ["rural_or_urban"])

    def test_unchanged_prior_values_are_not_rechecked(self):
        prior = core.normalise_profile(core.demo_profile())
        raw = core.demo_profile()
        raw["evidence"] = {"rural_or_urban": None, "money_amount": None}
        x = self._extract(raw, "yes that is right", prior)
        self.assertEqual(x["missing"], [])

    def test_route_payload_hides_evidence(self):
        p = core.normalise_profile(core.demo_profile())
        p["evidence"] = {"rural_or_urban": "x"}
        msgs = core.build_route_messages(p, [], [], [])
        self.assertNotIn("evidence", msgs[1]["content"])


class TestLanguage(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(core.detect_language("நான் தொழில் செய்ய வேண்டும்"), "Tamil")
        self.assertEqual(core.detect_language("मुझे दुकान खोलनी है"), "Hindi")
        self.assertEqual(core.detect_language("I want to start a shop"), None)
        self.assertEqual(core.detect_language("12345"), None)

    def test_choose(self):
        self.assertEqual(core.choose_language("village", "English", "Tamil", "English"), "Tamil")   # follow-up keeps Tamil
        self.assertEqual(core.choose_language("hello", "Telugu", None, "English"), "Telugu")        # dropdown
        self.assertEqual(core.choose_language("मुझे दुकान", "Marathi", None, None), "Marathi")
        self.assertEqual(core.choose_language("hello", "English", None, "Kannada"), "Kannada")
        self.assertEqual(core.choose_language("hello", "English", None, None), "English")

    def test_all_questions_translated_for_all_languages(self):
        for key, per in core.QUESTION_TEXT.items():
            for lang in core.LANGS:
                self.assertTrue(per.get(lang), f"{key}/{lang}")

    def test_followup_answer_in_english_keeps_tamil_questions(self):
        c = appmod.app.test_client()
        appmod.CACHE.clear()
        prior = core.normalise_profile(core.demo_profile())
        prior["language"]["preferred_language"] = "Tamil"
        raw = core.demo_profile()
        raw["resources"]["money_available"]["amount"] = None
        raw["evidence"] = {"rural_or_urban": None, "money_amount": None}
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm, "chat_json", return_value=raw):
            x = c.post("/api/extract", json={"text": "village", "prior_profile": prior}).get_json()
        self.assertEqual(x["profile"]["language"]["preferred_language"], "Tamil")
        self.assertEqual(x["questions"], [core.QUESTION_TEXT["money_available"]["Tamil"]])

    def test_rejection_question_localised(self):
        c = appmod.app.test_client()
        p = core.normalise_profile(core.demo_profile())
        p["language"]["preferred_language"] = "Hindi"
        r = c.post("/api/route", json={"profile": p, "rejections": [{"pathway_id": "svep", "reason": ""}]}).get_json()
        self.assertEqual(r["questions"], [core.QUESTION_TEXT["rejection_reason"]["Hindi"]])


class TestRefusedDoors(unittest.TestCase):
    def _route(self, notes, slot_for_svep="later"):
        doors = {"start_here": [{"pathway_id": "learning_program"}], "next": [{"pathway_id": "mudra"}],
                 "later": [], "not_yet": []}
        doors[slot_for_svep] = doors[slot_for_svep] + [{"pathway_id": "svep", "fix": "ask BRC"}]
        return {"doors": doors, "rejection_notes": notes, "message_to_user": "m"}

    def test_local_availability_removes_door_everywhere(self):
        rej = [{"pathway_id": "svep", "reason": "not available in our block"}]
        r, w = core.finalize(self._route([{"pathway_id": "svep", "class": "local_availability"}]), rej)
        every = ids(r, "start_here") + ids(r, "next") + ids(r, "later")
        self.assertNotIn("svep", every)
        self.assertTrue(any("local_availability" in x for x in w))

    def test_fixable_stays_in_later_only(self):
        rej = [{"pathway_id": "svep", "reason": "need a plan"}]
        r, _ = core.finalize(self._route([{"pathway_id": "svep", "class": "fixable_requirement"}]), rej)
        self.assertIn("svep", ids(r, "later"))

    def test_refused_door_never_in_first_slots(self):
        rej = [{"pathway_id": "svep", "reason": "x"}]
        r, w = core.finalize(self._route([{"pathway_id": "svep", "class": "fixable_requirement"}], "start_here"), rej)
        self.assertNotIn("svep", ids(r, "start_here"))
        self.assertTrue(any("removed refused door from start_here" in x for x in w))

    def test_no_rejections_unchanged(self):
        r, _ = core.finalize(self._route([], "start_here"))
        self.assertIn("svep", ids(r, "next") + ids(r, "later") + ids(r, "start_here"))


class TestRequestFallbacks(unittest.TestCase):
    """The client must adapt if the API rejects an optional setting."""

    class Resp:
        def __init__(self, status, payload=None, text=""):
            self.status_code, self._p, self.text = status, payload, text

        def json(self):
            return self._p

    OK = {"choices": [{"message": {"content": "{\"a\": 1}"}}], "usage": {"completion_tokens": 7}}

    def test_sends_reasoning_effort_and_json_mode(self):
        calls = []

        def fake_post(url, json=None, **kw):
            calls.append(json)
            return self.Resp(200, self.OK)
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm.requests, "post", fake_post):
            llm.chat_json([{"role": "user", "content": "x"}], effort="minimal")
        self.assertEqual(calls[0]["reasoning_effort"], "minimal")
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(llm.LAST_USAGE["completion_tokens"], 7)

    def test_drops_rejected_settings_one_by_one(self):
        calls = []

        def fake_post(url, json=None, **kw):
            calls.append(dict(json))
            if "reasoning_effort" in json or "response_format" in json:
                return self.Resp(400, text="unsupported")
            return self.Resp(200, self.OK)
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm.requests, "post", fake_post):
            out = llm.chat([{"role": "user", "content": "x"}], True, effort="low")
        self.assertEqual(out, '{"a": 1}')
        self.assertEqual(len(calls), 3)
        self.assertIn("reasoning_effort", calls[0])
        self.assertNotIn("reasoning_effort", calls[1])
        self.assertIn("response_format", calls[1])
        self.assertNotIn("response_format", calls[2])

    def test_real_errors_still_raise(self):
        with mock.patch.object(llm, "available", return_value=True), \
             mock.patch.object(llm.requests, "post", lambda *a, **k: self.Resp(401, text="Unauthorized")):
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "x"}])


class TestJsonParsing(unittest.TestCase):
    def test_fenced_and_chatty(self):
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json('Sure! {"a": 2} hope that helps'), {"a": 2})
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json here")


if __name__ == "__main__":
    unittest.main()
