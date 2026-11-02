"""Core logic for the Pathway Navigator.

The model understands the person and writes the explanation.
Code does everything that must be exact: tags, rules, links, dates, safety checks.
"""
import copy
import json
import pathlib
import re

DATA = pathlib.Path(__file__).parent / "data"


def _load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


LIB = _load("pathway_library.json")
ENGINE = _load("routing_engine.json")
SCHEMA = _load("user_profile_schema.json")

PATHWAYS = {p["id"]: p for p in LIB["pathways"]}
NOT_FIRST = {p["id"]: p for p in LIB.get("not_first_doors", [])}
TRAINING = {t["id"]: t for t in LIB.get("training_pathways", [])}

DISCLAIMER = ("This is guidance, not a decision. Please confirm eligibility and the "
              "current process with the official office, bank or lender.")

SLOT_CAPS = {"start_here": 2, "next": 1, "later": 1}
CONF_BARRIERS = {"low_confidence", "fear_of_rejection", "difficulty_approaching_people",
                 "difficulty_explaining_idea", "difficulty_handling_difficult_questions"}

# ---------------------------------------------------------------- profile

PROFILE_DEFAULT = {
    "location": {"country": "India", "state_or_region": None, "district_or_area": None,
                 "rural_or_urban": "unknown"},
    "goal": {"primary": "start_business", "description": ""},
    "business_idea": {"has_idea": True, "description": None, "sector": "unknown",
                      "product_or_service": None, "target_customer": None,
                      "idea_stage": "unknown"},
    "skills_and_experience": {"skills": [], "relevant_experience": [],
                              "business_experience": "unknown"},
    "resources": {"money_available": {"amount": None, "currency": None, "confidence": "unknown"},
                  "available_resources": [], "time_available": "unknown"},
    "knowledge": {"business_basics": "unknown", "finance": "unknown", "marketing": "unknown",
                  "legal_and_registration": "unknown", "funding_options": "unknown",
                  "support_programmes": "unknown"},
    "network_and_support": {"business_network": "unknown", "mentor_access": "unknown",
                            "family_support": "unknown", "community_support": "unknown"},
    "barriers": [],
    "confidence_and_readiness": {"overall_confidence": "unknown", "approaching_strangers": "unknown",
                                 "asking_for_help": "unknown", "asking_for_funding": "unknown",
                                 "handling_rejection": "unknown", "explaining_idea": "unknown",
                                 "handling_criticism": "unknown", "readiness_level": "unknown"},
    "support_needed": [],
    "language": {"preferred_language": "English", "comfortable_languages": [],
                 "needs_simple_language": True},
    "journey_state": {"current_stage": "discover", "current_focus": None, "selected_pathways": [],
                      "completed_actions": [], "next_action": None, "last_check_in": None,
                      "rejections": []},
}


def _fill(default, value):
    """Fill missing keys from default; keep what the model gave us."""
    if isinstance(default, dict):
        out = {}
        value = value if isinstance(value, dict) else {}
        for k, dv in default.items():
            out[k] = _fill(dv, value.get(k))
        for k, v in value.items():      # keep extra keys (e.g. dynamic_notes)
            if k not in out:
                out[k] = v
        return out
    if isinstance(default, list):
        return value if isinstance(value, list) else []
    return default if value is None and default is not None else value


def _to_number(x):
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    m = re.sub(r"[^\d.]", "", str(x))
    try:
        return float(m) if "." in m else int(m)
    except ValueError:
        return None


def normalise_profile(p):
    p = _fill(PROFILE_DEFAULT, p if isinstance(p, dict) else {})
    m = p["resources"]["money_available"]
    m["amount"] = _to_number(m.get("amount"))
    if m["amount"] is not None and not m.get("currency"):
        m["currency"] = "INR"
    return p


# ---------------------------------------------------------------- tags & rules

