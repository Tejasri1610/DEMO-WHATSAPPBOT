# app.py
import os, json, re, difflib
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI

# -------------------------------------------------
# Boot
# -------------------------------------------------
load_dotenv()
PORT = int(os.getenv("PORT", 5000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
PREFERRED_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")  # will fall back below

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase config in .env")
if not OPENAI_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_KEY)

app = Flask(_name_)
sessions = {}

# -------------------------------------------------
# Constants / dictionaries
# -------------------------------------------------
BLOODS = {"A+","A-","B+","B-","AB+","AB-","O+","O-"}
# Common misspellings / synonyms we’ll convert locally first
BLOOD_SYNONYMS = {
    "A POS": "A+", "A POSITIVE":"A+", "A PLUS":"A+",
    "A NEG": "A-", "A NEGATIVE":"A-",
    "B POS": "B+", "B POSITIVE":"B+",
    "B NEG": "B-", "B NEGATIVE":"B-",
    "AB POS":"AB+", "AB POSITIVE":"AB+",
    "AB NEG":"AB-", "AB NEGATIVE":"AB-",
    "O POS":"O+", "O POSITIVE":"O+","O PLUS":"O+",
    "O NEG":"O-", "O NEGATIVE":"O-",
    "APOS":"A+","ANEG":"A-","BPOS":"B+","BNEG":"B-","ABPOS":"AB+","ABNEG":"AB-","OPOS":"O+","ONEG":"O-",
}

# A compact list of Indian cities for fuzzy correction (add more if you like)
INDIAN_CITIES = [
    "Mumbai","Delhi","Bengaluru","Bangalore","Hyderabad","Ahmedabad","Chennai","Kolkata","Surat","Pune",
    "Jaipur","Lucknow","Kanpur","Nagpur","Indore","Thane","Bhopal","Visakhapatnam","Patna","Vadodara",
    "Ghaziabad","Ludhiana","Agra","Nashik","Faridabad","Meerut","Rajkot","Kalyan","Vasai","Srinagar",
    "Aurangabad","Dhanbad","Amritsar","Navi Mumbai","Allahabad","Prayagraj","Ranchi","Howrah","Coimbatore",
    "Jabalpur","Gwalior","Vijayawada","Jodhpur","Madurai","Raipur","Kota","Chandigarh","Guwahati",
    "Solapur","Hubli","Dharwad","Bareilly","Moradabad","Mysuru","Mysore","Gurugram","Gurgaon",
    "Aligarh","Jalandhar","Tiruchirappalli","Bhubaneswar","Salem","Warangal","Mira Bhayandar","Thiruvananthapuram",
    "Trivandrum","Bhiwandi","Saharanpur","Gorakhpur","Bikaner","Amravati","Noida","Jamshedpur","Bhilai",
    "Cuttack","Firozabad","Kochi","Ernakulam","Nellore","Bhavnagar","Dehradun","Durgapur","Asansol",
    "Rourkela","Nanded","Kolhapur","Ajmer","Akola","Gulbarga","Belgaum","Jamnagar","Ujjain","Loni",
    "Siliguri","Jhansi","Ulhasnagar","Jammu","Sangli","Mangalore","Erode","Tirunelveli","Muzaffarpur","Udaipur",
    "Rohtak","Karnal","Panipat","Rohini","Dwarka","Greater Noida"
]

# -------------------------------------------------
# Utilities
# -------------------------------------------------
def twiml_reply(text: str):
    r = MessagingResponse()
    r.message(text)
    xml = str(r)
    print("⬅ TwiML:", xml)
    return Response(xml, mimetype="application/xml")

def normalize_blood(txt: str):
    if not txt:
        return None
    t = txt.upper().strip().replace(" ", "")
    # try direct
    if t in BLOODS:
        return t
    # try with plus/minus symbols
    t2 = (txt.upper()
            .replace("POSITIVE", " POSITIVE")
            .replace("NEGATIVE", " NEGATIVE")
            .replace("+", " +")
            .replace("-", " -")
            .replace(" ", ""))
    if t2 in BLOODS:
        return t2
    # synonyms map
    t3 = (txt.upper().replace("-", " NEG").replace("+", " POS").replace("  ", " ").strip())
    t3 = re.sub(r"\s+", " ", t3)
    if t3 in BLOOD_SYNONYMS:
        return BLOOD_SYNONYMS[t3]
    # final hard clean
    t4 = re.sub(r"[^A-Z\+\-]", "", txt.upper())
    if t4 in BLOODS:
        return t4
    return None

def normalize_phone(value: str):
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    # prefer last 10 digits (India)
    if len(digits) >= 10:
        return digits[-10:]
    return digits if digits else None

def normalize_city(txt: str):
    if not txt:
        return None
    # direct exact
    s = txt.strip()
    # special mappings
    special = {"Bangalore": "Bengaluru", "Gurgaon": "Gurugram", "Trivandrum": "Thiruvananthapuram", "Prayagraj":"Prayagraj"}
    if s in special:
        return special[s]
    # fuzzy match
    match = difflib.get_close_matches(s, INDIAN_CITIES, n=1, cutoff=0.75)
    return match[0] if match else s.title()

def merge_known(data: dict, newbits: dict):
    out = dict(data or {})
    for k, v in (newbits or {}).items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        out[k] = v.strip() if isinstance(v, str) else v
    return out

# ...existing imports...

# -------------------------------------------------
# Utilities (update need_next)
# -------------------------------------------------
def need_next(role: str, data: dict):
    if role == "donor":
        if not data.get("full_name"): return "full_name"
        if not normalize_blood(data.get("blood_type")): return "blood_type"
        if not data.get("city"): return "city"
        return None
    if role == "request":
        if not data.get("full_name"): return "full_name"
        if not normalize_blood(data.get("blood_type")): return "blood_type"
        if not data.get("city"): return "city"
        return None
    return "role"

def prompt_for(field: str):
    prompts = {
        "role": "Please reply with 1 (Donor) or 2 (Require Blood).",
        "full_name": "📝 Please share your Full Name:",
        "blood_type": "🩸 Which Blood Group? (A+, A-, B+, B-, AB+, AB-, O+, O-)",
        "city": "🏙 Which City?",
    }
    return prompts.get(field, "Please provide the required detail.")

# -------------------------------------------------
# AI extraction (spelling tolerant)
# -------------------------------------------------
def ai_extract(user_msg: str, profile_name: str, session_state: dict, client, PREFERRED_MODEL):
    """
    Uses GPT to understand free-form messages and extract:
    intent (donor|request|reset|other), full_name, blood_type, city
    We instruct the model to fix typos, infer sensible values, and return strict JSON.
    """
    sys = (
        "You are Blood Help Bot. Extract structured data from a short WhatsApp message.\n"
        "Fix obvious typos (e.g., 'mumbaai' -> 'Mumbai', 'o pos' -> 'O+').\n"
        "Return STRICT JSON with keys: intent, full_name, blood_type, city.\n"
        "intent ∈ {donor, request, reset, other}.\n"
        "blood_type must be one of [A+,A-,B+,B-,AB+,AB-,O+,O-] if present; else null.\n"
        "If the user greets (hi/hello/start/menu/restart), use intent='reset'.\n"
        "Do not include any extra keys. No explanations."
    )

    state_hint = {
        "known": session_state.get("data", {}),
        "role": session_state.get("role"),
        "step": session_state.get("step"),
        "profile_name": profile_name,
    }
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": f"Message: {user_msg}\nState: {json.dumps(state_hint)}\nReturn JSON only."}
    ]

    tried = []
    last_err = None
    for model in [PREFERRED_MODEL, "gpt-4.1-mini"]:
        if model in tried:
            continue
        tried.append(model)
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=messages,
            )
            raw = resp.choices[0].message.content
            data = json.loads(raw)
            return data, model
        except Exception as e:
            last_err = e
            continue

    print("⚠ AI fallback error:", last_err)
    return (
        {"intent": "other", "full_name": None, "blood_type": None, "city": None},
        f"error:{last_err}"
    )

