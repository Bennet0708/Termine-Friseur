import re
import secrets
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from supabase import create_client
import resend

resend.api_key = st.secrets["RESEND_API_KEY"]

supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"],
)

# Trage die zweite Kalender-ID später in st.secrets als GOOGLE_CALENDAR_ID_ANDRE ein.
KALENDER_IDS = {
    "Elias": "GOOGLE_CALENDAR_ID",
    "Andre": "GOOGLE_CALENDAR_ID_ANDRE",
}

BERLIN_TZ = ZoneInfo("Europe/Berlin")
REMINDER_STATUS_DATEI = Path(__file__).parent / ".reminder_status.json"
REMINDER_FENSTER_MINUTEN = 10
URLAUBSTAGE_DATEI = Path(__file__).parent / ".urlaubstage.json"


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
    except Exception:
        return standard


def _speichere_reminder_status(status):
    daten = {
        "24h": sorted(status.get("24h", set())),
        "2h": sorted(status.get("2h", set())),
    }
    REMINDER_STATUS_DATEI.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    email = termin.get("email")
    if not email:
        return False

    kunden_name = termin.get("name", "")
    service = termin.get("service", "deinen Termin")
    datum = termin.get("datum", "")
    uhrzeit = termin.get("uhrzeit", "")

    text = (
        f"Hallo {kunden_name},\n\n"
        f"kurze Erinnerung: Dein Termin ({service}) ist in {stufe_text}.\n"
        f"Datum: {datum}\n"
        f"Uhrzeit: {uhrzeit}\n\n"
        "Wir freuen uns auf dich!"
    )

    try:
        resend.Emails.send(
            {
                "from": "Dein Friseur <onboarding@resend.dev>",
                "to": email,
                "subject": f"Erinnerung: Termin in {stufe_text}",
                "text": text,
            }
        )
        return True
    except Exception:
        return False


def pruefe_und_sende_erinnerungen(termine):
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


def laden():
    try:
        termine_result = supabase.table("termine").select("*").execute()
        anfragen_result = supabase.table("anfragen").select("*").execute()

        termine = termine_result.data or []
        anfragen = anfragen_result.data or []

        belegte_slots = {
            "Elias": set(),
            "Andre": set(),
        }
        for termin in termine:
            datum = termin.get("datum")
            uhrzeit = termin.get("uhrzeit")
            dauer = termin.get("termindauer")
            friseur = termin.get("friseur", "Elias")
            if datum and uhrzeit and dauer:
                for slot in slots_fuer_termin(datum, uhrzeit, dauer):
                    if friseur not in belegte_slots:
                        belegte_slots[friseur] = set()
                    belegte_slots[friseur].add(slot)

        return termine + anfragen, belegte_slots
    except Exception as exc:
        st.error(f"Supabase-Fehler beim Laden: {exc}")
        return [], {"Elias": set(), "Andre": set()}


def speichern(termin_dict, ist_anfrage=False):
    if ist_anfrage:
        anfrage_payload = {
            "name": termin_dict.get("name"),
            "telefon": termin_dict.get("telefon"),
            "service": termin_dict.get("service"),
            "email": termin_dict.get("email"),
            "wunsch": termin_dict.get("wunsch"),
            "friseur": termin_dict.get("friseur"),
        }
        try:
            return supabase.table("anfragen").insert(anfrage_payload).execute()
        except Exception as exc:
            # Fallback for schemas that do not yet have a friseur column.
            if "friseur" in str(exc).lower() and "column" in str(exc).lower():
                anfrage_payload.pop("friseur", None)
                return supabase.table("anfragen").insert(anfrage_payload).execute()
            raise

    termin_payload = {
        "name": termin_dict.get("name"),
        "telefon": termin_dict.get("telefon"),
        "datum": termin_dict.get("datum"),
        "uhrzeit": termin_dict.get("uhrzeit"),
        "service": termin_dict.get("service"),
        "termindauer": termin_dict.get("termindauer"),
        "email": termin_dict.get("email"),
        "friseur": termin_dict.get("friseur"),
        "stornocode": termin_dict.get("stornocode"),
        "google_event_id": termin_dict.get("google_event_id"),
    }
    try:
        return supabase.table("termine").insert(termin_payload).execute()
    except Exception as exc:
        # Fallback for schemas that do not yet have friseur/stornocode/google_event_id columns.
        err = str(exc).lower()
        if "column" in err:
            retry_payload = dict(termin_payload)
            if "friseur" in err:
                retry_payload.pop("friseur", None)
            if "stornocode" in err:
                retry_payload.pop("stornocode", None)
            if "google_event_id" in err:
                retry_payload.pop("google_event_id", None)
            if retry_payload != termin_payload:
                return supabase.table("termine").insert(retry_payload).execute()
        raise


def generiere_stornocode():
    return f"{secrets.randbelow(1000000):06d}"


def filtere_storno_termine(termine, name_suche, email_suche, telefon_suche, datum_suche):
    jetzt = datetime.now(BERLIN_TZ)
    name_norm = name_suche.strip().lower()
    email_norm = email_suche.strip().lower()
    telefon_norm = telefon_suche.strip().lower()
    datum_norm = datum_suche.strip()

    treffer = []
    for termin in termine:
        if not termin.get("datum") or not termin.get("uhrzeit"):
            continue

        start_dt = _termin_start_datetime(termin)
        if not start_dt or start_dt <= jetzt:
            continue

        name_val = str(termin.get("name", "")).lower()
        email_val = str(termin.get("email", "")).lower()
        telefon_val = str(termin.get("telefon", "")).lower()
        datum_val = str(termin.get("datum", ""))

        if name_norm and name_norm not in name_val:
            continue
        if email_norm and email_norm not in email_val:
            continue
        if telefon_norm and telefon_norm not in telefon_val:
            continue
        if datum_norm and datum_norm != datum_val:
            continue

        treffer.append(termin)

    treffer.sort(
        key=lambda t: datetime.strptime(
            f"{t.get('datum')} {t.get('uhrzeit')}", "%d.%m.%Y %H:%M"
        )
    )
    return treffer