def derive_tags(p):
    idea, res = p["business_idea"], p["resources"]
    k, n = p["knowledge"], p["network_and_support"]
    support, barriers = set(p.get("support_needed", [])), set(p.get("barriers", []))
    stage = idea["idea_stage"]
    loc = p["location"]["rural_or_urban"]
    m = res["money_available"]
    amt = m["amount"]
    tags = set()

    if stage in ("just_an_idea", "exploring"):
        tags.add("idea_only")
    if stage in ("just_an_idea", "exploring", "tested", "early_sales"):
        tags.add("early_stage")
    if stage in ("tested", "early_sales"):
        tags.add("validated")
    if idea["sector"] == "food":
        tags.add("food_business")
    if loc == "rural":
        tags.add("rural")
    if loc in ("urban", "semi_urban"):
        tags.add("urban")

    if amt == 0:
        tags.add("money_0")                       # None means unknown, not zero
    elif amt is not None and amt <= 50000 and m.get("currency") in ("INR", None):
        tags.add("money_low")

    if any(k[f] == "low" for f in ("business_basics", "funding_options", "support_programmes")):
        tags.add("low_knowledge")
    if n["business_network"] in ("none", "limited") and n["mentor_access"] in ("none", "limited"):
        tags.add("network_low")
    if "funding" in support:
        tags.add("finance_needed")
    if "training" in support or "low_knowledge" in tags:
        tags.add("needs_training")
    if "mentor" in support or n["mentor_access"] in ("none", "limited"):
        tags.add("needs_mentor")
    if support & {"business_connections", "government_support"}:
        tags.add("needs_business_support")
    if "market_testing" in support or "uncertain_idea" in barriers:
        tags.add("needs_market_testing")
    if barriers & CONF_BARRIERS:
        tags.add("confidence_barrier")

    missing = []
    if amt is None:
        missing.append("money_available")
    if loc == "unknown":
        missing.append("rural_or_urban")
    return sorted(tags), missing


def fired_rules(tags):
    t = set(tags)
    return [r for r in ENGINE["routing_rules_v2"] if set(r["when"]) <= t]


LANGS = {"English": "en-IN", "Hindi": "hi-IN", "Tamil": "ta-IN", "Telugu": "te-IN",
         "Bengali": "bn-IN", "Marathi": "mr-IN"}

QUESTION_TEXT = {
    "money_available": {
        "English": "About how much money do you have to start, in rupees? (It is fine to say 0.)",
        "Tamil": "தொடங்குவதற்கு உங்களிடம் சுமார் எவ்வளவு பணம் இருக்கிறது? (ரூபாயில்; 0 என்றாலும் பரவாயில்லை.)",
        "Hindi": "शुरू करने के लिए आपके पास लगभग कितने पैसे हैं? (रुपये में; 0 भी बता सकते हैं।)",
        "Telugu": "ప్రారంభించడానికి మీ దగ్గర సుమారు ఎంత డబ్బు ఉంది? (రూపాయల్లో; 0 అని చెప్పినా పర్వాలేదు.)",
        "Bengali": "শুরু করার জন্য আপনার কাছে আনুমানিক কত টাকা আছে? (টাকায়; ০ হলেও ঠিক আছে।)",
        "Marathi": "सुरुवात करण्यासाठी तुमच्याकडे साधारण किती पैसे आहेत? (रुपयांत; ० असले तरी चालेल.)",
    },
    "rural_or_urban": {
        "English": "Do you live in a village, a town or a city?",
        "Tamil": "நீங்கள் கிராமத்தில், சிறு நகரத்தில் அல்லது பெரிய நகரத்தில் வசிக்கிறீர்களா?",
        "Hindi": "आप गाँव में, कस्बे में या शहर में रहते हैं?",
        "Telugu": "మీరు గ్రామంలో, పట్టణంలో లేదా నగరంలో నివసిస్తున్నారా?",
        "Bengali": "আপনি গ্রামে, ছোট শহরে নাকি বড় শহরে থাকেন?",
        "Marathi": "तुम्ही गावात, लहान शहरात की मोठ्या शहरात राहता?",
    },
    "rejection_reason": {
        "English": "What were you told when you were refused?",
        "Tamil": "மறுக்கப்பட்டபோது உங்களிடம் என்ன சொன்னார்கள்?",
        "Hindi": "मना करते समय आपको क्या बताया गया था?",
        "Telugu": "తిరస్కరించినప్పుడు మీకు ఏమి చెప్పారు?",
        "Bengali": "প্রত্যাখ্যান করার সময় আপনাকে কী বলা হয়েছিল?",
        "Marathi": "नकार देताना तुम्हाला काय सांगितले गेले?",
    },
}
QUESTIONS = {k: v["English"] for k, v in QUESTION_TEXT.items()}

