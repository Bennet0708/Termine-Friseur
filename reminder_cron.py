#!/usr/bin/env python3
"""
Cronjob-Script für Erinnerungen
Wird von GitHub Actions alle 15 Minuten aufgerufen
"""

import os
import sys
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import smtplib
from email.mime.text import MIMEText

from supabase import create_client
import resend

# Umgebungsvariablen laden
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY]):
    print("ERROR: Fehlende Umgebungsvariablen (SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY)")
    sys.exit(1)

resend.api_key = RESEND_API_KEY
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BERLIN_TZ = ZoneInfo("Europe/Berlin")
REMINDER_FENSTER_MINUTEN = 10
REMINDER_STATUS_DATEI = Path(__file__).parent / ".reminder_status.json"


def _lade_reminder_status():
    standard = {"24h": set(), "2h": set()}
    if not REMINDER_STATUS_DATEI.exists():
        return standard

    try:
        daten = json.loads(REMINDER_STATUS_DATEI.read_text(encoding="utf-8"))
        return {
            "24h": set(daten.get("24h", [])),
            "2h": set(daten.get("2h", [])),
        }
    except Exception as e:
        print(f"WARNING: Konnte Reminder-Status nicht laden: {e}")
        return standard


def _speichere_reminder_status(status):
    try:
        daten = {
            "24h": sorted(status.get("24h", set())),
            "2h": sorted(status.get("2h", set())),
        }
        REMINDER_STATUS_DATEI.write_text(
            json.dumps(daten, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"WARNING: Konnte Reminder-Status nicht speichern: {e}")


def _termin_start_datetime(termin):
    datum = termin.get("datum")
    uhrzeit = termin.get("uhrzeit")
    if not datum or not uhrzeit:
        return None

    try:
        return datetime.strptime(
            f"{datum} {uhrzeit}", "%d.%m.%Y %H:%M"
        ).replace(tzinfo=BERLIN_TZ)
    except ValueError:
        return None


def _reminder_key(termin, stufe):
    termin_id = termin.get("id")
    if termin_id is not None:
        return f"{termin_id}:{stufe}"

    return "|".join(
        [
            termin.get("email", ""),
            termin.get("datum", ""),
            termin.get("uhrzeit", ""),
            termin.get("name", ""),
            stufe,
        ]
    )


def sende_erinnerungs_email_an_kunde(termin, stufe_text):
    """Sendet Erinnerungs-Email an den Kunden"""
    email = termin.get("email")
    if not email:
        return False

    kunden_name = termin.get("name", "")
    service = termin.get("service", "deinen Termin")
    datum = termin.get("datum", "")
    uhrzeit = termin.get("uhrzeit", "")

    html_text = f"""
    <h2>Erinnerung: {stufe_text} vor deinem Friseurtermin</h2>
    <p>Hallo {kunden_name},</p>
    <p>dein Termin für {service} findet statt:</p>
    <p><strong>{datum} um {uhrzeit} Uhr</strong></p>
    <p>Wir freuen uns auf dich!</p>
    """

    try:
        resend.Emails.send(
            {
                "from": "noreply@beispiel.de",
                "to": email,
                "subject": f"Erinnerung: {stufe_text} vor deinem Termin",
                "html": html_text,
            }
        )
        print(f"✓ Email gesendet an {email} ({stufe_text})")
        return True
    except Exception as e:
        print(f"✗ Fehler beim Email-Versand an {email}: {e}")
        return False


def pruefe_und_sende_erinnerungen():
    """Hauptfunktion: Prüft alle Termine und sendet Erinnerungen"""
    try:
        # Laden der Termine von Supabase
        termine_result = supabase.table("termine").select("*").execute()
        termine = termine_result.data or []
        print(f"ℹ Loaded {len(termine)} Termine from Supabase")

        status = _lade_reminder_status()
        jetzt = datetime.now(BERLIN_TZ)
        geändert = False

        erinnerungen = [
            ("24h", 24 * 60 * 60, "24 Stunden"),
            ("2h", 2 * 60 * 60, "2 Stunden"),
        ]

        for termin in termine:
            if not termin.get("datum") or not termin.get("uhrzeit"):
                continue
            if not termin.get("email"):
                continue

            start_dt = _termin_start_datetime(termin)
            if not start_dt or start_dt <= jetzt:
                continue

            diff_sekunden = (start_dt - jetzt).total_seconds()

            for stufe, ziel_sekunden, stufe_text in erinnerungen:
                key = _reminder_key(termin, stufe)
                if key in status.get(stufe, set()):
                    continue

                fenster = REMINDER_FENSTER_MINUTEN * 60
                if ziel_sekunden - fenster <= diff_sekunden <= ziel_sekunden + fenster:
                    if sende_erinnerungs_email_an_kunde(termin, stufe_text):
                        status[stufe].add(key)
                        geändert = True

        if geändert:
            _speichere_reminder_status(status)
            print("✓ Reminder-Status aktualisiert")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    print(f"[{datetime.now(BERLIN_TZ).isoformat()}] Starte Reminder-Cronjob...")
    pruefe_und_sende_erinnerungen()
    print("✓ Reminder-Cronjob abgeschlossen")