# -------------------------------------------------
# Routes
# -------------------------------------------------
# ...existing code...

@app.route("/webhook", methods=["POST"])
def webhook():
    body = (request.values.get("Body") or "").strip()
    from_number = request.values.get("From", "")
    profile_name = request.values.get("ProfileName") or "Friend"

    print(f"📩 From {from_number} ({profile_name}): {body}")

    # Load/create session
    session = sessions.get(from_number) or {"role": None, "step": "start", "data": {}}

    # --- Reset / Start ---
    if body.lower() in {"hi","hello","start","menu","restart"} or session.get("step") == "start":
        sessions[from_number] = {"role": None, "step": "choose_role", "data": {}}
        return twiml_reply(
            "👋 Hi, how may I help you?\n\n"
            "Please classify yourself:\n"
            "1️⃣ Donor\n"
            "2️⃣ Require Blood (Recipient Request)\n\n"
            "👉 Reply with 1 or 2 to continue."
        )

    # --- Choose role (supports numbers or words) ---
    if session.get("step") == "choose_role":
        b = body.strip().lower()
        if b in {"1", "donor"}:
            session["role"] = "donor"
            session["step"] = "collect"
            sessions[from_number] = session
            return twiml_reply("✅ Great! Registering you as a Donor.\nYou can reply naturally (e.g., 'A+ in Pune, my name is Ravi').")
        elif b in {"2", "request", "recipient"}:
            session["role"] = "request"
            session["step"] = "collect"
            sessions[from_number] = session
            return twiml_reply("🆘 Okay! Making a Blood Request.\nYou can reply naturally (e.g., 'Need AB- in Hyderabad').")
        else:
            return twiml_reply("⚠ Invalid choice.\nReply 1 for Donor or 2 for Request.")

    # --- Let AI parse the message, then fill missing fields ---
    ai, used_model = ai_extract(body, profile_name, session, client, PREFERRED_MODEL)
    print("🤖 AI model:", used_model)
    print("🤖 AI JSON:", ai)

    # Update role from AI if offered
    intent = (ai.get("intent") or "").lower()
    if intent == "reset":
        sessions[from_number] = {"role": None, "step": "choose_role", "data": {}}
        return twiml_reply(
            "🔄 Reset.\n"
            "1️⃣ Donor\n"
            "2️⃣ Require Blood\n\n"
            "👉 Reply with 1 or 2."
        )
    if intent in {"donor","request"} and not session.get("role"):
        session["role"] = "donor" if intent == "donor" else "request"

    # Merge fields and normalize
    data = merge_known(session.get("data", {}), {
        "full_name": ai.get("full_name"),
        "blood_type": ai.get("blood_type"),
        "city": ai.get("city"),
    })

    # Local strong normalization
    if data.get("blood_type"):
        bt = normalize_blood(data["blood_type"])
        if bt: data["blood_type"] = bt
        else:  data["blood_type"] = None

    if data.get("city"):
        data["city"] = normalize_city(data["city"])

    session["data"] = data
    session["step"] = "collect"

    # If role still unknown, ask directly
    if not session.get("role"):
        sessions[from_number] = session
        return twiml_reply(prompt_for("role"))

    # Ask only for what's missing
    missing = need_next(session["role"], data)
    if missing:
        sessions[from_number] = session
        return twiml_reply(prompt_for(missing))

    # --- All fields present → act ---
    if session["role"] == "donor":
        payload = {
            "full_name": data["full_name"],
            "blood_type": data["blood_type"],
            "phone": normalize_phone(from_number),
            "city": data["city"],
        }
        print("🗄 Insert donor:", payload)
        try:
            result = supabase.table("donors").insert(payload).execute()
            print("✅ Supabase donor insert:", result)
            msg = (
                "✅ Thanks! You’re registered as a donor.\n"
                f"Name: {payload['full_name']}\n"
                f"Group: {payload['blood_type']}\n"
                f"Phone: {payload['phone']}\n"
                f"City:  {payload['city']}"
            )
        except Exception as e:
            print("❌ Supabase donor insert error:", e)
            msg = "⚠ Saved your info locally but DB insert failed. Please try again later."
        sessions.pop(from_number, None)
        return twiml_reply(msg)

    if session["role"] == "request":
        # Prepare recipient payload for Supabase
        recipient_payload = {
            "full_name": data.get("full_name") or profile_name,
            "blood_type": data["blood_type"],
            "phone": normalize_phone(from_number),
            "city": data["city"],
        }
        print("🗄 Insert recipient:", recipient_payload)
        try:
            result = supabase.table("recipients").insert(recipient_payload).execute()
            print("✅ Supabase recipient insert:", result)
        except Exception as e:
            print("❌ Supabase recipient insert error:", e)

        # Search donors
        donors = []
        try:
            res = (
                supabase.table("donors")
                .select("full_name,phone,city")
                .eq("blood_type", recipient_payload["blood_type"])
                .ilike("city", f"%{recipient_payload['city']}%")
                .execute()
            )
            donors = res.data or []
            print(f"🔎 Found donors: {len(donors)}")
        except Exception as e:
            print("❌ Supabase donor search error:", e)

        if donors:
            lines = [f"✅ Donors for {recipient_payload['blood_type']} in {recipient_payload['city']}:", ""]
            for i, d in enumerate(donors[:10], 1):
                lines.append(f"{i}. {d['full_name']} — {normalize_phone(d['phone'])} ({d['city']})")
            lines.append("\n📞 Please contact donors directly.")
            reply = "\n".join(lines)
        else:
            reply = (
                f"❌ No donors found for {recipient_payload['blood_type']} in {recipient_payload['city']}.\n"
                "We’ll notify you if someone becomes available. Meanwhile you can place an emergency request here --> https://thala-connect-ai-28.lovable.app/"
            )

        sessions.pop(from_number, None)
        return twiml_reply(reply)

    # Fallback
    sessions[from_number] = session
    return twiml_reply("I didn’t catch that. Reply 1 for Donor or 2 for Require Blood.")

if _name_ == "_main_":
    app.run(host="0.0.0.0", port=PORT, debug=True)