_SCRIPTS = {"Tamil": (0x0B80, 0x0BFF), "Telugu": (0x0C00, 0x0C7F),
            "Bengali": (0x0980, 0x09FF), "Hindi": (0x0900, 0x097F)}   # Devanagari: Hindi or Marathi


def detect_language(text):
    """Language from the script of the text, or None for Latin/unclear text."""
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return None
    best, share = None, 0.0
    for name, (lo, hi) in _SCRIPTS.items():
        s = sum(1 for ch in letters if lo <= ord(ch) <= hi) / len(letters)
        if s > share:
            best, share = name, s
    return best if share >= 0.3 else None


def choose_language(text, ui_language, prior_language, model_language):
    d = detect_language(text)
    if d:
        return "Marathi" if (d == "Hindi" and ui_language == "Marathi") else d
    for cand in (prior_language, ui_language):
        if cand in LANGS and cand != "English":
            return cand
    if isinstance(model_language, str) and model_language.strip() and model_language.lower() != "unknown":
        return model_language.strip()
    return "English"


def question_text(key, language):
    return QUESTION_TEXT[key].get(language) or QUESTION_TEXT[key]["English"]


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower() if isinstance(s, str) else ""


def apply_evidence_guard(profile, text, prior=None):
    """Reset location or money if the model cannot point to the person's own words for it.
    Only checks values that are new or changed in this turn. Returns a list of notes."""
    notes = []
    prior = prior if isinstance(prior, dict) else {}
    ev = profile.get("evidence") if isinstance(profile.get("evidence"), dict) else {}
    t = _norm(text)

    prior_loc = ((prior.get("location") or {}).get("rural_or_urban")) or "unknown"
    loc = profile["location"]["rural_or_urban"]
    if loc != "unknown" and loc != prior_loc:
        q = _norm(ev.get("rural_or_urban"))
        if not q or q not in t:
            profile["location"]["rural_or_urban"] = "unknown"
            notes.append("location was not stated; asking")

    prior_amt = (((prior.get("resources") or {}).get("money_available") or {}).get("amount"))
    m = profile["resources"]["money_available"]
    if m["amount"] is not None and m["amount"] != prior_amt:
        q = _norm(ev.get("money_amount"))
        if not q or q not in t:
            m.update({"amount": None, "currency": None, "confidence": "unknown"})
            notes.append("amount was not stated; asking")
    return notes


def localized_questions(profile, missing):
    lang = (profile.get("language") or {}).get("preferred_language") or "English"
    fq = profile.get("followup_questions") if isinstance(profile.get("followup_questions"), dict) else {}
    out = []
    for m in missing:
        if lang in QUESTION_TEXT[m]:                       # tested translation first
            out.append(QUESTION_TEXT[m][lang])
        else:                                              # another language: ask the model's version
            q = fq.get(m)
            out.append(q.strip() if isinstance(q, str) and q.strip() else QUESTION_TEXT[m]["English"])
    return out[:2]


# ---------------------------------------------------------------- prompts

EXTRACT_SYSTEM = """You turn what a person says about wanting to start something of their own into a structured profile.
Return ONLY a JSON object that follows the TEMPLATE keys exactly.
Rules:
- Use only what the person said. If something is not stated, use "unknown" (or null for numbers and text). NEVER guess.
- location.rural_or_urban: set it ONLY if the person says where they live (village or rural area = "rural"; town = "semi_urban"; city or a place you are certain is a big city, such as Chennai = "urban"). Do NOT infer it from the business type, the language, family situation or lack of money. If they did not say, use "unknown".
- resources.money_available.amount: a number in rupees ONLY if the person states an amount (convert lakh/thousand words), or clearly says they have no money at all (then 0). "Not much" or "a little" means null, because we will ask for the amount.
- idea_stage: just_an_idea, exploring, tested (has tried selling to a few people), early_sales, established, or unknown.
- barriers and support_needed: use only values from the template lists, and only barriers the person clearly expresses.
- language.preferred_language: the language the person wrote in (or the UI language hint).
- evidence: for rural_or_urban and money_amount, copy the EXACT words from NEW_TEXT (original language, unchanged) that justify your value. If the value is "unknown"/null, or unchanged from PRIOR_PROFILE, use null.
- followup_questions: write two short, polite questions in the person's language. money_available: roughly how much money do they have to start (0 is fine). rural_or_urban: do they live in a village, a town or a city.
- Keep journey_state.rejections as given in PRIOR_PROFILE if provided.
- If PRIOR_PROFILE is provided, update it with the NEW_TEXT (the answer to a follow-up question) and keep everything else.
- No commentary. JSON only."""

