import os
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_TO = os.getenv("EMAIL_TO")

client = OpenAI(api_key=OPENAI_API_KEY)

# companies to track
companies = [
    {"name": "Google", "url": "https://www.google.com/about/careers/applications/jobs/results/?q=software"},
    {"name": "Meta", "url": "https://www.metacareers.com/jobs/?q=software"},
    {"name": "Microsoft", "url": "https://jobs.careers.microsoft.com/global/en/search?q=software"},
    {"name": "Adobe", "url": "https://careers.adobe.com/us/en/search-results?keywords=software"},
    {"name": "Amazon", "url": "https://www.amazon.jobs/en/search?base_query=software"}
]

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "jobradar@ai.com"
    msg["To"] = EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(os.getenv("GMAIL_USER"), os.getenv("GMAIL_PASS"))
        server.send_message(msg)

def analyze_job(text):
    prompt = f"""
    Check if this job fits a 3-4 year full stack/software/frontend engineer in USA.
    Return YES or NO with short reason.
    Job:
    {text[:2000]}
    """

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return res.choices[0].message.content

def run():
    results = []

    for c in companies:
        try:
            r = requests.get(c["url"], timeout=20)
            text = r.text[:4000]

            decision = analyze_job(text)

            if "YES" in decision:
                results.append(f"{c['name']} might have a matching role\nLink: {c['url']}\nAI: {decision}\n")

        except Exception as e:
            print(e)

    if results:
        body = "\n\n".join(results)
        send_email("🚨 Job Alert Matches Found", body)
    else:
        print("No strong matches now")

if __name__ == "__main__":
    run()
