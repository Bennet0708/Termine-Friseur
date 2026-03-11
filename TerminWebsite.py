import streamlit as st
import json
from datetime import date, datetime, timedelta
import pandas as pd
import re

DATEI = "termine.json"

def laden():
    try:
        with open(DATEI, "r") as f:
            data = json.load(f)
            termine = data.get("termine", [])
            belegte_slots = set(data.get("belegte_slots", []))
            return termine, belegte_slots
    except:
        return [], set()

def speichern(termine, belegte_slots):
    with open(DATEI, "w") as f:
        data = {
            "termine": termine,
            "belegte_slots": list(belegte_slots)
        }
        json.dump(data, f, indent=4)

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

def freie_termine(datum, dauer, belegte_slots):

    jetzt = datetime.now()
    heute_str = jetzt.strftime("%d.%m.%Y")

    freie_startzeiten = []

    stunden = 8
    minuten = 30

    while True:
        startzeit = f"{stunden:02d}:{minuten:02d}"

        slot_dt = datetime.strptime(datum + " " + startzeit, "%d.%m.%Y %H:%M")

        if datum == heute_str:
            if slot_dt < jetzt + timedelta(minutes=60):
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
        
        kollidiert = False

        for s in slots:
            if s in belegte_slots:
                kollidiert = True
                break
        if not kollidiert:
            freie_startzeiten.append(startzeit)

        minuten += 15
        if minuten == 60:
            minuten = 0
            stunden+= 1
    return freie_startzeiten

def freie_morgen(dauer, belegte_slots):
    morgen = datetime.today() + timedelta(days=1)
    
    datum_morgen = morgen.strftime("%d.%m.%Y")
    
    freie = freie_termine(datum_morgen, dauer, belegte_slots)

    return datum_morgen, freie


dauer_min = {
    "Haare - Schneiden": 30,
    "Haare - Färben": 60,
    "Haare - Stylen": 30,
    "Haare - Beratung": 45,
    "Haare - Extrawunsch": 45,
    "Bart - Trimmen": 15,
    "Bart - Kontur": 15,
    "Bart - Beratung": 30,
    "Bart - Extrawunsch": 15,
}

kategorien = {
    "Haare": ["Haare - Schneiden", "Haare - Färben", "Haare - Stylen", "Haare - Beratung", "Haare - Extrawunsch"],
    "Bart": ["Bart - Trimmen", "Bart - Kontur", "Bart - Beratung", "Bart - Extrawunsch"],
    "Anderes": ["Anderes - Event/Hochzeit", "Anderes - Beratung", "Anderes - Extrawunsch"],
}

st.set_page_config(page_title="Terminbot", page_icon="📅")
st.title("📅 Termin buchen")
st.write("Ich bin für Sie da um ein Termin zu buchen. Folgen Sie einfach den Anweisungen.")
st.info("Öffnungszeiten: Mo-Fr 8:30-18:00 Uhr")

termine, belegte_slots = laden()

if "step" not in st.session_state: st.session_state.step = 1

if "name" not in st.session_state: st.session_state.name = ""

if "kategorie" not in st.session_state: st.session_state.kategorie = ""
   
if "service" not in st.session_state: st.session_state.service = ""

if "letzte_buchung" not in st.session_state: st.session_state.letzte_buchung = None

if "gebucht" not in st.session_state: st.session_state.gebucht = False

if "admin_versuche" not in st.session_state: st.session_state.admin_versuche = 0

if "is_admin" not in st.session_state: st.session_state.is_admin = False
    
if "gewaehlte_uhrzeit" not in st.session_state: st.session_state.gewaehlte_uhrzeit = None

if "gewaehltes_datum" not in st.session_state: st.session_state.gewaehltes_datum = None

if st.session_state.step == 1:
    st.write(f"**Schritt {st.session_state.step} von 3**")
    name = st.text_input("Name", value=st.session_state.name, placeholder="Vorname Nachname")

    col1, col2 = st.columns(2)
    with col2:
        if st.button("Weiter ▶️"):
            if not name.strip():
                st.error("Bitte Name eingeben.")
            else:
                st.session_state.name = name.strip()
                st.session_state.step = 2
                st.rerun()
    with st.sidebar:
        if st.button("🔒 Admin-Bereich"):
            st.session_state.admin_versuche = 0
            st.session_state.step = 99 
            st.rerun()