def render_storno_sidebar(termine):
    menu_titel = f"☰ {t('menu_title')}"
    if st.button(menu_titel, key="toggle_main_menu"):
        st.session_state.show_main_menu = not st.session_state.get("show_main_menu", False)

    if st.session_state.get("show_main_menu", False):
        st.markdown(f"### {t('menu_title')}")

        if st.button(t("menu_admin"), key="sidebar_admin_button"):
            st.session_state.admin_versuche = 0
            st.session_state.step = 99
            st.rerun()

        st.markdown("---")
        st.markdown(t("cancel_title"))
        st.caption(t("cancel_caption"))

        name_suche = st.text_input(t("name"), key="storno_name")
        email_suche = st.text_input(t("email"), key="storno_email")
        telefon_suche = st.text_input(t("phone"), key="storno_telefon")
        datum_suche = st.text_input(t("date_optional"), key="storno_datum")

        if st.button(t("search_appt"), key="storno_suchen"):
            st.session_state.storno_treffer = filtere_storno_termine(
                termine,
                name_suche,
                email_suche,
                telefon_suche,
                datum_suche,
            )
            st.session_state.storno_auswahl_id = None

        treffer = st.session_state.get("storno_treffer", [])
        if treffer:
            st.write(t("found_appts"))
            for termin in treffer:
                label = (
                    f"{termin.get('datum', '-')} {termin.get('uhrzeit', '-')} | "
                    f"{termin.get('service', '-')}"
                )
                if st.button(label, key=f"storno_pick_{termin.get('id')}"):
                    st.session_state.storno_auswahl_id = termin.get("id")

            ausgewaehlt = next(
                (t for t in treffer if t.get("id") == st.session_state.get("storno_auswahl_id")),
                None,
            )
            if ausgewaehlt:
                st.info(
                    t(
                        "selected",
                        datum=ausgewaehlt.get("datum"),
                        uhrzeit=ausgewaehlt.get("uhrzeit"),
                        service=ausgewaehlt.get("service"),
                    )
                )
                code = st.text_input(
                    t("cancel_code_prompt"),
                    max_chars=6,
                    key="storno_code_input",
                )
                if st.button(t("confirm_cancel"), key="storno_delete"):
                    if not re.match(r"^\d{6}$", code or ""):
                        st.error(t("invalid_code"))
                        return

                    gespeicherter_code = str(ausgewaehlt.get("stornocode") or "")
                    if code != gespeicherter_code:
                        st.error(t("wrong_code"))
                        return

                    try:
                        if not loesche_termin_aus_google_calendar(ausgewaehlt):
                            st.error(t("calendar_delete_required"))
                            return

                        supabase.table("termine").delete().eq("id", ausgewaehlt["id"]).execute()
                        st.success(t("cancel_ok"))
                        st.session_state.storno_treffer = []
                        st.session_state.storno_auswahl_id = None
                        st.rerun()
                    except Exception as exc:
                        st.error(t("cancel_fail", error=exc))
        elif st.session_state.get("storno_treffer") == [] and st.session_state.get("storno_name", "").strip():
            st.warning(t("no_results"))


def sende_buchungs_email(termin, ist_anfrage=False):
    """
    Sendet eine Benachrichtigung über eine neue Buchung oder Anfrage.
    """
    try:
        buchungs_link = "https://online-buchung.com"
        if ist_anfrage:
            betreff = "🔔 Neue manuelle Anfrage!"
            inhalt_text = f"""
            Neue manuelle Anfrage!
            
            Name: {termin['name']}
            Telefon: {termin['telefon']}
            Service: {termin['service']}
            Wunsch: {termin['wunsch']}
            E-Mail: {termin['email']}
            """
        else:
            betreff = "✅ Neuer Termin gebucht!"
            inhalt_text = f"""
            Neuer Termin gebucht!
            
            Name: {termin['name']}
            Telefon: {termin['telefon']}
            Service: {termin['service']}
            Datum: {termin['datum']}
            Uhrzeit: {termin['uhrzeit']}
            Dauer: {termin['termindauer']} Minuten
            """

        resend.Emails.send({
            "from": "Termin-System <onboarding@resend.dev>", 
            "to": st.secrets["EMAIL_EMPFAENGER"],
            "subject": betreff,
            "text": inhalt_text
        })

        if termin.get('email'):
            if ist_anfrage:
                kunden_text = (
                    f"Hallo {termin['name']},\n"
                    "deine Anfrage ist bei uns eingegangen. "
                    "Wir melden uns schnellstmöglich bei dir.\n\n"
                    f"Online-Buchung: {buchungs_link}"
                )
                kunden_subject = "Anfragebestätigung"
            else:
                friseur_name = termin.get("friseur", "Elias")
                stornocode = termin.get("stornocode", "-")
                kunden_text = (
                    f"Sehr geehrte/r {termin['name']},\n\n"
                    "vielen Dank für Ihre Terminbuchung. Nachfolgend finden Sie Ihre Buchungsdaten:\n\n"
                    f"Name: {termin.get('name', '-')}\n"
                    f"Service: {termin.get('service', '-')}\n"
                    f"Friseur: {friseur_name}\n"
                    f"Datum: {termin.get('datum', '-')}\n"
                    f"Uhrzeit: {termin.get('uhrzeit', '-')} Uhr\n"
                    f"Dauer: {termin.get('termindauer', '-')} Minuten\n"
                    f"Telefon: {termin.get('telefon', '-')}\n"
                    f"E-Mail: {termin.get('email', '-')}\n\n"
                    f"Stornocode: {stornocode}\n"
                    "Bitte diesen Code aufbewahren. Er wird für eine Stornierung benötigt.\n\n"
                    f"Online-Buchung: {buchungs_link}\n\n"
                    "Unsere Adresse:\n"
                    "Warfer Landstrasse 13\n\n"
                    "Wir freuen uns auf Ihren Besuch.\n\n"
                    "Ihr Friseur Team"
                )
                kunden_subject = "Terminbestätigung"

            resend.Emails.send({
                "from": "Dein Friseur <onboarding@resend.dev>",
                "to": termin['email'],
                "subject": kunden_subject,
                "text": kunden_text
            })
            
        return True
    except Exception as e:
        st.error(t("email_send_error", error=e))
        return False

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


def _lade_urlaubstage():
    standard = {"Elias": set(), "Andre": set()}
    if not URLAUBSTAGE_DATEI.exists():
        return standard

    try:
        daten = json.loads(URLAUBSTAGE_DATEI.read_text(encoding="utf-8"))
        return {
            "Elias": set(daten.get("Elias", [])),
            "Andre": set(daten.get("Andre", [])),
        }
    except Exception:
        return standard


def _speichere_urlaubstage(urlaubstage):
    daten = {
        "Elias": sorted(urlaubstage.get("Elias", set())),
        "Andre": sorted(urlaubstage.get("Andre", set())),
    }
    URLAUBSTAGE_DATEI.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


URLAUBSTAGE = _lade_urlaubstage()


def blockierte_tage_gesamt(friseur=None):
    basis = set(BLOCKIERTE_TAGE)
    if friseur:
        basis |= URLAUBSTAGE.get(friseur, set())
    else:
        for tage in URLAUBSTAGE.values():
            basis |= set(tage)
    return basis

