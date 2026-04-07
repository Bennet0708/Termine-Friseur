import re
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from supabase import create_client

try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_CALENDAR_AVAILABLE = True
except ImportError:
    GOOGLE_CALENDAR_AVAILABLE = False
    st.warning("Google Calendar API nicht installiert. Nutze: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"],
)


def laden():
    try:
        termine_result = supabase.table("termine").select("*").execute()
        anfragen_result = supabase.table("anfragen").select("*").execute()

        termine = termine_result.data or []
        anfragen = anfragen_result.data or []

        belegte_slots = set()
        for termin in termine:
            datum = termin.get("datum")
            uhrzeit = termin.get("uhrzeit")
            dauer = termin.get("termindauer")
            if datum and uhrzeit and dauer:
                for slot in slots_fuer_termin(datum, uhrzeit, dauer):
                    belegte_slots.add(slot)

        return termine + anfragen, belegte_slots
    except Exception as exc:
        st.error(f"Supabase-Fehler beim Laden: {exc}")
        return [], set()


def speichern(termin_dict, ist_anfrage=False):
    if ist_anfrage:
        return supabase.table("anfragen").insert(
            {
                "name": termin_dict.get("name"),
                "telefon": termin_dict.get("telefon"),
                "service": termin_dict.get("service"),
                "email": termin_dict.get("email"),
                "wunsch": termin_dict.get("wunsch"),
            }
        ).execute()

    return supabase.table("termine").insert(
        {
            "name": termin_dict.get("name"),
            "telefon": termin_dict.get("telefon"),
            "datum": termin_dict.get("datum"),
            "uhrzeit": termin_dict.get("uhrzeit"),
            "service": termin_dict.get("service"),
            "termindauer": termin_dict.get("termindauer"),
            "email": termin_dict.get("email"),
        }
    ).execute()