ROUTE_SYSTEM = """You are the routing engine of the Pathway Navigator. You help people who do not know how the entrepreneurial world works find the right "doors" in the right order. You decide which support pathway makes sense NOW, what must be confirmed, and what to do next. You do not tell people how to run a business.

INPUTS (JSON): USER_PROFILE, TAGS (true facts about the person), FIRED_RULES, CONFIDENCE_GATE, REJECTION_HANDLING, REJECTIONS, PATHWAY_LIBRARY.

HARD RULES
1. Only recommend pathways whose id exists in PATHWAY_LIBRARY.pathways. Never invent a scheme, figure, eligibility rule, link or date. Facts come only from the library.
2. Never say "you qualify" or promise approval. Say "this may fit" and say what must be confirmed with the official office, bank or lender.
3. Never show a later-stage door as a first action.
4. Write message_to_user in USER_PROFILE.language.preferred_language, in simple short sentences, warm and practical. Do not judge the person. Write the other text fields in the same language.
5. Output valid JSON only, in the schema below.
6. If REJECTIONS is not empty, message_to_user must begin by kindly acknowledging the refusal, say in one sentence what it probably means, and name the next door to try or the one thing to fix. Put the same fix in the "fix" field of the door it concerns.
7. A user's personal barrier (for example, family disagreement, lack of confidence or lack of support) is NOT an official scheme requirement unless the PATHWAY_LIBRARY explicitly says so. Never turn a personal barrier into a requirement, eligibility condition, document, or fix. If the refusal reason mentions family or parents, describe it as a personal/social barrier and focus on official pathway requirements instead.
8. Be brief. Every text field is one short sentence (under 25 words). must_confirm has at most 2 items. what_to_say only for start_here doors. prepare_me has at most 3 items. message_to_user at most 3 sentences.

HOW TO BUILD THE ROUTE
- FIRED_RULES give start_here / next / later suggestions. Some values are placeholders (for example "prepare_project", "appropriate_finance_pathway", "pmfme_or_pmegp"). Placeholders are not pathway ids: resolve each to a real library id that fits her stage and money, or turn it into a prepare_me action. Never output a placeholder as a pathway_id.
- If several rules fire and disagree: earlier-stage, no-cost doors come first; a door appears in only one slot (its earliest); start_here has up to 2 doors, and only when both are free to start and can be done in parallel; next has 1 door; later has 1 door. At most 4 doors in total.
- If TAGS has money_0, do not put a door that needs her own contribution (pmegp, pmfme) in start_here. Do not calculate project size, loan size, subsidy amount or contribution amounts unless the user explicitly asks for a calculation.
- Food idea plus idea_only: pmfme goes in next, with the condition that the product is tested and the activity and local route are confirmed.
- If a door has training_links, set training_step from the library's training_pathways: the name, whether required, and how to access it if known, otherwise "ask the office to confirm".
- Community-based doors (svep): say whom to ask (library entry_route.how_to_approach), that coverage depends on the area, and that there is no individual online application.
- Lender-based doors (mudra): she applies through a participating lender, who decides.
- Add one not_yet entry when a later-stage door applies (for example sisfs for a person who only has an idea), with a one-sentence reason.

CONFIDENCE GATE
If TAGS has confidence_barrier, fill prepare_me with 2 or 3 practice actions chosen from CONFIDENCE_GATE.prepare_actions that match the first door. For each, give a short practice_script (1-2 simple sentences she can say aloud), based on that door's what_to_say when present.

REJECTIONS
Follow REJECTION_HANDLING. For each rejection:
- Classify the reason: fixable_requirement, not_ready, not_eligible_or_unclear, lender_decision, local_availability, or other.
- local_availability or not_eligible_or_unclear: remove the door from EVERY slot and bring forward its alternatives from the library (skip any already rejected). You may mention in message_to_user what she can still ask locally.
- fixable_requirement, not_ready or lender_decision: keep the door only in "later" and fill "fix" with the one thing to change. Never in start_here or next.
- List every rejection and its class in rejection_notes.
- Never say "try again" alone. Say what to change or which door to try next.

OUTPUT SCHEMA
{
  "snapshot": {"goal": str, "idea": str, "strengths": [str], "resources": str,
               "main_barriers": [str], "readiness_level": str, "recommended_first_step": str},
  "doors": {"start_here": [DOOR], "next": [DOOR], "later": [DOOR],
            "not_yet": [{"pathway_id": str, "reason": str}]},
  "prepare_me": [{"action": str, "practice_script": str}],
  "rejection_notes": [{"pathway_id": str, "class": str}],
  "message_to_user": str
}
DOOR = {"pathway_id": str, "status": "available_now"|"prepare_first"|"possible_later"|"needs_local_confirmation",
        "why_it_may_fit": str, "must_confirm": [str], "what_to_prepare": str, "next_step": str,
        "what_to_say": str|null, "training_step": str|null, "fix": str|null}
Do not include URLs or dates; the application adds them from the library."""