def ist_tag_blockiert(datum_str, friseur=None):
    try:
        datum = datetime.strptime(datum_str, "%d.%m.%Y")
        if datum.weekday() == 6:
            return True
        if datum_str in blockierte_tage_gesamt(friseur):
            return True
        return False
    except:
        return False
    
def freie_termine(datum, dauer, belegte_slots, friseur=None):
    if ist_tag_blockiert(datum, friseur):
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


def _google_calendar_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        st.warning(t("google_api_missing"))
        return None

    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        return build("calendar", "v3", credentials=credentials)
    except Exception as exc:
        st.warning(t("google_auth_error", error=exc))
        return None


def _kalender_id_fuer_friseur(friseur):
    kalender_secret_key = KALENDER_IDS.get(friseur or "Elias", "GOOGLE_CALENDAR_ID")

    if kalender_secret_key == "GOOGLE_CALENDAR_ID_ANDRE" and kalender_secret_key not in st.secrets:
        st.warning(t("google_calendar_id_missing"))
        kalender_secret_key = "GOOGLE_CALENDAR_ID"

    return st.secrets[kalender_secret_key]


def termin_zu_google_calendar(termin):
    service = _google_calendar_service()
    if service is None:
        return None

    try:
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
        kalender_id = _kalender_id_fuer_friseur(termin.get("friseur", "Elias"))
        created_event = service.events().insert(calendarId=kalender_id, body=event).execute()
        return created_event.get("id")
        
    except Exception as exc:
        st.warning(t("google_error", error=exc))
        return None


def loesche_termin_aus_google_calendar(termin):
    service = _google_calendar_service()
    if service is None:
        return False

    try:
        kalender_id = _kalender_id_fuer_friseur(termin.get("friseur", "Elias"))
    except Exception as exc:
        st.warning(t("calendar_id_resolve_fail", error=exc))
        return False

    event_id = termin.get("google_event_id")
    if event_id:
        try:
            service.events().delete(calendarId=kalender_id, eventId=event_id).execute()
            return True
        except Exception as exc:
            if "404" in str(exc):
                return True
            st.warning(t("google_delete_eventid_error", error=exc))

    start_dt = _termin_start_datetime(termin)
    if not start_dt:
        st.warning(t("calendar_time_invalid"))
        return False

    dauer = int(termin.get("termindauer", 30) or 30)
    time_min = (start_dt - timedelta(minutes=5)).isoformat()
    time_max = (start_dt + timedelta(minutes=dauer + 5)).isoformat()
    soll_summary = f"Termin: {termin.get('name')} - {termin.get('service')}"

    try:
        events = service.events().list(
            calendarId=kalender_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            maxResults=25,
        ).execute().get("items", [])

        for event in events:
            summary = event.get("summary", "")
            if summary == soll_summary or termin.get("name", "") in summary:
                service.events().delete(
                    calendarId=kalender_id,
                    eventId=event.get("id"),
                ).execute()
                return True

        st.warning(t("calendar_no_match"))
        return True
    except Exception as exc:
        st.warning(t("google_delete_error", error=exc))
        return False

def reset_formular():
    st.session_state.telefon = ""
    st.session_state.email = ""
    st.session_state.wunsch = ""
    st.session_state.modus = None
    st.session_state.dauer = 0
    st.session_state.gewaehlte_uhrzeit = None
    st.session_state.gewaehltes_datum = None
    st.session_state.email_und_kalender_erledigt = False



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


def preis_fuer_service(service, haartyp):
    preis = PREISE.get(service)
    if preis is None:
        return None
    if isinstance(preis, dict):
        return preis.get(haartyp, "ab XX €")
    return preis

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

SPRACHEN = {
    "Deutsch": "de",
    "English": "en",
    "Turkce": "tr",
}

SPRACH_LABELS = {
    "de": "Deutsch",
    "en": "English",
    "tr": "Turkce",
}