def benachrichtigung_senden(termin):
    if termin.get("modus") == "standard":
        inhalt = f"""
Neuer Termin gebucht!

Name: {termin['name']}
Telefon: {termin['telefon']}
Service: {termin['service']}
Datum: {termin['datum']}
Uhrzeit: {termin['uhrzeit']}
Dauer: {termin['termindauer']} Minuten
        """
    else:
        inhalt = f"""
Neue manuelle Anfrage!

Name: {termin['name']}
Telefon: {termin['telefon']}
Service: {termin['service']}
Wunsch: {termin['wunsch']}
E-Mail: {termin['email']}
        """

    msg = MIMEText(inhalt, "plain", "utf-8")
    msg["Subject"] = "Neuer Termin - Terminbot"
    msg["From"] = st.secrets["EMAIL_ABSENDER"]
    msg["To"] = st.secrets["EMAIL_EMPFAENGER"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(
            st.secrets["EMAIL_ABSENDER"],
            st.secrets["EMAIL_PASSWORT"],
        )
        server.send_message(msg)


def bestaetigung_senden(termin, email_kunde):
    if termin.get("modus") == "standard":
        inhalt = f"""
Hallo {termin['name']}!

Dein Termin wurde erfolgreich gebucht.

Service: {termin['service']}
Datum: {termin['datum']}
Uhrzeit: {termin['uhrzeit']}
Dauer: {termin['termindauer']} Minuten

Bei Fragen melde dich direkt beim Salon.
        """
    else:
        inhalt = f"""
Hallo {termin['name']}!

Deine Anfrage wurde erfolgreich übermittelt.

Service: {termin['service']}
Dein Wunsch: {termin['wunsch']}

Wir melden uns bald bei dir.
        """

    msg = MIMEText(inhalt, "plain", "utf-8")
    msg["Subject"] = "Deine Buchungsbestätigung"
    msg["From"] = st.secrets["EMAIL_ABSENDER"]
    msg["To"] = email_kunde

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(
            st.secrets["EMAIL_ABSENDER"],
            st.secrets["EMAIL_PASSWORT"],
        )
        server.send_message(msg)


def slots_fuer_termin(datum, start_uhrzeit, dauer):
    teile = start_uhrzeit.split(":")
    stunden = int(teile[0])
    minuten = int(teile[1])

    slots_liste = []
    vergangene = 0

    while vergangene < dauer:
        slot = f"{datum} {str(stunden).zfill(2)}:{str(minuten).zfill(2)}"
        slots_liste.append(slot)

        minuten += 15
        vergangene += 15

        if minuten == 60:
            minuten = 0
            stunden += 1

    return slots_liste


def email_ok(email):
    muster = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return re.match(muster, email) is not None

BLOCKIERTE_TAGE = [
   "24.12.2026",
   "25.12.2026",
   "26.12.2026",
   "31.12.2026",
   "01.01.2027"
]

def ist_tag_blockiert(datum_str):
    try:
        datum = datetime.strptime(datum_str, "%d.%m.%Y")
        if datum.weekday() == 6:
            return True
        if datum_str in BLOCKIERTE_TAGE:
            return True
        return False
    except:
        return False
    
def freie_termine(datum, dauer, belegte_slots):
    if ist_tag_blockiert(datum):
        return []
    
    jetzt = datetime.now(timezone(timedelta(hours=2)))
    heute_str = jetzt.strftime("%d.%m.%Y")

    freie_startzeiten = []
    stunden = 8
    minuten = 30

    while True:
        startzeit = f"{stunden:02d}:{minuten:02d}"
        slot_dt = datetime.strptime(f"{datum} {startzeit}", "%d.%m.%Y %H:%M").replace(tzinfo=timezone(timedelta(hours=2)))

        if datum == heute_str and slot_dt < jetzt + timedelta(minutes=15):
            minuten += 15
            if minuten == 60:
                minuten = 0
                stunden += 1
            continue

        start_minuten = stunden * 60 + minuten
        ende_minuten = start_minuten + dauer
        if ende_minuten > 18 * 60:
            break

        slots = slots_fuer_termin(datum, startzeit, dauer)
        if all(slot not in belegte_slots for slot in slots):
            freie_startzeiten.append(startzeit)

        minuten += 15
        if minuten == 60:
            minuten = 0
            stunden += 1

    return freie_startzeiten


def buchungen_pro_tag(termine, email, datum):
    return sum(
        1
        for termin in termine
        if termin.get("email") == email and termin.get("datum") == datum
    )


def sende_emails_sicher(termin, email_kunde):
    try:
        benachrichtigung_senden(termin)
        bestaetigung_senden(termin, email_kunde)
    except Exception as exc:
        st.warning(f"Buchung gespeichert, aber E-Mail-Versand fehlgeschlagen: {exc}")


def termin_zu_google_calendar(termin):
    if not GOOGLE_CALENDAR_AVAILABLE or termin.get("modus") != "standard":
        return
    
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        
        service = build("calendar", "v3", credentials=credentials)
        
        datum = termin.get("datum")
        uhrzeit = termin.get("uhrzeit")
        dauer = int(termin.get("termindauer", 30))
        
        datum_obj = datetime.strptime(f"{datum} {uhrzeit}", "%d.%m.%Y %H:%M")
        ende_obj = datum_obj + timedelta(minutes=dauer)
        
        event = {
            "summary": f"Termin: {termin.get('name')} - {termin.get('service')}",
            "description": f"Telefon: {termin.get('telefon')}\nE-Mail: {termin.get('email')}",
            "start": {
                "dateTime": datum_obj.isoformat(),
                "timeZone": "Europe/Berlin",
            },
            "end": {
                "dateTime": ende_obj.isoformat(),
                "timeZone": "Europe/Berlin",
            },
        }
        kalender_id = st.secrets["GOOGLE_CALENDAR_ID"]
        result = service.events().insert(calendarId=kalender_id, body=event).execute()
        st.success(f"✅ Termin in Google Calendar eingetragen!")
        
    except Exception as exc:
        st.warning(f"Google Calendar Fehler: {exc}")

def reset_formular():
    st.session_state.telefon = ""
    st.session_state.email = ""
    st.session_state.wunsch = ""
    st.session_state.modus = None
    st.session_state.dauer = 0
    st.session_state.gewaehlte_uhrzeit = None
    st.session_state.gewaehltes_datum = None



DAUER_MIN = {
    "Haare - Schneiden": 30,
    "Haare - Färben": 60,
    "Haare - Stylen": 30,
    "Haare & Bart": 45,
    "Haare - Beratung": 45,
    "Haare - Extrawunsch": 45,
    "Bart - Trimmen": 15,
    "Bart - Kontur": 15,
    "Bart - Beratung": 30,
    "Bart - Extrawunsch": 15,
}

PREISE = {
    "Haare - Schneiden": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Haare - Färben": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Haare - Stylen": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Haare & Bart": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Haare - Beratung": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Haare - Extrawunsch": {"Kurzhaar": "ab XX €", "Langhaar": "ab YY €"},
    "Bart - Trimmen": "ab XX €",
    "Bart - Kontur": "ab XX €",
    "Bart - Beratung": "ab XX €",
    "Bart - Extrawunsch": "ab XX €",
}

KATEGORIEN = {
    "Haare": [
        "Haare - Schneiden",
        "Haare - Färben",
        "Haare - Stylen",
        "Haare & Bart",
        "Haare - Beratung",
        "Haare - Extrawunsch",
    ],
    "Bart": [
        "Bart - Trimmen",
        "Bart - Kontur",
        "Bart - Beratung",
        "Bart - Extrawunsch",
    ],
    "Anderes": [
        "Anderes - Event/Hochzeit",
        "Anderes - Beratung",
        "Anderes - Extrawunsch",
    ],
}

st.set_page_config(page_title="Termin buchen", page_icon="💈", layout="wide")
st.title("Online Termin buchen")
st.caption("Schnell und unkompliziert Termin auswählen")
st.markdown("---")
st.info("Öffnungszeiten: Mo-Fr 8:30-18:00 Uhr")

termine, belegte_slots = laden()

if "step" not in st.session_state:
    st.session_state.step = 1
if "name" not in st.session_state:
    st.session_state.name = ""
if "kategorie" not in st.session_state:
    st.session_state.kategorie = ""
if "service" not in st.session_state:
    st.session_state.service = ""
if "haartyp" not in st.session_state:
    st.session_state.haartyp = "Kurzhaar"
if "modus" not in st.session_state:
    st.session_state.modus = None
if "telefon" not in st.session_state:
    st.session_state.telefon = ""
if "email" not in st.session_state:
    st.session_state.email = ""
if "wunsch" not in st.session_state:
    st.session_state.wunsch = ""
if "dauer" not in st.session_state:
    st.session_state.dauer = 0
if "letzte_buchung" not in st.session_state:
    st.session_state.letzte_buchung = None
if "gebucht" not in st.session_state:
    st.session_state.gebucht = False
if "admin_versuche" not in st.session_state:
    st.session_state.admin_versuche = 0
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "gewaehlte_uhrzeit" not in st.session_state:
    st.session_state.gewaehlte_uhrzeit = None
if "gewaehltes_datum" not in st.session_state:
    st.session_state.gewaehltes_datum = None
if "slot_datum_widget" not in st.session_state:
    st.session_state.slot_datum_widget = date.today()

if st.session_state.step == 1:
    st.write(f"**Schritt {st.session_state.step} von 3**")
    name = st.text_input("Name", value=st.session_state.name, placeholder="Vorname Nachname")

    col1, col2 = st.columns(2)
    with col2:
        if st.button("Weiter"):
            if not name.strip():
                st.error("Bitte Name eingeben.")
            else:
                st.session_state.name = name.strip()
                st.session_state.step = 2
                st.rerun()

    with st.sidebar:
        if st.button("Admin-Bereich"):
            st.session_state.admin_versuche = 0
            st.session_state.step = 99
            st.rerun()

elif st.session_state.step == 2:
    st.write(f"**Schritt {st.session_state.step} von 3**")
    st.write(f"Hallo **{st.session_state.name}**")

    kategorien_liste = list(KATEGORIEN.keys())
    kat_index = kategorien_liste.index(st.session_state.kategorie) if st.session_state.kategorie in kategorien_liste else 0
    kategorie = st.selectbox("Kategorie", kategorien_liste, index=kat_index)

    services = KATEGORIEN[kategorie]
    srv_index = services.index(st.session_state.service) if st.session_state.service in services else 0
    service = st.selectbox("Service", services, index=srv_index)

    # Haartyp-Auswahl für Services, die mit "Haare" beginnen
    haartyp = None
    if service.startswith("Haare"):
        haartyp = st.radio("Haartyp", ["Kurzhaar", "Langhaar"], index=0 if st.session_state.haartyp == "Kurzhaar" else 1)

    # Dauer und Preis anzeigen
    if service in DAUER_MIN:
        basis_dauer = DAUER_MIN[service]
        zusatz = 15 if haartyp == "Langhaar" else 0
        st.caption(f"Dauer: {basis_dauer + zusatz} Minuten")
        
        if service in PREISE:
            preis = PREISE[service]
            if isinstance(preis, dict):
                preis = preis.get(haartyp, "ab XX €")
            st.caption(f"Preis: {preis}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Zurück"):
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("Weiter"):
            if service != st.session_state.service:
                st.session_state.kategorie = kategorie
                st.session_state.service = service
            if haartyp:
                st.session_state.haartyp = haartyp
            st.session_state.gebucht = False
            st.session_state.step = 3
            st.rerun()

elif st.session_state.step == 3:
    service = st.session_state.service
    st.write(f"**Schritt {st.session_state.step} von 3**")
    st.write(f"**Service:** {service}")

    if service.startswith("Anderes -"):
        modus = "manual"
    elif "Beratung" in service or "Extrawunsch" in service:
        art = st.radio("Wie soll das laufen?", ["Termin vor Ort", "Rückruf / E-Mail"])
        modus = "manual" if art == "Rückruf / E-Mail" else "standard"
    else:
        modus = "standard"

    if modus == "manual":
        st.info("Dieser Service läuft als manuelle Anfrage ohne festen Zeitslot.")

        email = st.text_input("E-Mail")
        telefon = st.text_input("Telefon")
        wunsch = st.text_area("Wunsch (1 Satz reicht)")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Zurück"):
                reset_formular()
                st.session_state.step = 2
                st.rerun()

        with col2:
            if st.button("Anfrage speichern"):
                if not email_ok(email):
                    st.error("Bitte korrekte E-Mail eingeben.")
                elif not telefon.strip():
                    st.error("Bitte Telefonnummer angeben.")
                elif not wunsch.strip():
                    st.error("Bitte Wunsch eingeben.")
                else:
                    st.session_state.telefon = telefon.strip()
                    st.session_state.email = email.strip()
                    st.session_state.wunsch = wunsch.strip()
                    st.session_state.modus = "manual"
                    st.session_state.step = 4
                    st.rerun()

    else:
        basis_dauer = DAUER_MIN[service]
        zusatz = 15 if st.session_state.haartyp == "Langhaar" else 0
        dauer = basis_dauer + zusatz

        telefon = st.text_input("Telefonnummer")
        email = st.text_input("E-Mail (für Bestätigung)")
        datum = st.date_input("Datum auswählen", key="slot_datum_widget", min_value=date.today())
        datum_str = datum.strftime("%d.%m.%Y")

        if ist_tag_blockiert(datum_str):
            st.error("❌ An diesem Tag sind keine Termine möglich (Sonntag oder Urlaubstag).")
            st.stop()

        if datum_str != st.session_state.gewaehltes_datum:
            st.session_state.gewaehlte_uhrzeit = None
            st.session_state.gewaehltes_datum = datum_str

        freie = freie_termine(datum_str, dauer, belegte_slots)

        if freie:
            st.subheader("Freie Uhrzeiten")
            st.write("Tippe auf eine Uhrzeit, um den Termin auszuwählen.")
            cols = st.columns(4)
            for index, slot in enumerate(freie):
                with cols[index % 4]:
                    if st.button(slot, key=f"slot_{datum_str}_{slot}", use_container_width=True):
                        st.session_state.gewaehlte_uhrzeit = slot
                        st.session_state.gewaehltes_datum = datum_str
        else:
            st.warning("An diesem Tag sind keine Termine frei.")

        if st.session_state.gewaehlte_uhrzeit and st.session_state.gewaehltes_datum == datum_str:
            st.success(
                f"Gewählter Termin: {st.session_state.gewaehltes_datum} um {st.session_state.gewaehlte_uhrzeit}"
            )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Zurück"):
                reset_formular()
                st.session_state.step = 2
                st.session_state.gebucht = False
                st.rerun()

        with col2:
            if st.button("Termin buchen"):
                if not telefon.strip():
                    st.error("Bitte Telefonnummer angeben.")
                elif not email_ok(email):
                    st.error("Bitte korrekte E-Mail eingeben.")
                elif not st.session_state.gewaehltes_datum or not st.session_state.gewaehlte_uhrzeit:
                    st.error("Bitte erst eine Uhrzeit auswählen.")
                else:
                    st.session_state.telefon = telefon.strip()
                    st.session_state.email = email.strip()
                    st.session_state.dauer = dauer
                    st.session_state.modus = "standard"
                    st.session_state.step = 4
                    st.rerun()
elif st.session_state.step == 4:
    st.write("**Schritt 4 von 5**")
    st.subheader("Bestätigung der Daten")

    modus = st.session_state.modus
    service = st.session_state.service
    if modus == "standard":
        datum_final = st.session_state.gewaehltes_datum
        uhrzeit_final = st.session_state.gewaehlte_uhrzeit
        telefon_val = st.session_state.telefon
        email_val = st.session_state.email
        buchung = {
            "modus": "standard",
            "name": st.session_state.name,
            "telefon": telefon_val,
            "service": service,
            "datum": datum_final,
            "uhrzeit": uhrzeit_final,
            "termindauer": st.session_state.dauer,
            "email": email_val,
        }
    else:
        telefon_val = st.session_state.telefon
        email_val = st.session_state.email
        wunsch_val = st.session_state.wunsch
        buchung = {
            "modus": "manual",
            "name": st.session_state.name,
            "telefon": telefon_val,
            "service": service,
            "email": email_val,
            "wunsch": wunsch_val,
        }

    if buchung.get("modus") == "standard":
        st.write("**Name:**", buchung.get("name", "-"))
        st.write("**Telefon:**", buchung.get("telefon", "-"))
        st.write("**Service:**", buchung.get("service", "-"))
        st.write("**Datum:**", buchung.get("datum", "-"))
        st.write("**Uhrzeit:**", buchung.get("uhrzeit", "-"))
        st.write("**Dauer:**", f"{buchung.get('termindauer', '-')} Minuten")
        st.write("**E-Mail:**", buchung.get("email", "-"))
    else:
        st.write("**Name:**", buchung.get("name", "-"))
        st.write("**Telefon:**", buchung.get("telefon", "-"))
        st.write("**Service:**", buchung.get("service", "-"))
        st.write("**Wunsch:**", buchung.get("wunsch", "-"))
        st.write("**E-Mail:**", buchung.get("email", "-"))

    st.write("**Alles korrekt?**")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Ja, buchen"):
            # Hier die Validierungen und Speicherung
            if buchung.get("modus") == "standard":
                termine_aktuell, belegte_slots_aktuell = laden()
                slots_liste = slots_fuer_termin(datum_final, uhrzeit_final, st.session_state.dauer)

                if any(slot in belegte_slots_aktuell for slot in slots_liste):
                    st.error("Dieser Termin wurde gerade schon vergeben. Bitte wähle eine andere Uhrzeit.")
                    st.stop()

                count = buchungen_pro_tag(termine_aktuell, email_val, datum_final)
                if count >= 4:
                    st.error("Diese E-Mail-Adresse hat bereits mehrere Termine an diesem Tag gebucht.")
                    st.stop()

            st.session_state.letzte_buchung = buchung

            try:
                speichern(st.session_state.letzte_buchung, ist_anfrage=(buchung.get("modus") == "manual"))
            except Exception as exc:
                st.error(f"Fehler beim Speichern: {exc}")
                st.stop()

            sende_emails_sicher(st.session_state.letzte_buchung, buchung.get("email"))
            termin_zu_google_calendar(st.session_state.letzte_buchung)
            st.session_state.step = 5
            st.rerun()

    with col2:
        if st.button("Nein, zurück"):
            reset_formular()
            st.session_state.step = 3
            st.rerun()
elif st.session_state.step == 5:
    st.success("Termin gespeichert")
    buchung = st.session_state.letzte_buchung or {}

    if buchung.get("modus") == "standard":
        st.write("**Name:**", buchung.get("name", "-"))
        st.write("**Telefon:**", buchung.get("telefon", "-"))
        st.write("**Service:**", buchung.get("service", "-"))
        st.write("**Datum:**", buchung.get("datum", "-"))
        st.write("**Uhrzeit:**", buchung.get("uhrzeit", "-"))
        st.write("**Dauer:**", f"{buchung.get('termindauer', '-')} Minuten")
        st.write("**E-Mail:**", buchung.get("email", "-"))
    elif buchung.get("modus") == "manual":
        st.write("**Name:**", buchung.get("name", "-"))
        st.write("**Telefon:**", buchung.get("telefon", "-"))
        st.write("**Service:**", buchung.get("service", "-"))
        st.write("**Wunsch:**", buchung.get("wunsch", "-"))
        st.write("**E-Mail:**", buchung.get("email", "-"))

    st.info("Wenn der Mailversand funktioniert hat, wurde eine Bestätigung verschickt.")

    if st.button("Noch einen Termin buchen"):
        st.session_state.step = 1
        st.session_state.gebucht = False
        st.session_state.letzte_buchung = None
        reset_formular()
        st.rerun()

    st.stop()

elif st.session_state.step == 6:
    if not st.session_state.is_admin:
        st.session_state.step = 99
        st.rerun()

    st.title("Admin Dashboard")
    if st.button("Zurück"):
        st.session_state.step = 1
        st.rerun()

    termine, belegte_slots = laden()


    if not termine:
        st.info("Noch keine Termine gespeichert.")
    else:
        standard = []
        manual = []

        for termin in termine:
            if termin.get("datum") and termin.get("uhrzeit"):
                termin_copy = termin.copy()
                termin_copy["Sortierung"] = datetime.strptime(
                    f"{termin['datum']} {termin['uhrzeit']}",
                    "%d.%m.%Y %H:%M",
                )
                standard.append(termin_copy)
            else:
                manual.append(termin)

        standard.sort(key=lambda eintrag: eintrag["Sortierung"])
        for termin in standard:
            del termin["Sortierung"]

        gewuenschte_spalten_standard = ['name', 'telefon', 'datum', 'uhrzeit', 'service', 'termindauer', 'email']
        gewuenschte_spalten_manual = ['name', 'telefon', 'service', 'wunsch', 'email']

        st.subheader("Terminbuchungen")
        if standard:
            cols = st.columns([2, 2, 2, 2, 2, 2, 2, 1])
            cols[0].write("**Name**")
            cols[1].write("**Telefon**")
            cols[2].write("**Datum**")
            cols[3].write("**Uhrzeit**")
            cols[4].write("**Service**")
            cols[5].write("**Dauer**")
            cols[6].write("**E-Mail**")
            cols[7].write("**Löschen**")
            for termin in standard:
                cols[0].write(termin.get('name', '-'))
                cols[1].write(termin.get('telefon', '-'))
                cols[2].write(termin.get('datum', '-'))
                cols[3].write(termin.get('uhrzeit', '-'))
                cols[4].write(termin.get('service', '-'))
                cols[5].write(f"{termin.get('termindauer', '-')} Min")
                cols[6].write(termin.get('email', '-'))
                if cols[7].button("🗑️", key=f"del_std_{termin['id']}"):
                    supabase.table("termine").delete().eq("id", termin["id"]).execute()
                    st.success("Termin gelöscht")
                    st.rerun()
        else:
            st.info("Keine Terminbuchungen vorhanden.")

        st.subheader("Manuelle Anfragen")
        if manual:
            cols = st.columns([2, 2, 2, 2, 2, 1])
            cols[0].write("**Name**")
            cols[1].write("**Telefon**")
            cols[2].write("**Service**")
            cols[3].write("**Wunsch**")
            cols[4].write("**E-Mail**")
            cols[5].write("**Löschen**")
            for termin in manual:
                cols[0].write(termin.get('name', '-'))
                cols[1].write(termin.get('telefon', '-'))
                cols[2].write(termin.get('service', '-'))
                cols[3].write(termin.get('wunsch', '-'))
                cols[4].write(termin.get('email', '-'))
                if cols[5].button("🗑️", key=f"del_man_{termin['id']}"):
                    supabase.table("anfragen").delete().eq("id", termin["id"]).execute()
                    st.success("Anfrage gelöscht")
                    st.rerun()
        else:
            st.info("Keine manuellen Anfragen vorhanden.")

        # Entferne die alte selectbox und button, da jetzt inline
        # optionen = ...
        # auswahl = ...
        # if st.button("Löschen"):

elif st.session_state.step == 99:
    st.title("Admin Login")
    passwort = st.text_input("Passwort eingeben", type="password")

    if st.button("Anmelden"):
        if passwort == st.secrets["ADMIN_PASSWORT"]:
            st.session_state.is_admin = True
            st.session_state.step = 6
            st.rerun()
        else:
            st.session_state.admin_versuche += 1
            rest = 3 - st.session_state.admin_versuche
            st.error(f"Falsches Passwort. {rest} Versuche übrig.")

            if st.session_state.admin_versuche >= 3:
                st.warning("Zu viele Fehlversuche.")
                st.session_state.step = 1
                st.rerun()

    if st.button("Zurück"):
        st.session_state.step = 1
        st.rerun()