elif st.session_state.step == 2:
    st.write(f"**Schritt {st.session_state.step} von 3**")
    st.write(f"Hallo **{st.session_state.name}** 👋")
    
    kats = list(kategorien.keys())
    
    if st.session_state.kategorie in kats:
        kat_index = kats.index(st.session_state.kategorie)
    else:
        kat_index = 0
        
    kategorie = st.selectbox("Kategorie", kats, index=kat_index)

    services = kategorien[kategorie]
    
    if st.session_state.service in services:
        srv_index = services.index(st.session_state.service)
    else:
        srv_index = 0
        
    service = st.selectbox("Service", services, index=srv_index)
   
    col1, col2 = st.columns(2)
    with col1:
        if st.button("◀️ Zurück"):
            st.session_state.step = 1
            st.rerun()
    with col2:
        if st.button("Weiter ▶️"):
            st.session_state.kategorie = kategorie
            st.session_state.service = service
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
            
        if art == "Rückruf / E-Mail":
            modus = "manual"
        else:
            modus = "standard"
    else:
        modus = "standard" 

    if modus == "manual":
        st.info("Dieser Service läuft als **manuelle Anfrage** (kein fester Zeitslot).")

        email = st.text_input("E-Mail")
        telefon = st.text_input("Telefon")
        wunsch = st.text_area("Wunsch (1 Satz reicht)")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("◀️ Zurück"):
                st.session_state.step = 2
                st.rerun()

        with col2:
            if st.button("📩 Anfrage speichern"):
                if not email_ok(email):
                    st.error("Bitte korrekte E-Mail eingeben.")
                elif not wunsch.strip():
                    st.error("Bitte Wunsch eingeben.")
                elif not telefon.strip():
                    st.error("Bitte Telefonnummer angeben.")
                else:
                    anfrage = {
                        "Name": st.session_state.name,
                        "Service": service,
                        "E-Mail": email.strip(),
                        "Telefon": telefon.strip(),
                        "Wunsch": wunsch.strip()
                    }
                    termine.append(anfrage)
                    speichern(termine, belegte_slots)

                    st.session_state.letzte_buchung = {
                        "modus": "manual",
                        "Name": st.session_state.name,
                        "Telefon": telefon.strip(),
                        "Service": service,
                        "Email": email.strip(),
                        "Wunsch": wunsch.strip()
                        }
                    st.session_state.step = 4
                    st.rerun()

    else:
        dauer = dauer_min[service]

        telefon = st.text_input("Telefonnummer")

        datum = st.date_input("Datum auswählen", min_value=date.today())

        if datum:
            datum_str = datum.strftime("%d.%m.%Y")

            freie = freie_termine(datum_str, dauer, belegte_slots)

            st.subheader("Freie Uhrzeiten")

            if freie:

                cols = st.columns(4)

                for i in range(0, len(freie), 4):
                    row = freie[i:i+4]

                    for j, slot in enumerate(row):
                        with cols[j]:

                            if st.button(slot, key=f"slot_{slot}"):

                                st.session_state["gewaehlte_uhrzeit"] = slot
                                st.session_state["gewaehltes_datum"] = datum_str

                if st.session_state.get("gewaehlte_uhrzeit") and st.session_state.get("gewaehltes_datum"):
                    st.success(
                    f"Gewählter Termin: {st.session_state.gewaehltes_datum} um {st.session_state.gewaehlte_uhrzeit}"
                    )

        else:
            st.warning("An diesem Tag sind keine Termine mehr frei.")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("◀️ Zurück"):
                st.session_state.step = 2
                st.session_state.gebucht = False
                st.rerun()

        with col2:
            buchen = st.button("Termin buchen", disabled=st.session_state.gebucht)

            if buchen:
                st.session_state.gebucht = True

                datum = st.session_state.gewaehltes_datum
                uhrzeit = st.session_state.gewaehlte_uhrzeit 

                if not telefon.strip():
                    st.error("Bitte Telefonnummer angeben.")
                    st.session_state.gebucht = False
                    st.stop()

                slots_liste = slots_fuer_termin(datum, uhrzeit, dauer)


                count = sum(
                    1 for t in termine if t.get("Telefon") == telefon and t.get("Datum") == datum
                )

                if count >= 4:
                    st.error("Diese Telefonnummer hat bereits mehrere Termine an diesem Tag gebucht.")
                    st.session_state.gebucht = False
                    st.stop()

                termin = {
                    "Name": st.session_state.name,
                    "Telefon": telefon.strip(),
                    "Datum": datum,
                    "Uhrzeit": uhrzeit,
                    "Service": service,
                     "Termindauer": dauer
                } 

                termine.append(termin)

                for s in slots_liste:
                    belegte_slots.add(s)

                speichern(termine, belegte_slots)

                st.session_state.letzte_buchung = {
                    "modus" : "standard",
                    "Name": st.session_state.name,
                    "Telefon": telefon.strip(),
                    "Service": service,
                    "Datum": datum,
                    "Uhrzeit": uhrzeit,
                    "Termindauer": dauer
                    }

                st.session_state.step = 4
                st.rerun()