I18N = {
    "de": {
        "title": "Online Termin buchen",
        "caption": "Schnell und unkompliziert Termin auswählen",
        "opening": "Öffnungszeiten: Mo-Fr 8:30-18:00 Uhr",
        "cancel_hint_corner": "<- Hier stornieren",
        "lang_label": "Sprache",
        "menu_title": "Menü",
        "menu_admin": "Admin",
        "cancel_title": "### Termin stornieren",
        "cancel_caption": "Geben Sie Ihre Termindaten ein und wählen Sie den Termin aus.",
        "name": "Name",
        "email": "E-Mail",
        "phone": "Telefon",
        "date_optional": "Datum (optional, TT.MM.JJJJ)",
        "search_appt": "Termin suchen",
        "found_appts": "Gefundene Termine",
        "selected": "Ausgewählt: {datum} um {uhrzeit} ({service})",
        "cancel_code_prompt": "Um die Stornierung zu bestätigen, geben Sie bitte den 6-stelligen Stornocode ein, den Sie in der Bestätigungsmail erhalten haben.",
        "confirm_cancel": "Termin verbindlich stornieren",
        "invalid_code": "Bitte einen gueltigen 6-stelligen Code eingeben.",
        "wrong_code": "Stornocode falsch. Bitte pruefen Sie die Bestaetigungsmail.",
        "cancel_ok": "Termin wurde erfolgreich storniert.",
        "cancel_fail": "Fehler bei der Stornierung: {error}",
        "no_results": "Keine passenden Termine gefunden.",
        "step": "Schritt {current} von 5",
        "hairdresser_question": "Bei welchem Friseur möchtest du einen Termin?",
        "next": "Weiter",
        "back": "Zurück",
        "enter_name": "Bitte Name eingeben.",
        "hello": "Hallo {name}",
        "category": "Kategorie",
        "service": "Service",
        "hair_type": "Haartyp",
        "duration": "Dauer: {minutes} Minuten",
        "price": "Preis: {price}",
        "slot_header": "Freie Uhrzeiten",
        "slot_hint": "Doppelklick auf Uhrzeit wählt diese aus. Bitte danach auf 'Termin buchen' klicken.",
        "book_appt": "Termin buchen",
        "book_more": "Noch einen Termin buchen",
        "confirm_data": "Bestätigung der Daten",
        "all_correct": "Alles korrekt?",
        "yes_book": "Ja, buchen",
        "no_back": "Nein, zurück",
        "saved": "Glückwunsch! Termin gespeichert",
        "mail_sent": "Bestätigung wurde versendet!",
        "placeholder_name": "Vorname Nachname",
        "flow_mode_question": "Wie soll das laufen?",
        "flow_on_site": "Termin vor Ort",
        "flow_callback": "Rückruf / E-Mail",
        "manual_info": "Dieser Service läuft als manuelle Anfrage ohne festen Zeitslot.",
        "wish": "Wunsch (1 Satz reicht)",
        "save_request": "Anfrage speichern",
        "invalid_email": "Bitte korrekte E-Mail eingeben.",
        "enter_phone": "Bitte Telefonnummer angeben.",
        "enter_wish": "Bitte Wunsch eingeben.",
        "phone_number": "Telefonnummer",
        "email_confirm": "E-Mail (für Bestätigung)",
        "select_date": "Datum auswählen",
        "blocked_day": "An diesem Tag sind keine Termine möglich (Sonntag oder Urlaubstag).",
        "chosen_slot": "Gewählter Termin: {datum} um {uhrzeit}",
        "no_slots": "An diesem Tag sind keine Termine frei.",
        "pick_slot_first": "Bitte erst eine Uhrzeit auswählen.",
        "field_name": "Name",
        "field_service": "Service",
        "field_duration": "Dauer",
        "field_date": "Datum",
        "field_time": "Uhrzeit",
        "field_price": "Preis",
        "field_phone": "Telefon",
        "field_email": "E-Mail",
        "field_wish": "Wunsch",
        "minutes_short": "{minutes} Minuten",
        "slot_taken": "Dieser Termin wurde gerade schon vergeben. Bitte wähle eine andere Uhrzeit.",
        "daily_limit": "Diese E-Mail-Adresse hat bereits mehrere Termine an diesem Tag gebucht.",
        "save_error": "Fehler beim Speichern: {error}",
        "register_spinner": "Termin wird im System registriert...",
        "calendar_delete_required": "Termin konnte nicht im Google Kalender gelöscht werden. Bitte später erneut versuchen.",
        "calendar_missing_column": "Spalte google_event_id fehlt in Supabase. Kalender-Löschung nutzt dann Fallback-Suche.",
        "calendar_event_save_fail": "Konnte google_event_id nicht speichern: {error}",
        "cancel_code_show": "Falls Sie stornieren möchten, verwenden Sie diesen Code:",
        "mail_status_info": "Wenn der Mailversand funktioniert hat, wurde eine Bestätigung verschickt.",
        "email_send_error": "Fehler beim E-Mail-Versand: {error}",
        "mail_request_subject": "Anfragebestätigung",
        "mail_booking_subject": "Terminbestätigung",
        "mail_request_body": "Hallo {name},\nIhre Anfrage ist bei uns eingegangen. Wir melden uns schnellstmöglich bei Ihnen.",
        "mail_booking_body": "Sehr geehrte/r {name},\n\nvielen Dank für Ihre Terminbuchung. Nachfolgend finden Sie Ihre Buchungsdaten:\n\nName: {name}\nService: {service}\nFriseur: {friseur}\nDatum: {datum}\nUhrzeit: {uhrzeit} Uhr\nDauer: {dauer} Minuten\nTelefon: {telefon}\nE-Mail: {email}\n\nStornocode: {stornocode}\nBitte diesen Code aufbewahren. Er wird für eine Stornierung benötigt.\n\nAdresse:\nWarfer Landstrasse 13\n\nWir freuen uns auf Ihren Besuch.\n\nIhr Friseur Team",
        "google_api_missing": "Google Calendar API nicht installiert. Nutze: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client",
        "google_auth_error": "Google Calendar Auth-Fehler: {error}",
        "google_calendar_id_missing": "GOOGLE_CALENDAR_ID_ANDRE fehlt in den Secrets. Termin wird vorerst im Elias-Kalender gespeichert.",
        "google_error": "Google Calendar Fehler: {error}",
        "calendar_id_resolve_fail": "Kalender-ID konnte nicht bestimmt werden: {error}",
        "google_delete_eventid_error": "Google Calendar Löschfehler (Event-ID): {error}",
        "calendar_time_invalid": "Terminzeit für Google-Kalender-Löschung ungültig.",
        "calendar_no_match": "Kein passender Google-Kalender-Eintrag gefunden. Der Termin wird trotzdem in der Datenbank gelöscht.",
        "google_delete_error": "Google Calendar Löschfehler: {error}",
    },
    "en": {
        "title": "Book Appointment Online",
        "caption": "Choose your appointment quickly and easily",
        "opening": "Opening hours: Mon-Fri 8:30-18:00",
        "cancel_hint_corner": "<- Cancel here",
        "lang_label": "Language",
        "menu_title": "Menu",
        "menu_admin": "Admin",
        "cancel_title": "### Cancel appointment",
        "cancel_caption": "Enter your appointment details and select your booking.",
        "name": "Name",
        "email": "Email",
        "phone": "Phone",
        "date_optional": "Date (optional, DD.MM.YYYY)",
        "search_appt": "Search appointment",
        "found_appts": "Found appointments",
        "selected": "Selected: {datum} at {uhrzeit} ({service})",
        "cancel_code_prompt": "To confirm the cancellation, please enter the 6-digit cancellation code from your confirmation email.",
        "confirm_cancel": "Cancel appointment",
        "invalid_code": "Please enter a valid 6-digit code.",
        "wrong_code": "Wrong cancellation code. Please check your confirmation email.",
        "cancel_ok": "Appointment canceled successfully.",
        "cancel_fail": "Cancellation error: {error}",
        "no_results": "No matching appointments found.",
        "step": "Step {current} of 5",
        "hairdresser_question": "Which hairdresser would you like?",
        "next": "Next",
        "back": "Back",
        "enter_name": "Please enter your name.",
        "hello": "Hello {name}",
        "category": "Category",
        "service": "Service",
        "hair_type": "Hair type",
        "duration": "Duration: {minutes} minutes",
        "price": "Price: {price}",
        "slot_header": "Available time slots",
        "slot_hint": "Double-click a time slot to select it, then click 'Book appointment'.",
        "book_appt": "Book appointment",
        "book_more": "Book another appointment",
        "confirm_data": "Confirm your details",
        "all_correct": "Is everything correct?",
        "yes_book": "Yes, book now",
        "no_back": "No, go back",
        "saved": "Success! Appointment saved",
        "mail_sent": "Confirmation email sent!",
        "placeholder_name": "First and last name",
        "flow_mode_question": "How should this be handled?",
        "flow_on_site": "On-site appointment",
        "flow_callback": "Callback / Email",
        "manual_info": "This service is handled as a manual request without a fixed time slot.",
        "wish": "Request (one short sentence)",
        "save_request": "Save request",
        "invalid_email": "Please enter a valid email address.",
        "enter_phone": "Please enter a phone number.",
        "enter_wish": "Please enter your request.",
        "phone_number": "Phone number",
        "email_confirm": "Email (for confirmation)",
        "select_date": "Select date",
        "blocked_day": "No appointments are possible on this day (Sunday or holiday).",
        "chosen_slot": "Selected appointment: {datum} at {uhrzeit}",
        "no_slots": "No free appointments on this day.",
        "pick_slot_first": "Please select a time slot first.",
        "field_name": "Name",
        "field_service": "Service",
        "field_duration": "Duration",
        "field_date": "Date",
        "field_time": "Time",
        "field_price": "Price",
        "field_phone": "Phone",
        "field_email": "Email",
        "field_wish": "Request",
        "minutes_short": "{minutes} minutes",
        "slot_taken": "This appointment has just been booked by someone else. Please choose a different time.",
        "daily_limit": "This email address has already booked multiple appointments on this day.",
        "save_error": "Error while saving: {error}",
        "register_spinner": "Registering appointment...",
        "calendar_delete_required": "Appointment could not be removed from Google Calendar. Please try again later.",
        "calendar_missing_column": "Column google_event_id is missing in Supabase. Calendar deletion will use fallback search.",
        "calendar_event_save_fail": "Could not save google_event_id: {error}",
        "cancel_code_show": "If you want to cancel, please use this code:",
        "mail_status_info": "If email delivery works, a confirmation was sent.",
        "email_send_error": "Email sending error: {error}",
        "mail_request_subject": "Request confirmation",
        "mail_booking_subject": "Appointment confirmation",
        "mail_request_body": "Hello {name},\nwe received your request. We will get back to you as soon as possible.",
        "mail_booking_body": "Dear {name},\n\nthank you for your appointment booking. Here are your booking details:\n\nName: {name}\nService: {service}\nHairdresser: {friseur}\nDate: {datum}\nTime: {uhrzeit}\nDuration: {dauer} minutes\nPhone: {telefon}\nEmail: {email}\n\nCancellation code: {stornocode}\nPlease keep this code. It is required for cancellation.\n\nAddress:\nWarfer Landstrasse 13\n\nWe look forward to your visit.\n\nYour Friseur Team",
        "google_api_missing": "Google Calendar API not installed. Use: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client",
        "google_auth_error": "Google Calendar auth error: {error}",
        "google_calendar_id_missing": "GOOGLE_CALENDAR_ID_ANDRE is missing in secrets. Appointment will be saved in Elias calendar for now.",
        "google_error": "Google Calendar error: {error}",
        "calendar_id_resolve_fail": "Could not determine calendar ID: {error}",
        "google_delete_eventid_error": "Google Calendar deletion error (event ID): {error}",
        "calendar_time_invalid": "Appointment time is invalid for Google Calendar deletion.",
        "calendar_no_match": "No matching Google Calendar event found. The appointment will still be deleted from the database.",
        "google_delete_error": "Google Calendar deletion error: {error}",
    },
    "tr": {
        "title": "Online Randevu Al",
        "caption": "Randevunuzu hizli ve kolay secin",
        "opening": "Calisma saatleri: Pzt-Cuma 8:30-18:00",
        "cancel_hint_corner": "<- Buradan iptal edin",
        "lang_label": "Dil",
        "menu_title": "Menu",
        "menu_admin": "Admin",
        "cancel_title": "### Randevu iptali",
        "cancel_caption": "Randevu bilgilerinizi girin ve kaydinizi secin.",
        "name": "Ad Soyad",
        "email": "E-posta",
        "phone": "Telefon",
        "date_optional": "Tarih (istege bagli, GG.AA.YYYY)",
        "search_appt": "Randevu ara",
        "found_appts": "Bulunan randevular",
        "selected": "Secilen: {datum} saat {uhrzeit} ({service})",
        "cancel_code_prompt": "Iptali onaylamak icin lutfen onay e-postanizdaki 6 haneli iptal kodunu girin.",
        "confirm_cancel": "Randevuyu iptal et",
        "invalid_code": "Lutfen gecerli 6 haneli kod girin.",
        "wrong_code": "Iptal kodu yanlis. Lutfen onay e-postanizi kontrol edin.",
        "cancel_ok": "Randevu basariyla iptal edildi.",
        "cancel_fail": "Iptal hatasi: {error}",
        "no_results": "Eslesen randevu bulunamadi.",
        "step": "Adim {current} / 5",
        "hairdresser_question": "Hangi kuaforu tercih edersiniz?",
        "next": "Ileri",
        "back": "Geri",
        "enter_name": "Lutfen adinizi girin.",
        "hello": "Merhaba {name}",
        "category": "Kategori",
        "service": "Hizmet",
        "hair_type": "Sac tipi",
        "duration": "Sure: {minutes} dakika",
        "price": "Fiyat: {price}",
        "slot_header": "Musait saatler",
        "slot_hint": "Secmek icin saate cift tiklayin, sonra 'Randevu al' butonuna basin.",
        "book_appt": "Randevu al",
        "book_more": "Yeni randevu al",
        "confirm_data": "Bilgileri onayla",
        "all_correct": "Tum bilgiler dogru mu?",
        "yes_book": "Evet, onayla",
        "no_back": "Hayir, geri",
        "saved": "Randevu kaydedildi",
        "mail_sent": "Onay e-postasi gonderildi!",
        "placeholder_name": "Ad Soyad",
        "flow_mode_question": "Nasil ilerleyelim?",
        "flow_on_site": "Yerinde randevu",
        "flow_callback": "Geri arama / E-posta",
        "manual_info": "Bu hizmet sabit saat olmadan manuel talep olarak islenir.",
        "wish": "Istek (kisa bir cumle yeterli)",
        "save_request": "Talebi kaydet",
        "invalid_email": "Lutfen gecerli bir e-posta girin.",
        "enter_phone": "Lutfen telefon numarasi girin.",
        "enter_wish": "Lutfen isteginizi girin.",
        "phone_number": "Telefon numarasi",
        "email_confirm": "E-posta (onay icin)",
        "select_date": "Tarih sec",
        "blocked_day": "Bu gunde randevu mumkun degil (Pazar veya tatil).",
        "chosen_slot": "Secilen randevu: {datum} saat {uhrzeit}",
        "no_slots": "Bu gun bos randevu yok.",
        "pick_slot_first": "Lutfen once bir saat secin.",
        "field_name": "Ad Soyad",
        "field_service": "Hizmet",
        "field_duration": "Sure",
        "field_date": "Tarih",
        "field_time": "Saat",
        "field_price": "Fiyat",
        "field_phone": "Telefon",
        "field_email": "E-posta",
        "field_wish": "Istek",
        "minutes_short": "{minutes} dakika",
        "slot_taken": "Bu saat az once baskasi tarafindan alindi. Lutfen baska bir saat secin.",
        "daily_limit": "Bu e-posta adresi bu gun icin zaten birden fazla randevu almis.",
        "save_error": "Kaydetme hatasi: {error}",
        "register_spinner": "Randevu sisteme kaydediliyor...",
        "calendar_delete_required": "Randevu Google Takvim'den silinemedi. Lutfen daha sonra tekrar deneyin.",
        "calendar_missing_column": "Supabase'de google_event_id kolonu yok. Takvim silme icin yedek arama kullanilacak.",
        "calendar_event_save_fail": "google_event_id kaydedilemedi: {error}",
        "cancel_code_show": "Iptal etmek isterseniz bu kodu kullanin:",
        "mail_status_info": "E-posta gonderimi calistiysa onay mesaji gonderildi.",
        "email_send_error": "E-posta gonderim hatasi: {error}",
        "mail_request_subject": "Talep onayi",
        "mail_booking_subject": "Randevu onayi",
        "mail_request_body": "Merhaba {name},\ntalebiniz bize ulasti. En kisa surede size donus yapacagiz.",
        "mail_booking_body": "Sayin {name},\n\nrandevu olusturdugunuz icin tesekkur ederiz. Randevu bilgileriniz asagidadir:\n\nAd Soyad: {name}\nHizmet: {service}\nKuafor: {friseur}\nTarih: {datum}\nSaat: {uhrzeit}\nSure: {dauer} dakika\nTelefon: {telefon}\nE-posta: {email}\n\nIptal kodu: {stornocode}\nLutfen bu kodu saklayin. Iptal icin gereklidir.\n\nAdres:\nWarfer Landstrasse 13\n\nSizi gormeyi bekliyoruz.\n\nFriseur Team",
        "google_api_missing": "Google Calendar API kurulu degil. Su komutu kullanin: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client",
        "google_auth_error": "Google Calendar yetkilendirme hatasi: {error}",
        "google_calendar_id_missing": "GOOGLE_CALENDAR_ID_ANDRE secrets icinde yok. Randevu simdilik Elias takvimine kaydedilecek.",
        "google_error": "Google Calendar hatasi: {error}",
        "calendar_id_resolve_fail": "Takvim ID belirlenemedi: {error}",
        "google_delete_eventid_error": "Google Calendar silme hatasi (event ID): {error}",
        "calendar_time_invalid": "Google Takvim silme icin randevu saati gecersiz.",
        "calendar_no_match": "Eslesen Google Takvim kaydi bulunamadi. Randevu yine de veritabanindan silinecek.",
        "google_delete_error": "Google Calendar silme hatasi: {error}",
    },
}


