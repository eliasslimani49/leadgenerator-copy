import sys
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import anthropic

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_DB_ID = "3468c144ece78081ad5edd03993469a7"

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

VALID_BESOINS = [
    "Pas de réservation en ligne",
    "Pas de paiement en ligne",
    "Pas de site",
    "Réseaux sociaux inefficaces",
    "Perte de temps (gestion manuelle)",
    "Pas de clients",
    "Autre",
]


def search_places(keyword: str, city: str) -> list[dict]:
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    query = f"{keyword} {city}"
    results = []
    params = {"query": query, "key": GOOGLE_API_KEY, "language": "fr"}

    while len(results) < 20:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            print(f"[Google Places erreur] status={data.get('status')} — {data.get('error_message', '')}")
            break
        results.extend(data.get("results", []))
        next_token = data.get("next_page_token")
        if not next_token or len(results) >= 20:
            break
        time.sleep(2)
        params = {"pagetoken": next_token, "key": GOOGLE_API_KEY}

    return results[:20]


def get_place_details(place_id: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,website",
        "key": GOOGLE_API_KEY,
        "language": "fr",
    }
    resp = requests.get(url, params=params, timeout=10)
    return resp.json().get("result", {})


def scrape_website(url: str) -> dict:
    result = {"email": None, "facebook": None, "instagram": None, "text": ""}
    if not url:
        return result
    try:
        resp = requests.get(url, headers=HEADERS_BROWSER, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        text = soup.get_text(separator=" ", strip=True)
        result["text"] = text[:8000]

        # Email
        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        if emails:
            result["email"] = emails[0]

        # Social links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "facebook.com" in href and not result["facebook"]:
                result["facebook"] = href
            if "instagram.com" in href and not result["instagram"]:
                result["instagram"] = href

    except Exception:
        pass

    return result


def analyze_with_claude(name: str, site_text: str, has_website: bool) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    context = site_text if site_text else f"Aucun site web trouvé pour '{name}'."

    prompt = f"""Tu analyses le profil d'un professionnel nommé "{name}" pour évaluer s'il est pertinent comme prospect pour un outil de réservation en ligne payant (comme Planity, Doctolib, etc.).

Contenu du site web :
\"\"\"
{context}
\"\"\"

Réponds UNIQUEMENT avec un objet JSON valide (sans markdown, sans explication) avec ces champs :
- "activites" : string décrivant les activités/métiers du professionnel
- "besoins" : array avec les besoins détectés parmi ces valeurs exactes uniquement : "Pas de réservation en ligne", "Pas de paiement en ligne", "Pas de site", "Réseaux sociaux inefficaces", "Perte de temps (gestion manuelle)", "Pas de clients"
- "booking_software" : string (outil de réservation détecté sur le site, ou "Aucun")
- "score" : integer de 1 à 5 (pertinence pour un outil de réservation en ligne payant — 5 = très pertinent)
- "notes" : string résumé rapide du profil (1-2 phrases)"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}

    # Sanitize besoins
    data["besoins"] = [b for b in data.get("besoins", []) if b in VALID_BESOINS]
    data.setdefault("activites", "")
    data.setdefault("booking_software", "Aucun")
    data.setdefault("score", 1)
    data.setdefault("notes", "")

    return data


def create_notion_page(lead: dict) -> bool:
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    def url_prop(val):
        return {"url": val} if val else {"url": None}

    properties = {
        "Nom": {"title": [{"text": {"content": lead["nom"]}}]},
        "Statut": {"select": {"name": "Prospect"}},
        "Source": {"select": {"name": "autres"}},
        "Activités/métiers": {"rich_text": [{"text": {"content": lead["activites"][:2000]}}]},
        "Booking software": {"rich_text": [{"text": {"content": lead["booking_software"][:2000]}}]},
        "Notes": {"rich_text": [{"text": {"content": lead["notes"][:2000]}}]},
        "Besoin / Problème": {"multi_select": [{"name": b} for b in lead["besoins"]]},
    }

    if lead.get("telephone"):
        properties["Téléphone"] = {"phone_number": lead["telephone"]}
    if lead.get("email"):
        properties["E-mail"] = {"email": lead["email"]}
    if lead.get("site_web"):
        properties["Site web"] = url_prop(lead["site_web"])
    if lead.get("facebook"):
        properties["Facebook"] = url_prop(lead["facebook"])
    if lead.get("instagram"):
        properties["Instagram"] = url_prop(lead["instagram"])

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code not in (200, 201):
        print(f"    [Notion error {resp.status_code}] {resp.text[:200]}")
        return False
    return True


def main():
    if len(sys.argv) < 3:
        print("Usage: python vitryne_leads.py \"mot-clé\" \"ville\"")
        sys.exit(1)

    keyword = sys.argv[1]
    city = sys.argv[2]

    print(f"\n🔍 Recherche : '{keyword}' à {city}\n")

    places = search_places(keyword, city)
    print(f"{len(places)} résultats Google Places trouvés.\n")

    total = 0
    inserted = 0

    for place in places:
        total += 1
        place_id = place.get("place_id", "")
        details = get_place_details(place_id)

        nom = details.get("name") or place.get("name", "Inconnu")
        telephone = details.get("formatted_phone_number", "")
        site_web = details.get("website", "")

        print(f"[{total}/{len(places)}] {nom}")

        scraped = scrape_website(site_web)
        analysis = analyze_with_claude(nom, scraped["text"], bool(site_web))

        score = analysis.get("score", 1)
        print(f"    Score : {score}/5 — {analysis.get('notes', '')}")

        if score >= 3:
            lead = {
                "nom": nom,
                "telephone": telephone,
                "email": scraped["email"],
                "site_web": site_web,
                "facebook": scraped["facebook"],
                "instagram": scraped["instagram"],
                "activites": analysis["activites"],
                "besoins": analysis["besoins"],
                "booking_software": analysis["booking_software"],
                "notes": analysis["notes"],
            }
            ok = create_notion_page(lead)
            if ok:
                inserted += 1
                print(f"    ✓ Inséré dans Notion")
            else:
                print(f"    ✗ Échec insertion Notion")
        else:
            print(f"    → Ignoré (score < 3)")

    print(f"\n{'='*50}")
    print(f"Résumé : {total} prospects analysés, {inserted} insérés dans Notion.")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
