"""Pathway Navigator: Flask backend.

Run:  python app.py      then open http://localhost:5000
"""
import base64
import hashlib
import json
import os
import time

from flask import Flask, jsonify, request, send_from_directory

import core
import llm

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024   # 8 MB uploads

# Repeat requests (same text / same profile) reuse the earlier model answer. Cleared on restart.
CACHE = {}


def _ck(*parts):
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _timed(label, fn):
    t = time.time()
    try:
        return fn()
    finally:
        print(f"[timing] {label}: {time.time() - t:.1f}s (model {llm.MODEL})", flush=True)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/health")
def health():
    return jsonify({"model_configured": llm.available(), "model": llm.MODEL, "base": llm.BASE})


def _profile_response(profile, engine, note=None, text=None, prior=None, ui_language=None):
    profile = core.normalise_profile(profile)
    notes = []
    if text is not None:
        notes = core.apply_evidence_guard(profile, text, prior)
        prior_lang = ((prior or {}).get("language") or {}).get("preferred_language")
        profile["language"]["preferred_language"] = core.choose_language(
            text, ui_language, prior_lang, profile["language"].get("preferred_language"))
    tags, missing = core.derive_tags(profile)
    out = {"profile": profile, "tags": tags, "missing": missing,
           "questions": core.localized_questions(profile, missing), "engine": engine}
    if note:
        out["note"] = note
    if notes:
        out["guard"] = notes
    return out


@app.post("/api/extract")
def extract():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    ui_language = data.get("ui_language") or "English"
    prior = data.get("prior_profile")
    if not text:
        return jsonify({"error": "Please say or type something first."}), 400
    if not llm.available():
        return jsonify(_profile_response(
            core.demo_profile(), "demo",
            "Demo mode: no MODEL_API_KEY is set, so the example profile is used."))
    try:
        key = _ck("extract", llm.MODEL, text, ui_language, prior)
        raw = CACHE.get(key)
        if raw is None:
            raw = _timed("extract", lambda: llm.chat_json(core.build_extract_messages(text, ui_language, prior)))
            CACHE[key] = raw
        return jsonify(_profile_response(raw, "model", text=text, prior=prior, ui_language=ui_language))
    except llm.LLMError as e:
        return jsonify({"error": f"The model could not read that: {e}"}), 502


@app.post("/api/route")
def route():
    data = request.get_json(silent=True) or {}
    profile = core.normalise_profile(data.get("profile"))
    rejections = data.get("rejections") or []
    profile["journey_state"]["rejections"] = rejections
    tags, missing = core.derive_tags(profile)
    if missing:
        return jsonify({"status": "needs_clarification",
                        "questions": core.localized_questions(profile, missing), "tags": tags})
    for r in rejections:
        if not (r.get("reason") or "").strip():
            return jsonify({"status": "needs_clarification",
                            "questions": [core.question_text(
                                "rejection_reason", profile["language"].get("preferred_language"))],
                            "tags": tags})
    rules = core.fired_rules(tags)
    engine, route_obj, warnings = "model", None, []
    if llm.available():
        try:
            key = _ck("route", llm.MODEL, profile, rejections)
            route_obj = CACHE.get(key)
            if route_obj is None:
                route_obj = _timed("route", lambda: llm.chat_json(
                    core.build_route_messages(profile, tags, rules, rejections)))
                CACHE[key] = route_obj
        except llm.LLMError as e:
            warnings.append(f"model unavailable, rules engine used instead: {e}")
    if route_obj is None:
        engine = "rules_fallback"
        route_obj = core.fallback_route(profile, tags, rules, rejections)
    result, w = core.finalize(route_obj, rejections)
    if engine == "model" and not any(result["doors"][s] for s in ("start_here", "next", "later")):
        # the model produced nothing usable: fall back rather than show an empty screen
        engine = "rules_fallback"
        warnings.append("model returned no valid doors, rules engine used instead")
        result, w2 = core.finalize(core.fallback_route(profile, tags, rules, rejections), rejections)
        w += w2
    return jsonify({"status": "ok", "engine": engine, "tags": tags,
                    "fired_rules": [r["when"] for r in rules],
                    "result": result, "warnings": warnings + w})


@app.post("/api/photo")
def photo():
    f = request.files.get("image")
    language = request.form.get("language") or "English"
    if not f:
        return jsonify({"error": "No image received."}), 400
    if not llm.available():
        return jsonify({"error": "Photo explanation needs MODEL_API_KEY (no demo mode)."}), 503
    mime = f.mimetype if (f.mimetype or "").startswith("image/") else "image/jpeg"
    b64 = base64.b64encode(f.read()).decode()
    ids = list(core.PATHWAYS)
    msgs = [{"role": "system", "content": core.PHOTO_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": json.dumps({"LANGUAGE": language, "PATHWAY_IDS": ids})},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]
    try:
        out = llm.chat_json(msgs)
    except llm.LLMError as e:
        return jsonify({"error": f"The model could not read that image: {e}"}), 502
    related = []
    for pid in out.get("related_pathway_ids") or []:
        if pid in core.PATHWAYS:
            related.append({"pathway_id": pid, "name": core.PATHWAYS[pid]["pathway_name"],
                            "official_source": core._source(pid)})
    out["related_pathways"] = related
    out.pop("related_pathway_ids", None)
    out["disclaimer"] = core.DISCLAIMER
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Model configured: {llm.available()}  ({llm.MODEL} @ {llm.BASE})")
    app.run(host="0.0.0.0", port=port, debug=False)