def t(key, **kwargs):
    lang = st.session_state.get("lang", "de")
    text = I18N.get(lang, {}).get(key, I18N["de"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text

st.set_page_config(page_title="Termin buchen", page_icon="💈", layout="wide")

if "lang" not in st.session_state:
    st.session_state.lang = "de"
if "show_main_menu" not in st.session_state:
    st.session_state.show_main_menu = False

header_left, header_right = st.columns([7, 3])
with header_left:
    st.title(t("title"))
    st.caption(t("caption"))
with header_right:
    aktuelle_sprache = st.session_state.get("lang", "de")
    ausgewaehlte_sprache = st.selectbox(
        t("lang_label"),
        options=list(SPRACHEN.keys()),
        index=list(SPRACHEN.values()).index(aktuelle_sprache),
        label_visibility="collapsed",
        key="language_picker_top",
    )
    neue_sprache = SPRACHEN[ausgewaehlte_sprache]
    if neue_sprache != aktuelle_sprache:
        st.session_state.lang = neue_sprache
        st.rerun()
st.markdown("---")
st.info(t("opening"))

termine, belegte_slots = laden()
pruefe_und_sende_erinnerungen(termine)

if "step" not in st.session_state:
    st.session_state.step = 1
if "name" not in st.session_state:
    st.session_state.name = ""
if "kategorie" not in st.session_state:
    st.session_state.kategorie = ""
if "friseur" not in st.session_state:
     st.session_state.friseur = ""
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
if "email_und_kalender_erledigt" not in st.session_state:
    st.session_state.email_und_kalender_erledigt = False
if "storno_treffer" not in st.session_state:
    st.session_state.storno_treffer = []
if "storno_auswahl_id" not in st.session_state:
    st.session_state.storno_auswahl_id = None
if "show_urlaubstage_admin" not in st.session_state:
    st.session_state.show_urlaubstage_admin = False

render_storno_sidebar(termine)

if st.session_state.step == 1:
    st.write(f"**{t('step', current=st.session_state.step)}**")
    friseur = st.radio(
        t("hairdresser_question"),
        ["Elias", "Andre"],
        index=0 if st.session_state.friseur == "Elias" else (1 if st.session_state.friseur == "Andre" else 0),
    )
    name = st.text_input(t("name"), value=st.session_state.name, placeholder=t("placeholder_name"))

    col1, col2 = st.columns(2)
    with col2:
        if st.button(t("next")):
            if not name.strip():
                st.error(t("enter_name"))
            else:
                st.session_state.friseur = friseur
                st.session_state.name = name.strip()
                st.session_state.step = 2
                st.rerun()

elif st.session_state.step == 2:
    st.write(f"**{t('step', current=st.session_state.step)}**")
    st.write(f"{t('hello', name='')} **{st.session_state.name}**")

    kategorien_liste = list(KATEGORIEN.keys())
    kat_index = kategorien_liste.index(st.session_state.kategorie) if st.session_state.kategorie in kategorien_liste else 0
    kategorie = st.selectbox(t("category"), kategorien_liste, index=kat_index)

    services = KATEGORIEN[kategorie]
    srv_index = services.index(st.session_state.service) if st.session_state.service in services else 0
    service = st.selectbox(t("service"), services, index=srv_index)

    # Haartyp-Auswahl für Services, die mit "Haare" beginnen
    haartyp = None
    if service.startswith("Haare"):
        haartyp = st.radio(t("hair_type"), ["Kurzhaar", "Langhaar"], index=0 if st.session_state.haartyp == "Kurzhaar" else 1)

    # Dauer und Preis anzeigen
    if service in DAUER_MIN:
        basis_dauer = DAUER_MIN[service]
        zusatz = 15 if haartyp == "Langhaar" else 0
        st.caption(t("duration", minutes=basis_dauer + zusatz))

        if service in PREISE:
            preis = PREISE[service]
            if isinstance(preis, dict):
                preis = preis.get(haartyp, "ab XX €")
            st.caption(t("price", price=preis))

    col1, col2 = st.columns(2)
    with col1:
        if st.button(t("back")):
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button(t("next")):
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
    st.write(f"**{t('step', current=st.session_state.step)}**")
    st.write(f"**{t('service')}:** {service}")

    if service.startswith("Anderes -"):
        modus = "manual"
    elif "Beratung" in service or "Extrawunsch" in service:
        art = st.radio(t("flow_mode_question"), [t("flow_on_site"), t("flow_callback")])
        modus = "manual" if art == t("flow_callback") else "standard"
    else:
        modus = "standard"

    if modus == "manual":
        st.info(t("manual_info"))

        email = st.text_input(t("email"))
        telefon = st.text_input(t("phone"))
        wunsch = st.text_area(t("wish"))

        col1, col2 = st.columns(2)
        with col1:
            if st.button(t("back")):
                reset_formular()
                st.session_state.step = 2
                st.rerun()

        with col2:
            if st.button(t("save_request")):
                if not email_ok(email):
                    st.error(t("invalid_email"))
                elif not telefon.strip():
                    st.error(t("enter_phone"))
                elif not wunsch.strip():
                    st.error(t("enter_wish"))
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

        telefon = st.text_input(t("phone_number"))
        email = st.text_input(t("email_confirm"))
        datum = st.date_input(t("select_date"), key="slot_datum_widget", min_value=date.today())
        datum_str = datum.strftime("%d.%m.%Y")

        if ist_tag_blockiert(datum_str, st.session_state.friseur):
            st.error(t("blocked_day"))
            st.stop()

        if datum_str != st.session_state.gewaehltes_datum:
            st.session_state.gewaehlte_uhrzeit = None
            st.session_state.gewaehltes_datum = datum_str

        belegte_slots_friseur = belegte_slots.get(st.session_state.friseur, set())
        freie = freie_termine(datum_str, dauer, belegte_slots_friseur, st.session_state.friseur)

        from datetime import datetime

        from datetime import datetime

        if freie:
            st.subheader(t("slot_header"))
            st.info(t("slot_hint"))

            freie = sorted(freie, key=lambda x: datetime.strptime(x, "%H:%M"))

            if st.session_state.gewaehlte_uhrzeit and st.session_state.gewaehltes_datum == datum_str:
                st.success(
                    t("chosen_slot", datum=st.session_state.gewaehltes_datum, uhrzeit=st.session_state.gewaehlte_uhrzeit)
                )

            for slot in freie:
                if st.button(f"🕒 {slot}", use_container_width=True):
                    st.session_state.gewaehlte_uhrzeit = slot
                    st.session_state.gewaehltes_datum = datum_str
        else:
            st.warning(t("no_slots"))
        col1, col2 = st.columns(2)
        with col1:
            if st.button(t("back")):
                reset_formular()
                st.session_state.step = 2
                st.session_state.gebucht = False
                st.rerun()

        with col2:
            if st.button(t("book_appt")):
                if not telefon.strip():
                    st.error(t("enter_phone"))
                elif not email_ok(email):
                    st.error(t("invalid_email"))
                elif not st.session_state.gewaehltes_datum or not st.session_state.gewaehlte_uhrzeit:
                    st.error(t("pick_slot_first"))
                else:
                    st.session_state.telefon = telefon.strip()
                    st.session_state.email = email.strip()
                    st.session_state.dauer = dauer
                    st.session_state.modus = "standard"
                    st.session_state.step = 4
                    st.rerun()
elif st.session_state.step == 4:
    st.write(f"**{t('step', current=4)}**")
    st.subheader(t("confirm_data"))

    modus = st.session_state.modus
    service = st.session_state.service
    if modus == "standard":
        datum_final = st.session_state.gewaehltes_datum
        uhrzeit_final = st.session_state.gewaehlte_uhrzeit
        telefon_val = st.session_state.telefon
        email_val = st.session_state.email
        buchung = {
            "modus": "standard",
            "friseur": st.session_state.friseur,
            "name": st.session_state.name,
            "telefon": telefon_val,
            "service": service,
            "datum": datum_final,
            "uhrzeit": uhrzeit_final,
            "termindauer": st.session_state.dauer,
            "email": email_val,
            "preis": preis_fuer_service(service, st.session_state.haartyp),
        }
    else:
        telefon_val = st.session_state.telefon
        email_val = st.session_state.email
        wunsch_val = st.session_state.wunsch
        buchung = {
            "modus": "manual",
            "friseur": st.session_state.friseur,
            "name": st.session_state.name,
            "telefon": telefon_val,
            "service": service,
            "email": email_val,
            "wunsch": wunsch_val,
        }

    if buchung.get("modus") == "standard":
        st.write(f"**{t('field_name')}:**", buchung.get("name", "-"))
        st.write(f"**{t('field_service')}:**", buchung.get("service", "-"))
        if buchung.get("preis"):
            st.write(f"**{t('field_price')}:**", buchung.get("preis"))
        st.write(f"**{t('field_duration')}:**", t("minutes_short", minutes=buchung.get("termindauer", "-")))
        st.write(f"**{t('field_date')}:**", buchung.get("datum", "-"))
        st.write(f"**{t('field_time')}:**", buchung.get("uhrzeit", "-"))
        st.write(f"**{t('field_phone')}:**", buchung.get("telefon", "-"))
        st.write(f"**{t('field_email')}:**", buchung.get("email", "-"))
    elif buchung.get("modus") == "manual":
        st.write(f"**{t('field_name')}:**", buchung.get("name", "-"))
        st.write(f"**{t('field_service')}:**", buchung.get("service", "-"))
        st.write(f"**{t('field_wish')}:**", buchung.get("wunsch", "-"))
        st.write(f"**{t('field_phone')}:**", buchung.get("telefon", "-"))
        st.write(f"**{t('field_email')}:**", buchung.get("email", "-"))

    st.write(f"**{t('all_correct')}**")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(t("yes_book")):
            if buchung.get("modus") == "standard":
                termine_aktuell, belegte_slots_aktuell = laden()
                slots_liste = slots_fuer_termin(datum_final, uhrzeit_final, st.session_state.dauer)

                belegte_slots_friseur = belegte_slots_aktuell.get(st.session_state.friseur, set())
                if any(slot in belegte_slots_friseur for slot in slots_liste):
                    st.error(t("slot_taken"))
                    st.stop()

                count = buchungen_pro_tag(termine_aktuell, email_val, datum_final)
                if count >= 4:
                    st.error(t("daily_limit"))
                    st.stop()

                buchung["stornocode"] = generiere_stornocode()

            st.session_state.letzte_buchung = buchung
            st.session_state.email_und_kalender_erledigt = False

            try:
                speicher_result = speichern(
                    st.session_state.letzte_buchung,
                    ist_anfrage=(buchung.get("modus") == "manual"),
                )
                if buchung.get("modus") == "standard":
                    gespeicherte = (speicher_result.data or [])
                    if gespeicherte:
                        st.session_state.letzte_buchung["id"] = gespeicherte[0].get("id")
            except Exception as exc:
                st.error(t("save_error", error=exc))
                st.stop()

            st.session_state.step = 5
            st.rerun()

    with col2:
        if st.button(t("no_back")):
            reset_formular()
            st.session_state.step = 3
            st.rerun()
elif st.session_state.step == 5:
    st.success(t("saved"))

    buchung = st.session_state.letzte_buchung or {}

    if not st.session_state.email_und_kalender_erledigt:
        with st.spinner(t("register_spinner")):
            sende_buchungs_email(
                st.session_state.letzte_buchung,
                ist_anfrage=(buchung.get("modus") == "manual")
            )
            if buchung.get("modus") == "standard":
                google_event_id = termin_zu_google_calendar(st.session_state.letzte_buchung)
                if google_event_id:
                    st.session_state.letzte_buchung["google_event_id"] = google_event_id
                    termin_id = st.session_state.letzte_buchung.get("id")
                    if termin_id:
                        try:
                            supabase.table("termine").update(
                                {"google_event_id": google_event_id}
                            ).eq("id", termin_id).execute()
                        except Exception as exc:
                            if "google_event_id" in str(exc).lower() and "column" in str(exc).lower():
                                st.warning(t("calendar_missing_column"))
                            else:
                                st.warning(t("calendar_event_save_fail", error=exc))
        st.session_state.email_und_kalender_erledigt = True
        st.toast(t("mail_sent"))

    if buchung.get("modus") == "standard":
        st.write(f"**{t('field_name')}:**", buchung.get("name", "-"))
        st.write(f"**{t('field_service')}:**", buchung.get("service", "-"))
        if buchung.get("preis"):
            st.write(f"**{t('field_price')}:**", buchung.get("preis"))
        st.write(f"**{t('field_duration')}:**", t("minutes_short", minutes=buchung.get("termindauer", "-")))
        st.write(f"**{t('field_date')}:**", buchung.get("datum", "-"))
        st.write(f"**{t('field_time')}:**", buchung.get("uhrzeit", "-"))
        st.write(f"**{t('field_phone')}:**", buchung.get("telefon", "-"))
        st.write(f"**{t('field_email')}:**", buchung.get("email", "-"))
        st.write(f"**{t('cancel_code_show')}**", buchung.get("stornocode", "-"))
    elif buchung.get("modus") == "manual":
        st.write(f"**{t('field_name')}:**", buchung.get("name", "-"))
        st.write(f"**{t('field_service')}:**", buchung.get("service", "-"))
        st.write(f"**{t('field_wish')}:**", buchung.get("wunsch", "-"))
        st.write(f"**{t('field_phone')}:**", buchung.get("telefon", "-"))
        st.write(f"**{t('field_email')}:**", buchung.get("email", "-"))

    st.info(t("mail_status_info"))

    if st.button(t("book_more")):
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

    if st.button("Urlaubstage", key="admin_urlaubstage_toggle"):
        st.session_state.show_urlaubstage_admin = not st.session_state.show_urlaubstage_admin

    if st.session_state.show_urlaubstage_admin:
        st.subheader("Urlaubstage verwalten")
        friseur_urlaub = st.selectbox(
            "Welcher Friseur?",
            ["Elias", "Andre"],
            key="admin_urlaub_friseur",
        )
        urlaub_datum = st.date_input(
            "Urlaubstag auswählen",
            value=date.today(),
            min_value=date.today(),
            key="admin_urlaub_datum",
        )

        if st.button("Urlaubstag speichern", key="admin_urlaub_add"):
            datum_str = urlaub_datum.strftime("%d.%m.%Y")
            if datum_str in BLOCKIERTE_TAGE:
                st.info("Dieser Tag ist bereits global blockiert.")
            elif datum_str in URLAUBSTAGE.get(friseur_urlaub, set()):
                st.info("Dieser Urlaubstag ist bereits eingetragen.")
            else:
                URLAUBSTAGE.setdefault(friseur_urlaub, set()).add(datum_str)
                _speichere_urlaubstage(URLAUBSTAGE)
                st.success(f"Urlaubstag gespeichert: {datum_str} ({friseur_urlaub})")
                st.rerun()

        aktuelle_urlaubstage = sorted(URLAUBSTAGE.get(friseur_urlaub, set()))
        st.write(f"Eingetragene Urlaubstage für {friseur_urlaub}:")
        if aktuelle_urlaubstage:
            for tag in aktuelle_urlaubstage:
                cols_urlaub = st.columns([4, 1])
                cols_urlaub[0].write(tag)
                if cols_urlaub[1].button("Löschen", key=f"urlaub_del_{friseur_urlaub}_{tag}"):
                    URLAUBSTAGE.setdefault(friseur_urlaub, set()).discard(tag)
                    _speichere_urlaubstage(URLAUBSTAGE)
                    st.success(f"Urlaubstag gelöscht: {tag}")
                    st.rerun()
        else:
            st.info("Noch keine Urlaubstage eingetragen.")

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

        standard_elias = [t for t in standard if t.get("friseur", "Elias") == "Elias"]
        standard_andre = [t for t in standard if t.get("friseur", "Elias") == "Andre"]

        gewuenschte_spalten_standard = ['name', 'telefon', 'datum', 'uhrzeit', 'service', 'termindauer', 'email']
        gewuenschte_spalten_manual = ['name', 'telefon', 'service', 'wunsch', 'email']

        def render_standard_tabelle(titel, eintraege, key_prefix):
            st.subheader(titel)
            if not eintraege:
                st.info("Keine Terminbuchungen vorhanden.")
                return

            cols = st.columns([2, 2, 2, 2, 2, 2, 2, 1])
            cols[0].write("**Name**")
            cols[1].write("**Telefon**")
            cols[2].write("**Datum**")
            cols[3].write("**Uhrzeit**")
            cols[4].write("**Service**")
            cols[5].write("**Dauer**")
            cols[6].write("**E-Mail**")
            cols[7].write("**Löschen**")

            for termin in eintraege:
                cols[0].write(termin.get('name', '-'))
                cols[1].write(termin.get('telefon', '-'))
                cols[2].write(termin.get('datum', '-'))
                cols[3].write(termin.get('uhrzeit', '-'))
                cols[4].write(termin.get('service', '-'))
                cols[5].write(f"{termin.get('termindauer', '-')} Min")
                cols[6].write(termin.get('email', '-'))
                if cols[7].button("🗑️", key=f"del_std_{key_prefix}_{termin['id']}"):
                    if not loesche_termin_aus_google_calendar(termin):
                        st.error("Termin konnte nicht im Google Kalender gelöscht werden. Bitte später erneut versuchen.")
                        return
                    supabase.table("termine").delete().eq("id", termin["id"]).execute()
                    st.success("Termin gelöscht")
                    st.rerun()

        render_standard_tabelle("Terminbuchungen - Elias", standard_elias, "elias")
        render_standard_tabelle("Terminbuchungen - Andre", standard_andre, "andre")

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
