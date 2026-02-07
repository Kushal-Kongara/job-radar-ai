import os
import re
import time
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_TO = os.getenv("EMAIL_TO")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- FILTERS ----------------

TARGET_KEYWORDS = [
    "software engineer",
    "full stack",
    "frontend",
    "front end",
    "web engineer",
    "product engineer",
    "ui engineer"
]

EXCLUDE = [
    "senior","sr","staff","principal","lead","manager","director","vp",
    "recruiter","intern","internship","embedded","hardware","firmware",
    "verification","architect","security","devops","sre","machine learning scientist"
]

def valid_title(title):
    t = (title or "").lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in TARGET_KEYWORDS)

def is_us(loc):
    l = (loc or "").lower()
    return (
        "united states" in l or
        "usa" in l or
        "us" in l or
        "remote" in l
    )

def posted_recent(date_iso):
    if not date_iso:
        return True
    try:
        dt = datetime.fromisoformat(date_iso.replace("Z","+00:00"))
        return datetime.utcnow() - dt < timedelta(hours=24)
    except:
        return True

# ---------------- EMAIL ----------------

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)

# ---------------- AI SCORE ----------------

def ai_score(job):
    prompt = f"""
Candidate: 3-4 years full stack/frontend/software engineer USA.

Score 0-100 how good fit.

Job:
{job['title']}
{job['desc'][:1200]}

Return number only.
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        txt = r.choices[0].message.content.strip()
        num = int(re.findall(r"\d+", txt)[0])
        return num
    except:
        return 50

# ---------------- GREENHOUSE ----------------

GREENHOUSE = [
"airbnb","stripe","coinbase","notion","figma","brex","databricks",
"discord","robinhood","dropbox","scaleai","pinterest","reddit",
"shopify","affirm","square","instacart","asana","twitch","coursera",
"rippling","flexport","gusto","intercom","doordash","lyft","uber",
"zillow","box","plaid","snowflake","twilio","okta","hashicorp",
"mongodb","unity","carta","loom","retool","webflow","zapier",
"calendly","grammarly","canva","chime","sofi","nuro","anduril",
"palantir","samsara","gong","clickup","monday","miro","airtable",
"perplexityai","runwayml","huggingface"
]

def greenhouse_jobs(board):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    jobs=[]
    try:
        r = requests.get(url,timeout=30)
        data=r.json()
        for j in data.get("jobs",[]):
            jobs.append({
                "company":board,
                "title":j.get("title"),
                "loc":(j.get("location") or {}).get("name"),
                "url":j.get("absolute_url"),
                "updated":j.get("updated_at"),
                "desc":(j.get("content") or "")[:1500]
            })
    except:
        pass
    return jobs

# ---------------- LEVER ----------------

LEVER = [
"netflix","tesla","snap","spotify","cloudflare","nvidia",
"roblox","epicgames","riotgames","scaleai","anthropic",
"benchling","duolingo","udemy","coursera"
]

def lever_jobs(handle):
    url=f"https://api.lever.co/v0/postings/{handle}?mode=json"
    jobs=[]
    try:
        r=requests.get(url,timeout=30)
        data=r.json()
        for j in data:
            jobs.append({
                "company":handle,
                "title":j.get("text"),
                "loc":(j.get("categories") or {}).get("location"),
                "url":j.get("hostedUrl"),
                "updated":datetime.utcfromtimestamp(j.get("createdAt")/1000).isoformat(),
                "desc":(j.get("descriptionPlain") or "")[:1500]
            })
    except:
        pass
    return jobs

# ---------------- MICROSOFT ----------------

def microsoft_jobs():
    url="https://gcsservices.careers.microsoft.com/search/api/v1/search"
    params={"l":"en_us","pg":1,"pgSz":20,"o":"Recent"}
    headers={"User-Agent":"Mozilla/5.0"}
    jobs=[]
    try:
        r=requests.get(url,headers=headers,params=params,timeout=30)
        data=r.json()
        for j in data.get("operationResult",{}).get("result",{}).get("jobs",[]):
            jobs.append({
                "company":"Microsoft",
                "title":j.get("title"),
                "loc":j.get("primaryLocation"),
                "url":"https://jobs.careers.microsoft.com/global/en/job/"+str(j.get("jobId")),
                "updated":datetime.utcnow().isoformat(),
                "desc":j.get("title")
            })
    except:
        pass
    return jobs

# ---------------- RUN ----------------

def run():
    all_jobs=[]

    for g in GREENHOUSE:
        all_jobs+=greenhouse_jobs(g)

    for l in LEVER:
        all_jobs+=lever_jobs(l)

    all_jobs+=microsoft_jobs()

    filtered=[]
    for j in all_jobs:
        if not valid_title(j["title"]): continue
        if not is_us(j["loc"]): continue
        if not posted_recent(j["updated"]): continue
        filtered.append(j)

    ranked=[]
    for j in filtered[:25]:
        s=ai_score(j)
        ranked.append((s,j))
        time.sleep(0.3)

    ranked.sort(reverse=True)

    if not ranked:
        send_email("⚠️ 0 jobs last 24hrs","No strong matches in last 24 hrs")
        return

    lines=[]
    for s,j in ranked[:12]:
        if s<60: continue
        lines.append(
f"{j['company']} — {j['title']}
Score:{s}/100
{j['loc']}
Apply:{j['url']}
--------------------------------"
        )

    if not lines:
        send_email("⚠️ nothing worth applying","Jobs exist but not strong match")
        return

    send_email("🚨 APPLY FAST — JOBS FOUND","\n".join(lines))

if __name__=="__main__":
    run()