elif st.session_state.step == 4:
    st.success("Termin gebucht ✅")

    b = st.session_state.letzte_buchung or {}
    
    if b["modus"] == "standard":
        st.write("**Name:**", b.get("Name", "-"))
        st.write("**Telefon:**", b.get("Telefon", "-"))
        st.write("**Service:**", b.get("Service", "-"))
        st.write("**Datum:**", b.get("Datum", "-"))
        st.write("**Uhrzeit:**", b.get("Uhrzeit", "-"))
        st.write("**Dauer:**", f"{b.get('Termindauer', '-') } Minuten")

    elif b["modus"] == "manual":
        st.write("**Name:**", b.get("Name", "-"))
        st.write("**Telefon:**", b.get("Telefon", "-"))
        st.write("**Service:**", b.get("Service", "-"))
        st.write("**Wunsch:**", b.get("Wunsch", "-"))
        st.write("**E-Mail:**", b.get("Email", "-"))

    st.info("Bitte Screenshot machen oder Termin notieren.")

    if st.button("Noch einen Termin buchen"):
        st.session_state.step = 1
        st.session_state.gebucht = False
        st.session_state.letzte_buchung = None
        st.session_state.gewaehlte_uhrzeit = None
        st.session_state.gewaehltes_datum = None
        st.rerun()

    st.stop()


elif st.session_state.step == 5:
    if not st.session_state.is_admin:
        st.session_state.step = 99
        st.rerun()
    st.title("🔒 Admin Dashboard")
    if st.button("⬅️ Zurück"):
        st.session_state.step = 1
        st.rerun()

    termine, belegte_slots = laden()

    if not termine:
        st.info("Noch keine Termine gespeichert.")
    else:
        standard = []
        manual = []

        for t in termine:
            if "Datum" in t and "Uhrzeit" in t:
                dt = datetime.strptime(
                    t["Datum"] + " " + t["Uhrzeit"],
                    "%d.%m.%Y %H:%M"
                )
                t_copy = t.copy()
                t_copy["Sortierung"] = dt
                standard.append(t_copy)
            else:
                manual.append(t)

        standard.sort(key=lambda x: x["Sortierung"])

        for t in standard:
            del t["Sortierung"]

        st.subheader("📅 Terminbuchungen")
        st.dataframe(pd.DataFrame(standard), use_container_width=True)

        st.subheader("📩 Manuelle Anfragen")
        st.dataframe(pd.DataFrame(manual), use_container_width=True)

    optionen = [f"{i+1} - {t.get('Name','-')} - {t.get('Service','-')}" for i, t in enumerate(termine)]
    auswahl = st.selectbox("Eintrag auswählen zum Löschen", optionen)

    if st.button("🗑️ Löschen"):
        index = optionen.index(auswahl)
        t = termine.pop(index)

        if "Datum" in t:
            slots = slots_fuer_termin(t["Datum"], t["Uhrzeit"], t["Termindauer"])
            for s in slots:
                belegte_slots.discard(s)

        speichern(termine, belegte_slots)
        st.success("Eintrag gelöscht")
        st.rerun()


elif st.session_state.step == 99:
    st.title("🔒 Admin Login")

    passwort = st.text_input("Passwort eingeben", type="password")

    if st.button("Anmelden"):
        if passwort == "admin123":
            st.session_state.is_admin = True
            st.session_state.step = 5
            st.rerun()
        else:
            st.session_state.admin_versuche += 1
            st.error(f"Falsches Passwort. {3 - st.session_state.admin_versuche} Versuche übrig.")

            if st.session_state.admin_versuche >= 3:
                st.warning("Zu viele Fehlversuche.")
                st.session_state.step = 1
                st.rerun()

    if st.button("⬅️ Zurück"):
        st.session_state.step = 1
        st.rerun()