PHOTO_SYSTEM = """You help a person who may be new to business paperwork. They photographed a notice, poster, form or letter.
Return ONLY JSON:
{"readable": bool, "summary": str, "plain_language_explanation": str, "key_points": [str],
 "related_pathway_ids": [str], "questions_to_ask_the_office": [str]}
Rules: explain in simple words in the requested language. Do not invent details that are not visible. If the image is unreadable, set readable=false and say what to retake. related_pathway_ids may only contain ids from PATHWAY_IDS and only if the document clearly relates to them. Never say the person qualifies for anything."""


def extraction_template():
    t = copy.deepcopy(SCHEMA["user_profile"])
    for k in ("user_id", "created_at", "updated_at", "dynamic_notes", "routing_tags"):
        t.pop(k, None)
    t["evidence"] = {"rural_or_urban": "exact words from NEW_TEXT or null",
                     "money_amount": "exact words from NEW_TEXT or null"}
    t["followup_questions"] = {"money_available": "question in the person's language",
                               "rural_or_urban": "question in the person's language"}
    return t


def library_for_model():
    lib = copy.deepcopy(LIB)
    for p in lib["pathways"]:
        p.pop("verification_notes", None)
        p.pop("official_sources", None)   # links are added by code
    return lib


def build_route_messages(profile, tags, rules, rejections):
    clean = {k: v for k, v in profile.items() if k not in ("evidence", "followup_questions")}
    payload = {
        "USER_PROFILE": clean,
        "TAGS": tags,
        "FIRED_RULES": rules,
        "CONFIDENCE_GATE": ENGINE["confidence_gate"],
        "REJECTION_HANDLING": ENGINE["rejection_handling"],
        "REJECTIONS": rejections,
        "PATHWAY_LIBRARY": library_for_model(),
    }
    return [{"role": "system", "content": ROUTE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def build_extract_messages(text, ui_language, prior_profile=None):
    payload = {"TEMPLATE": extraction_template(), "UI_LANGUAGE_HINT": ui_language,
               "NEW_TEXT": text}
    if prior_profile:
        payload["PRIOR_PROFILE"] = prior_profile
    return [{"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


# ---------------------------------------------------------------- finalise

UNSAFE = re.compile(r"\b(you (definitely |will )?(qualify|are eligible|will get|will receive|are sure to)|"
                    r"guarantee[sd]?|100% (eligible|approved))\b", re.I)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _source(pid):
    p = PATHWAYS.get(pid)
    if not p:
        return None
    s = (p.get("official_sources") or [{}])[0]
    return {"label": s.get("label", ""), "url": s.get("url", "")}


REMOVE_CLASSES = {"local_availability", "not_eligible_or_unclear"}


def finalize(route, rejections=None):
    """Make the model's output safe and exact. Returns (result, warnings)."""
    warnings = []
    route = route if isinstance(route, dict) else {}
    rejected = {r.get("pathway_id") for r in (rejections or []) if isinstance(r, dict)}
    classes = {n.get("pathway_id"): str(n.get("class") or "")
               for n in (route.get("rejection_notes") or []) if isinstance(n, dict)}
    doors_in = route.get("doors") or {}
    seen, doors_out = set(), {}
    for slot in ("start_here", "next", "later"):
        out = []
        for d in doors_in.get(slot) or []:
            pid = d.get("pathway_id") if isinstance(d, dict) else None
            if pid not in PATHWAYS:
                warnings.append(f"dropped unknown pathway id: {pid!r}")
                continue
            if pid in seen:
                continue
            if pid in rejected and slot in ("start_here", "next"):
                warnings.append(f"removed refused door from {slot}: {pid}")
                continue
            if pid in rejected and classes.get(pid) in REMOVE_CLASSES:
                warnings.append(f"removed refused door ({classes.get(pid)}): {pid}")
                continue
            if len(out) >= SLOT_CAPS[slot]:
                warnings.append(f"trimmed extra door in {slot}: {pid}")
                continue
            seen.add(pid)
            d = dict(d)
            d["name"] = PATHWAYS[pid]["pathway_name"]
            d["official_source"] = _source(pid)
            d["last_verified"] = PATHWAYS[pid].get("last_verified", "unknown")
            d.setdefault("must_confirm", [])
            out.append(d)
        doors_out[slot] = out
    not_yet = []
    for n in doors_in.get("not_yet") or []:
        pid = n.get("pathway_id") if isinstance(n, dict) else None
        if pid in NOT_FIRST:
            not_yet.append({"pathway_id": pid, "name": NOT_FIRST[pid]["pathway_name"],
                            "reason": n.get("reason") or NOT_FIRST[pid]["reason"],
                            "official_source": {"label": NOT_FIRST[pid]["pathway_name"],
                                                "url": NOT_FIRST[pid].get("official_source", "")}})
        else:
            warnings.append(f"dropped unknown not_yet id: {pid!r}")
    doors_out["not_yet"] = not_yet

    result = {
        "snapshot": route.get("snapshot") or {},
        "doors": doors_out,
        "prepare_me": route.get("prepare_me") or [],
        "message_to_user": route.get("message_to_user") or "",
        "disclaimer": DISCLAIMER,
    }
    scan = {"d": result["doors"], "m": result["message_to_user"], "p": result["prepare_me"]}
    for s in _strings(scan):
        if UNSAFE.search(s):
            warnings.append(f"unsafe wording flagged: {s[:80]}")
    if not any(doors_out[s] for s in ("start_here", "next", "later")):
        warnings.append("no valid doors returned")
    return result, warnings


# ---------------------------------------------------------------- fallback router (no model)

PLACEHOLDERS = {"appropriate_finance_pathway": ["mudra", "pmegp"]}

PRACTICE = {
    "explain_my_idea": "I want to start a small [business]. I know how to [skill]. I plan to sell to [customers] near me.",
    "ask_for_funding": "I need about Rs [amount] for [purpose]. I expect to earn from [activity] and repay from [income].",
    "approach_someone": "Hello. I am starting a small [business]. Could you tell me who I should speak to about support?",
    "handle_difficult_questions": "That is a fair question. I do not know yet, but I will find out and come back to you.",
    "handle_rejection": "Thank you for your time. Could you tell me what I should change so I can try again?",
}


def _resolve(token):
    if not token:
        return []
    if token in PATHWAYS:
        return [token]
    if token in PLACEHOLDERS:
        return PLACEHOLDERS[token]
    if "_or_" in token:
        return [t for t in token.split("_or_") if t in PATHWAYS]
    return []


def fallback_route(profile, tags, rules, rejections):
    """Deterministic route from the rules. Used when the model is unavailable."""
    rejected = {r.get("pathway_id") for r in rejections or []}
    slots = {"start_here": [], "next": [], "later": []}
    seen = set()
    for slot in ("start_here", "next", "later"):
        for r in rules:
            for pid in _resolve(r.get(slot)):
                if pid in seen or pid in rejected or len(slots[slot]) >= SLOT_CAPS[slot]:
                    continue
                slots[slot].append(pid)
                seen.add(pid)
                break
    if not any(slots.values()):          # nothing fired: safe default
        for pid in ("learning_program", "svep"):
            if pid in rejected or (pid == "svep" and "rural" not in tags):
                continue
            slots["start_here"].append(pid)

    def door(pid, slot):
        p = PATHWAYS[pid]
        status = {"start_here": "available_now", "next": "prepare_first", "later": "possible_later"}[slot]
        if pid == "svep":
            status = "needs_local_confirmation"
        training = None
        for tid in p.get("training_links", []):
            t = TRAINING[tid]
            training = f"{t['name']}: " + (t.get("when_required") or "included in the scheme") \
                       + ". How to access: confirm with the office."
        return {"pathway_id": pid, "status": status,
                "why_it_may_fit": p.get("why_it_may_fit", ""),
                "must_confirm": [p["why_it_may_not_fit"]] if p.get("why_it_may_not_fit") else [],
                "what_to_prepare": p.get("preparation_needed", ""),
                "next_step": p.get("next_step", ""), "what_to_say": p.get("what_to_say"),
                "training_step": training, "fix": None}

    doors = {s: [door(pid, s) for pid in slots[s]] for s in slots}
    doors["not_yet"] = []
    if "idea_only" in tags and "sisfs" in NOT_FIRST:
        doors["not_yet"].append({"pathway_id": "sisfs", "reason": NOT_FIRST["sisfs"]["reason"]})

    prepare = []
    if "confidence_barrier" in tags:
        for a in ENGINE["confidence_gate"]["prepare_actions"][:3]:
            prepare.append({"action": a.replace("_", " "), "practice_script": PRACTICE.get(a, "")})

    first = (doors["start_here"] or doors["next"] or doors["later"] or [{}])[0].get("next_step", "")
    amt = profile["resources"]["money_available"]["amount"]
    snap = {"goal": profile["goal"].get("description") or profile["goal"]["primary"],
            "idea": profile["business_idea"].get("description") or "",
            "strengths": profile["skills_and_experience"].get("skills", []),
            "resources": f"Rs {int(amt):,} available" if amt is not None else "unknown",
            "main_barriers": profile.get("barriers", [])[:3],
            "readiness_level": profile["confidence_and_readiness"].get("readiness_level", "unknown"),
            "recommended_first_step": first}
    msg = ("Here are the doors that make sense for you right now. Start with the first one. "
           "You do not need to do everything at once.")
    if rejections:
        names = ", ".join(PATHWAYS[r["pathway_id"]]["pathway_name"] for r in rejections if r.get("pathway_id") in PATHWAYS)
        msg = (f"Thank you for telling us. {names} did not work out for now. That does not mean you cannot succeed: "
               "here are other doors to try, in order.")
    return {"snapshot": snap, "doors": doors, "prepare_me": prepare, "message_to_user": msg}


# ---------------------------------------------------------------- demo data

DEMO_TEXT = ("I want to start a small food business. I know how to cook well, but I only have 10,000 rupees. "
             "I live in a village. My family does not support me and I do not know anyone in business. "
             "I do not know where to start and I am scared to talk to people.")


def demo_profile():
    p = copy.deepcopy(PROFILE_DEFAULT)
    p["location"]["rural_or_urban"] = "rural"
    p["goal"] = {"primary": "start_business", "description": "Start a small food business"}
    p["business_idea"].update({"description": "Small food business", "sector": "food",
                               "product_or_service": "home-cooked food", "idea_stage": "just_an_idea"})
    p["skills_and_experience"]["skills"] = ["cooking"]
    p["skills_and_experience"]["business_experience"] = "none"
    p["resources"]["money_available"] = {"amount": 10000, "currency": "INR", "confidence": "exact"}
    p["knowledge"].update({"business_basics": "low", "funding_options": "low", "support_programmes": "low"})
    p["network_and_support"].update({"business_network": "none", "mentor_access": "none",
                                     "family_support": "discouraging"})
    p["barriers"] = ["do_not_know_where_to_start", "lack_of_money", "lack_of_network",
                     "family_discouragement", "low_confidence", "difficulty_approaching_people",
                     "fear_of_rejection"]
    p["support_needed"] = ["training", "mentor", "government_support", "confidence_building"]
    p["language"] = {"preferred_language": "English", "comfortable_languages": ["English"],
                     "needs_simple_language": True}
    return p
